"""ModelConfig (gateway model routing) router HTTP tests (P2-1)."""

import pytest


@pytest.mark.asyncio
async def test_list_models_any_user(db_session, client, as_user, test_user):
    as_user(test_user)
    resp = await client.get("/api/gateway/models")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_create_update_delete(db_session, client, as_user, admin_user):
    as_user(admin_user)
    created = await client.post(
        "/api/gateway/models",
        json={
            "model_alias": "deepseek-chat",
            "provider": "deepseek",
            "target_model": "deepseek-chat",
            "target_url": "https://api.deepseek.com/v1",
            "priority": 0,
        },
    )
    assert created.status_code == 200
    mc_id = created.json()["id"]

    updated = await client.put(
        f"/api/gateway/models/{mc_id}", json={"is_active": False}
    )
    assert updated.status_code == 200
    assert updated.json()["is_active"] is False

    deleted = await client.delete(f"/api/gateway/models/{mc_id}")
    assert deleted.status_code == 200
    assert "删除" in deleted.json()["message"]


@pytest.mark.asyncio
async def test_non_admin_create_forbidden(db_session, client, as_user, test_user):
    as_user(test_user)
    resp = await client.post(
        "/api/gateway/models",
        json={"model_alias": "x", "provider": "p", "target_model": "x",
              "target_url": "http://x", "priority": 0},
    )
    assert resp.status_code == 403
