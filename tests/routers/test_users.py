"""Users + departments router HTTP tests (P2-1)."""

import pytest


@pytest.mark.asyncio
async def test_list_users_admin(db_session, client, as_user, admin_user):
    as_user(admin_user)
    resp = await client.get("/api/users")
    assert resp.status_code == 200
    names = [u["username"] for u in resp.json()]
    assert "admin" in names


@pytest.mark.asyncio
async def test_list_users_non_admin_forbidden(db_session, client, as_user, test_user):
    as_user(test_user)
    resp = await client.get("/api/users")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_user_and_duplicate_rejected(db_session, client, as_user, admin_user):
    as_user(admin_user)
    resp = await client.post(
        "/api/users",
        json={"username": "carol", "email": "carol@t.local", "password": "pw12345"},
    )
    assert resp.status_code == 200
    assert resp.json()["username"] == "carol"

    dup = await client.post(
        "/api/users",
        json={"username": "carol", "email": "carol2@t.local", "password": "pw12345"},
    )
    assert dup.status_code == 400
    assert "已存在" in dup.json()["detail"]


@pytest.mark.asyncio
async def test_update_user(db_session, client, as_user, admin_user, test_user):
    as_user(admin_user)
    resp = await client.put(f"/api/users/{test_user.id}", json={"is_active": False})
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


@pytest.mark.asyncio
async def test_delete_user(db_session, client, as_user, admin_user, test_user):
    as_user(admin_user)
    resp = await client.delete(f"/api/users/{test_user.id}")
    assert resp.status_code == 200
    assert "删除" in resp.json()["message"]


@pytest.mark.asyncio
async def test_departments_crud(db_session, client, as_user, admin_user):
    as_user(admin_user)
    created = await client.post("/api/users/departments", json={"name": "新部门"})
    assert created.status_code == 200
    dept_id = created.json()["id"]

    listed = await client.get("/api/users/departments")
    assert any(d["id"] == str(dept_id) for d in listed.json())

    updated = await client.put(f"/api/users/departments/{dept_id}", json={"name": "改名部"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "改名部"

    deleted = await client.delete(f"/api/users/departments/{dept_id}")
    assert deleted.status_code == 200
