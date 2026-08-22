"""Shared HTTP client + auth-override helpers for router tests (P2-1).

`client` binds httpx AsyncClient to the FastAPI app with `get_db` pointing
at the test's in-memory session (ASGITransport — no real sockets). Tests
that need an authenticated caller use the `as_user` / `as_auth_context`
fixtures to set `app.dependency_overrides`; the `client` fixture clears
ALL overrides on teardown so no state leaks across cases.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import get_db
from app.main import app
from app.middleware import auth_middleware


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def as_user():
    """Override get_current_user to return the given User (bypasses real auth).

    require_admin internally depends on get_current_user, so this single
    override also satisfies admin-guarded routes (pass admin_user to it).
    """

    def _apply(user):
        async def _override():
            return user

        app.dependency_overrides[auth_middleware.get_current_user] = _override

    return _apply


@pytest.fixture
def as_auth_context():
    """Override get_auth_context (used by the /v1 gateway routers)."""

    def _apply(user, *, api_key_id=None, agent_type=None):
        ctx = auth_middleware.AuthContext(user=user, api_key_id=api_key_id, agent_type=agent_type)

        async def _override():
            return ctx

        app.dependency_overrides[auth_middleware.get_auth_context] = _override

    return _apply
