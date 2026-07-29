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
