"""Unit tests for ``_resolve_credentials`` — the shared auth helper extracted
in P1-1 to dedupe ``get_current_user`` and ``get_auth_context``.

Covers:
- API key path (happy + invalid + expired + inactive key)
- API key path with agent_type attached
- JWT path (happy + invalid)
- Missing credentials
- Inactive user (403)
"""

from datetime import timedelta

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.middleware.auth_middleware import _resolve_credentials
from app.models.agent_type import AgentType
from app.models.api_key import APIKey
from app.utils.datetime_utils import utcnow
from app.utils.hashing import hash_api_key
from app.utils.security import create_access_token

# ---------- helpers ----------


def _bearer(token: str | None) -> HTTPAuthorizationCredentials | None:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token) if token else None


async def _make_api_key(db, user, agent_type: AgentType | None = None) -> str:
    """Insert an active APIKey for ``user`` and return the plaintext token."""
    full, prefix, key_hash = _gen_key()
    key = APIKey(
        user_id=user.id,
        name="test-key",
        key_hash=key_hash,
        key_prefix=prefix,
        agent_type_id=agent_type.id if agent_type else None,
        is_active=True,
    )
    db.add(key)
    await db.commit()
    return full


def _gen_key() -> tuple[str, str, str]:
    import secrets

    full = "gf_" + secrets.token_urlsafe(45)
    return full, full[:11], hash_api_key(full)


# ---------- API key path ----------


@pytest.mark.asyncio
async def test_resolve_credentials_api_key_happy_path(db_session, test_user):
    full = await _make_api_key(db_session, test_user)

    user, api_key_id, agent_type = await _resolve_credentials(_bearer(full), db_session)

    assert user.id == test_user.id
    assert api_key_id is not None
    assert agent_type is None  # no agent_type attached
    # Eagerly loaded relationships are usable
    assert user.role.name == "user"
    assert user.department.name == "工程部"


@pytest.mark.asyncio
async def test_resolve_credentials_api_key_with_agent_type(db_session, test_user):
    agent = AgentType(name="Claude Code")
    db_session.add(agent)
    await db_session.flush()

    full = await _make_api_key(db_session, test_user, agent_type=agent)

    user, api_key_id, agent_type = await _resolve_credentials(_bearer(full), db_session)

    assert api_key_id is not None
    assert agent_type == "Claude Code"


@pytest.mark.asyncio
async def test_resolve_credentials_api_key_invalid_raises_401(db_session):
    with pytest.raises(HTTPException) as exc:
        await _resolve_credentials(_bearer("gf_doesnotexist"), db_session)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_resolve_credentials_api_key_expired_raises_401(db_session, test_user):
    full, prefix, key_hash = _gen_key()
    db_session.add(
        APIKey(
            user_id=test_user.id,
            name="expired",
            key_hash=key_hash,
            key_prefix=prefix,
            expires_at=utcnow() - timedelta(days=1),
            is_active=True,
        )
    )
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await _resolve_credentials(_bearer(full), db_session)
    assert exc.value.status_code == 401
    assert "过期" in exc.value.detail


@pytest.mark.asyncio
async def test_resolve_credentials_api_key_inactive_raises_401(db_session, test_user):
    full, prefix, key_hash = _gen_key()
    db_session.add(
        APIKey(
            user_id=test_user.id,
            name="inactive",
            key_hash=key_hash,
            key_prefix=prefix,
            is_active=False,
        )
    )
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await _resolve_credentials(_bearer(full), db_session)
    assert exc.value.status_code == 401


# ---------- JWT path ----------


@pytest.mark.asyncio
async def test_resolve_credentials_jwt_happy_path(db_session, test_user):
    token = create_access_token(data={"sub": str(test_user.id)})

    user, api_key_id, agent_type = await _resolve_credentials(_bearer(token), db_session)

    assert user.id == test_user.id
    assert api_key_id is None
    assert agent_type is None


@pytest.mark.asyncio
async def test_resolve_credentials_jwt_invalid_raises_401(db_session):
    with pytest.raises(HTTPException) as exc:
        await _resolve_credentials(_bearer("not.a.valid.jwt"), db_session)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_resolve_credentials_jwt_missing_sub_raises_401(db_session):
    token = create_access_token(data={"not_sub": "x"})

    with pytest.raises(HTTPException) as exc:
        await _resolve_credentials(_bearer(token), db_session)
    assert exc.value.status_code == 401


# ---------- shared error paths ----------


@pytest.mark.asyncio
async def test_resolve_credentials_missing_credentials_raises_401(db_session):
    with pytest.raises(HTTPException) as exc:
        await _resolve_credentials(None, db_session)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_resolve_credentials_inactive_user_raises_403(db_session, test_user):
    test_user.is_active = False
    await db_session.commit()

    token = create_access_token(data={"sub": str(test_user.id)})
    with pytest.raises(HTTPException) as exc:
        await _resolve_credentials(_bearer(token), db_session)
    assert exc.value.status_code == 403
    assert "禁用" in exc.value.detail

# ---------- P1-3: last_used_at off the hot path ----------


@pytest.mark.asyncio
async def test_auth_marks_buffer_not_db(db_session, test_user):
    """API key auth records the usage in memory only - no immediate
    UPDATE+commit on the hot path."""
    from app.middleware import auth_middleware

    auth_middleware._last_used_buffer.clear()
    full = await _make_api_key(db_session, test_user)

    user, api_key_id, _ = await _resolve_credentials(_bearer(full), db_session)

    assert api_key_id in auth_middleware._last_used_buffer
    # DB row was NOT touched by auth itself
    key_row = await db_session.get(APIKey, api_key_id)
    await db_session.refresh(key_row)
    assert key_row.last_used_at is None

    auth_middleware._last_used_buffer.clear()


@pytest.mark.asyncio
async def test_flush_updates_last_used_in_batch(db_session, test_user):
    """flush_last_used_buffer batch-updates all buffered ids and drains."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.middleware import auth_middleware

    auth_middleware._last_used_buffer.clear()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    full1 = await _make_api_key(db_session, test_user)
    full2 = await _make_api_key(db_session, test_user)
    await _resolve_credentials(_bearer(full1), db_session)
    await _resolve_credentials(_bearer(full2), db_session)
    assert len(auth_middleware._last_used_buffer) == 2

    count = await auth_middleware.flush_last_used_buffer(session_factory=factory)

    assert count == 2
    assert not auth_middleware._last_used_buffer

    from sqlalchemy import select

    result = await db_session.execute(select(APIKey))
    for row in result.scalars():
        assert row.last_used_at is not None

    auth_middleware._last_used_buffer.clear()


@pytest.mark.asyncio
async def test_flush_empty_buffer_is_noop():
    from app.middleware import auth_middleware

    auth_middleware._last_used_buffer.clear()
    count = await auth_middleware.flush_last_used_buffer(
        session_factory=_failing_factory_should_not_be_called()
    )
    assert count == 0


def _failing_factory_should_not_be_called():
    def factory():
        raise AssertionError("factory must not be called for an empty buffer")

    return factory


@pytest.mark.asyncio
async def test_flush_failure_keeps_buffer_for_retry(db_session, test_user):
    """A failed flush leaves the buffer intact so the next cycle retries."""
    from app.middleware import auth_middleware

    auth_middleware._last_used_buffer.clear()
    full = await _make_api_key(db_session, test_user)
    await _resolve_credentials(_bearer(full), db_session)
    assert len(auth_middleware._last_used_buffer) == 1

    def broken_factory():
        raise RuntimeError("db down")

    with pytest.raises(RuntimeError):
        await auth_middleware.flush_last_used_buffer(session_factory=broken_factory)

    assert len(auth_middleware._last_used_buffer) == 1
    auth_middleware._last_used_buffer.clear()
