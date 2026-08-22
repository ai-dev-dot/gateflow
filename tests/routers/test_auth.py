"""Auth router HTTP tests (P2-1): login / refresh / password change."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.user import Role, User
from app.utils.security import get_password_hash, verify_password


async def _make_authed_user(db_session, username="bob", password="s3cret!"):
    """Create a user with a real bcrypt hash + eager-loaded role/dept."""
    role = Role(name="user", permissions={})
    db_session.add(role)
    await db_session.flush()
    user = User(
        username=username,
        email=f"{username}@test.local",
        hashed_password=get_password_hash(password),
        role_id=role.id,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    result = await db_session.execute(
        select(User)
        .where(User.id == user.id)
        .options(selectinload(User.role), selectinload(User.department))
    )
    return result.scalar_one()


@pytest.mark.asyncio
async def test_login_success_returns_token(db_session, client):
    await _make_authed_user(db_session)
    resp = await client.post("/api/auth/login", json={"username": "bob", "password": "s3cret!"})
    assert resp.status_code == 200
    assert resp.json().get("access_token")


@pytest.mark.asyncio
async def test_login_wrong_password_401(db_session, client):
    await _make_authed_user(db_session)
    resp = await client.post("/api/auth/login", json={"username": "bob", "password": "nope"})
    assert resp.status_code == 401
    assert "密码" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_refresh_without_auth_401(client):
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_returns_new_token(db_session, client, as_user):
    bob = await _make_authed_user(db_session)
    as_user(bob)
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 200
    assert resp.json().get("access_token")


@pytest.mark.asyncio
async def test_change_password_wrong_old_400(db_session, client, as_user):
    bob = await _make_authed_user(db_session)
    as_user(bob)
    resp = await client.put(
        "/api/auth/password",
        json={"old_password": "wrong", "new_password": "newpass1"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_change_password_success_persists_new_hash(db_session, client, as_user):
    """Change-password persists the new hash; old hash no longer verifies.

    Verified against a fresh DB row rather than a second login round-trip:
    re-login on the SAME shared test session triggers an async identity-map
    + refresh lazy-load of user.role (MissingGreenlet) that is a harness
    artifact, not a production bug — production uses a fresh session per
    request (covered by test_login_success).
    """
    bob = await _make_authed_user(db_session)
    as_user(bob)
    resp = await client.put(
        "/api/auth/password",
        json={"old_password": "s3cret!", "new_password": "newpass1"},
    )
    assert resp.status_code == 200

    fresh = (await db_session.execute(select(User).where(User.id == bob.id))).scalar_one()
    assert verify_password("newpass1", fresh.hashed_password)
    assert not verify_password("s3cret!", fresh.hashed_password)
