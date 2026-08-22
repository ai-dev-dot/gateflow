"""Tests for GatewayService non-streaming error paths (P1-8).

Covers:
- Upstream non-200 response body lands in audit_logs.error_message
- Unexpected exception repr lands in audit_logs.error_message (status 500)
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.provider_key import ProviderAPIKey
from app.services.audit_service import AuditService
from app.services.gateway_service import GatewayService
from app.services.provider_adapters import OpenAIAdapter
from app.utils.crypto import encrypt_key, key_preview


async def _make_audit_and_key(db_session, test_user):
    audit_svc = AuditService(db_session)
    audit_log = await audit_svc.create_pending_log(
        user=test_user,
        model="gpt-4",
        provider="openai",
        path="/v1/chat/completions",
        request_body=None,
        is_stream=False,
    )
    await db_session.commit()
    await db_session.refresh(audit_log)

    pk = ProviderAPIKey(
        provider="openai",
        encrypted_key=encrypt_key("sk-test"),
        key_preview=key_preview("sk-test"),
        name="test-key",
        is_active=True,
    )
    db_session.add(pk)
    await db_session.commit()
    await db_session.refresh(pk)
    return audit_log, pk


def _session_factory(db_session):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    engine = db_session.bind
    return async_sessionmaker(engine, expire_on_commit=False)


class FakeNonStreamClient:
    """Fake http client with a controllable post() outcome."""

    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc

    async def post(self, url, **kwargs):
        if self._exc is not None:
            raise self._exc
        return self._response


class FakeResponse:
    def __init__(self, status_code, text, json_data=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data if json_data is not None else {"error": {"message": text}}

    def json(self):
        return self._json


@pytest.mark.asyncio
async def test_non_stream_upstream_error_persists_body(db_session, test_user):
    """P1-8: upstream non-200 error body is persisted to error_message."""
    audit_log, pk = await _make_audit_and_key(db_session, test_user)
    factory = _session_factory(db_session)

    service = GatewayService(db_session, OpenAIAdapter())
    fake_client = FakeNonStreamClient(
        response=FakeResponse(429, "Rate limit exceeded")
    )

    with (
        patch(
            "app.services.gateway_service.get_http_client",
            AsyncMock(return_value=fake_client),
        ),
        patch("app.services.gateway_service.async_session", factory),
    ):
        result = await service._handle_non_stream(
            upstream_url="https://api.openai.com/v1/chat/completions",
            upstream_headers={"Authorization": "Bearer sk-test"},
            forward_body={"model": "gpt-4", "messages": []},
            audit_log=audit_log,
            provider_key_id=pk.id,
            request_tokens=10,
        )

    assert result.status_code == 429
    async with factory() as verify:
        row = (
            await verify.execute(select(AuditLog).where(AuditLog.id == audit_log.id))
        ).scalar_one()
        assert row.status == "failed"
        assert row.status_code == 429
        assert row.error_message == "Rate limit exceeded"


@pytest.mark.asyncio
async def test_non_stream_exception_persists_repr(db_session, test_user):
    """P1-8: unexpected exception repr is persisted to error_message (500)."""
    audit_log, pk = await _make_audit_and_key(db_session, test_user)
    factory = _session_factory(db_session)

    service = GatewayService(db_session, OpenAIAdapter())
    boom = ValueError("bad upstream payload")
    fake_client = FakeNonStreamClient(exc=boom)

    with (
        patch(
            "app.services.gateway_service.get_http_client",
            AsyncMock(return_value=fake_client),
        ),
        patch("app.services.gateway_service.async_session", factory),
    ):
        result = await service._handle_non_stream(
            upstream_url="https://api.openai.com/v1/chat/completions",
            upstream_headers={"Authorization": "Bearer sk-test"},
            forward_body={"model": "gpt-4", "messages": []},
            audit_log=audit_log,
            provider_key_id=pk.id,
            request_tokens=10,
        )

    assert result.status_code == 500
    async with factory() as verify:
        row = (
            await verify.execute(select(AuditLog).where(AuditLog.id == audit_log.id))
        ).scalar_one()
        assert row.status == "failed"
        assert row.status_code == 500
        assert row.error_message is not None
        assert "ValueError" in row.error_message
        assert "bad upstream payload" in row.error_message
