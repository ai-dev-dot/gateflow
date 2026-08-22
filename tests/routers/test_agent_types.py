"""AgentType router HTTP tests (P2-1): list (all users) + admin CRUD."""

import pytest


@pytest.mark.asyncio
async def test_list_agent_types_any_user(db_session, client, as_user, test_user):
    as_user(test_user)
    resp = await client.get("/api/agent-types")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_create_duplicate_rejected(db_session, client, as_user, admin_user):
    as_user(admin_user)
    first = await client.post("/api/agent-types", json={"name": "Cursor"})
    assert first.status_code == 200
    assert first.json()["name"] == "Cursor"

    dup = await client.post("/api/agent-types", json={"name": "Cursor"})
    assert dup.status_code == 409


@pytest.mark.asyncio
async def test_update_agent_type(db_session, client, as_user, admin_user):
    as_user(admin_user)
    created = await client.post("/api/agent-types", json={"name": "Editor"})
    at_id = created.json()["id"]

    resp = await client.put(f"/api/agent-types/{at_id}", json={"is_active": False})
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


@pytest.mark.asyncio
async def test_delete_agent_type(db_session, client, as_user, admin_user):
    as_user(admin_user)
    created = await client.post("/api/agent-types", json={"name": "Temp"})
    at_id = created.json()["id"]

    resp = await client.delete(f"/api/agent-types/{at_id}")
    assert resp.status_code == 200
    assert resp.json()["detail"] == "Deleted"


@pytest.mark.asyncio
async def test_non_admin_create_forbidden(db_session, client, as_user, test_user):
    as_user(test_user)
    resp = await client.post("/api/agent-types", json={"name": "Nope"})
    assert resp.status_code == 403
