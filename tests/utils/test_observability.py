"""Observability tests (P2-8): /metrics endpoint + business counters + JSON logs.

Counter assertions use before/after deltas on unique label values (the
global REGISTRY accumulates across tests; absolute values are not stable).
"""

import json
import logging
from io import StringIO

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.audit_service import AuditService
from app.services.cleanup_service import mark_stale_pending_logs
from app.utils.logging_config import RequestIdFilter
from app.utils.metrics import AUDIT_WRITES, LLM_CALLS, LLM_LATENCY
from app.utils.request_id import current_request_id

# ---------- /metrics endpoint ----------


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session

    from app.database import get_db

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_metrics_endpoint_exposed(client):
    """GET /metrics returns Prometheus text format with both HTTP and
    business metrics."""
    resp = await client.get("/metrics")

    assert resp.status_code == 200
    body = resp.text
    # Business metrics (P2-8) are registered and documented
    assert "gateflow_llm_call_total" in body
    assert "gateflow_llm_latency_seconds" in body
    assert "gateflow_audit_log_write_total" in body
    # HTTP metrics from the instrumentator (route-template normalized)
    assert "http_request" in body
    # Prometheus exposition format markers
    assert "# HELP" in body and "# TYPE" in body


# ---------- business counters ----------


@pytest.mark.asyncio
async def test_record_completion_increments_counters(db_session, test_user):
    """A finalized call bumps llm_call_total and audit_log_write_total."""
    model = "p2-8-model-ok"
    provider = "p2-8-provider"
    before_calls = LLM_CALLS.labels(model=model, provider=provider, status="completed")._value.get()
    before_writes = AUDIT_WRITES.labels(status="completed")._value.get()

    service = AuditService(db_session)
    log = await service.create_pending_log(
        user=test_user,
        model=model,
        provider=provider,
        path="/v1/chat/completions",
        request_body=None,
        is_stream=False,
    )
    await db_session.commit()
    await service.record_completion(log, status_code=200, latency_ms=123)
    await db_session.commit()

    assert (
        LLM_CALLS.labels(model=model, provider=provider, status="completed")._value.get()
        == before_calls + 1
    )
    assert AUDIT_WRITES.labels(status="completed")._value.get() == before_writes + 1


@pytest.mark.asyncio
async def test_failed_call_counted_separately(db_session, test_user):
    """Failures land in the status='failed' series with the same labels."""
    model = "p2-8-model-fail"
    provider = "p2-8-provider"
    before = LLM_CALLS.labels(model=model, provider=provider, status="failed")._value.get()

    service = AuditService(db_session)
    log = await service.create_pending_log(
        user=test_user,
        model=model,
        provider=provider,
        path="/v1/chat/completions",
        request_body=None,
        is_stream=False,
    )
    await db_session.commit()
    await service.record_completion(
        log, status_code=500, latency_ms=10, error_message="boom"
    )
    await db_session.commit()

    assert (
        LLM_CALLS.labels(model=model, provider=provider, status="failed")._value.get()
        == before + 1
    )


@pytest.mark.asyncio
async def test_latency_histogram_observed(db_session, test_user):
    """latency_ms lands in the latency histogram (converted to seconds)."""
    model = "p2-8-model-latency"
    provider = "p2-8-provider"
    before = LLM_LATENCY.labels(model=model, provider=provider)._sum.get()

    service = AuditService(db_session)
    log = await service.create_pending_log(
        user=test_user,
        model=model,
        provider=provider,
        path="/x",
        request_body=None,
        is_stream=False,
    )
    await db_session.commit()
    await service.record_completion(log, status_code=200, latency_ms=1500)
    await db_session.commit()

    assert LLM_LATENCY.labels(model=model, provider=provider)._sum.get() == before + 1.5


@pytest.mark.asyncio
async def test_stale_cleanup_counted(db_session, test_user):
    """Rows marked stale by the P2-6 cleanup bump audit_log_write_total{status='stale'}."""
    from datetime import timedelta

    from app.utils.datetime_utils import utcnow

    before = AUDIT_WRITES.labels(status="stale")._value.get()

    service = AuditService(db_session)
    for _ in range(2):
        log = await service.create_pending_log(
            user=test_user,
            model="p2-8-model-stale",
            provider="p",
            path="/x",
            request_body=None,
            is_stream=True,
        )
        log.created_at = utcnow() - timedelta(hours=2)
    await db_session.commit()

    count = await mark_stale_pending_logs(db_session, older_than_seconds=3600)

    assert count == 2
    assert AUDIT_WRITES.labels(status="stale")._value.get() == before + 2


# ---------- structured logging ----------


def _make_record(msg: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="app.test", level=logging.INFO, pathname=__file__, lineno=1, msg=msg, args=None, exc_info=None
    )


def test_request_id_filter_injects_contextvar_value():
    """RequestIdFilter copies the ContextVar onto every record."""
    token = current_request_id.set("req-abc-123")
    try:
        rec = _make_record("hello")
        assert RequestIdFilter().filter(rec) is True
        assert rec.request_id == "req-abc-123"
    finally:
        current_request_id.reset(token)


def test_request_id_filter_defaults_to_dash_outside_request():
    rec = _make_record("no request scope")
    RequestIdFilter().filter(rec)
    assert rec.request_id == "-"


def test_json_formatter_outputs_request_id():
    """The P2-8 JSON layout emits one-line JSON with request_id."""
    try:
        from pythonjsonlogger.json import JsonFormatter
    except ImportError:
        from pythonjsonlogger.jsonlogger import JsonFormatter

    formatter = JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(request_id)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
    )
    token = current_request_id.set("req-json-42")
    try:
        rec = _make_record("structured hello")
        RequestIdFilter().filter(rec)
        stream = StringIO()
        stream.write(formatter.format(rec))
        payload = json.loads(stream.getvalue())
        assert payload["request_id"] == "req-json-42"
        assert payload["message"] == "structured hello"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "app.test"
    finally:
        current_request_id.reset(token)
