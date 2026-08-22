"""Tests for audit log access control (P0-3 fix).

Pins the contract from design spec §6.3:
- List endpoint never returns `request_body`
- Detail endpoint defaults to no `request_body`
- `?include_body=true` is admin-only; non-admin gets 403
- Admin access to body writes a meta-audit row at path='/admin/audit-access'
- Pre-existing `request_body_preview` (first N chars) is returned in both
  list and detail responses
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models.audit import AuditLog
from app.models.user import User
from app.utils.crypto import encrypt_key


@pytest_asyncio.fixture
async def client(db_session):
    """HTTPX async client bound to the FastAPI app, with the DB dependency
    overridden to use the test's in-memory session.
    """

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[__import__("app.database", fromlist=["get_db"]).get_db] = (
        _override_get_db
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _override_user(target_user: User):
    """Build a dependency override that returns the given user from
    get_current_user (bypasses real auth — we test the route logic only).
    """

    async def _override():
        return target_user

    return _override


async def _make_log(test_user, body: str, *, encrypt: bool = True) -> AuditLog:
    """Insert a log row as `test_user` with a Fernet-encrypted body.

    By default the body is encrypted (so tests that check decryption work
    independently of the dev's env or CI's AUDIT_LOG_FULL_BODY setting).
    Pass ``encrypt=False`` to simulate the FULL_BODY=false policy where
    the body is stored as preview-only and request_body stays NULL.
    """
    from app.config import get_settings
    from app.models.audit import AuditLog

    settings = get_settings()
    encrypted = encrypt_key(body) if encrypt else None
    log = AuditLog(
        user_id=test_user.id,
        username=test_user.username,
        department=test_user.department.name,
        model="gpt-4",
        provider="openai",
        method="POST",
        path="/v1/chat/completions",
        request_body=encrypted,
        request_body_preview=body[: settings.AUDIT_LOG_PREVIEW_CHARS],
        is_stream=False,
        status="completed",
        status_code=200,
        request_tokens=5,
        response_tokens=3,
        total_tokens=8,
        latency_ms=120,
    )
    return log


@pytest.mark.asyncio
async def test_list_endpoint_never_returns_body(db_session, test_user, admin_user, client):
    """GET /api/audit/logs must never include request_body in items."""
    log = await _make_log(test_user, "secret prompt that should not leak in list view")
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)

    app.dependency_overrides[
        __import__("app.middleware.auth_middleware", fromlist=["get_current_user"]).get_current_user
    ] = await _override_user(admin_user)

    resp = await client.get("/api/audit/logs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert "request_body" not in item, "list must not include request_body"
    assert "response_body" not in item, "list must not include response_body"
    # But the preview should be there
    assert "request_body_preview" in item
    assert item["request_body_preview"].startswith("secret prompt")


@pytest.mark.asyncio
async def test_detail_endpoint_default_no_body(db_session, test_user, admin_user, client):
    """GET /api/audit/logs/{id} without ?include_body returns no body."""
    log = await _make_log(test_user, "the actual prompt content")
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)

    app.dependency_overrides[
        __import__("app.middleware.auth_middleware", fromlist=["get_current_user"]).get_current_user
    ] = await _override_user(admin_user)

    resp = await client.get(f"/api/audit/logs/{log.id}")
    assert resp.status_code == 200
    body = resp.json()
    # When include_body is omitted, schema allows request_body but it's null
    assert body.get("request_body") is None
    # Preview is present
    assert body.get("request_body_preview", "").startswith("the actual prompt")


@pytest.mark.asyncio
async def test_detail_include_body_non_admin_forbidden(db_session, test_user, client):
    """Non-admin with ?include_body=true gets 403."""
    log = await _make_log(test_user, "private content for admin eyes")
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)

    app.dependency_overrides[
        __import__("app.middleware.auth_middleware", fromlist=["get_current_user"]).get_current_user
    ] = (
        await _override_user(test_user)  # non-admin (regular user)
    )

    resp = await client.get(f"/api/audit/logs/{log.id}?include_body=true")
    assert resp.status_code == 403
    assert "管理员" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_detail_include_body_admin_returns_plaintext(
    db_session, test_user, admin_user, client
):
    """Admin with ?include_body=true gets decrypted body + writes meta-audit."""
    log = await _make_log(test_user, "decrypted secret message")
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)

    app.dependency_overrides[
        __import__("app.middleware.auth_middleware", fromlist=["get_current_user"]).get_current_user
    ] = await _override_user(admin_user)

    resp = await client.get(f"/api/audit/logs/{log.id}?include_body=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["request_body"] == "decrypted secret message"

    # Meta-audit row should now exist
    result = await db_session.execute(
        select(AuditLog).where(AuditLog.path == "/admin/audit-access")
    )
    meta_rows = result.scalars().all()
    assert len(meta_rows) == 1
    meta = meta_rows[0]
    assert meta.user_id == admin_user.id
    # Preview format: "viewed log=<first-8-hex> user=<first-8-hex> path=..."
    assert "viewed log=" in meta.request_body_preview
    assert str(log.id).split("-")[0] in meta.request_body_preview


@pytest.mark.asyncio
async def test_detail_include_body_admin_no_body_stored(db_session, test_user, admin_user, client):
    """If the log was created with FULL_BODY=false, request_body is NULL
    and include_body=true returns null (no decryption attempted)."""
    log = await _make_log(test_user, "preview only content, no encrypted body", encrypt=False)
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)
    assert log.request_body is None

    app.dependency_overrides[
        __import__("app.middleware.auth_middleware", fromlist=["get_current_user"]).get_current_user
    ] = await _override_user(admin_user)

    resp = await client.get(f"/api/audit/logs/{log.id}?include_body=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["request_body"] is None
    # Meta-audit still written (the access happened, even if nothing was revealed)
    result = await db_session.execute(
        select(AuditLog).where(AuditLog.path == "/admin/audit-access")
    )
    assert len(result.scalars().all()) == 1

@pytest.mark.asyncio
async def test_list_and_detail_return_error_message(db_session, test_user, admin_user, client):
    """P1-8 regression: failed rows must surface error_message through the API.

    The router projection (_to_list_item) originally omitted the field, so
    the schema default None leaked out and the audit page tooltip never
    fired - while the DB layer had it all along. This pins the serialization
    layer that the original P1-8 tests missed.
    """
    log = await _make_log(test_user, "prompt that failed upstream")
    log.status = "failed"
    log.status_code = 502
    log.error_message = 'upstream said: {"error":"bad gateway"}'
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)

    app.dependency_overrides[
        __import__("app.middleware.auth_middleware", fromlist=["get_current_user"]).get_current_user
    ] = await _override_user(admin_user)

    resp = await client.get("/api/audit/logs")
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["error_message"] == 'upstream said: {"error":"bad gateway"}'

    detail = await client.get(f"/api/audit/logs/{log.id}")
    assert detail.status_code == 200
    assert detail.json()["error_message"] == 'upstream said: {"error":"bad gateway"}'

