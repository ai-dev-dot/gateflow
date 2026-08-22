"""API Key router HTTP tests (P2-1).

Pins the one-time-plaintext contract: POST returns the full key exactly once;
GET/UPDATE/DELETE never expose it and are scoped to the owner.
"""

import pytest
from sqlalchemy import select

from app.models.api_key import APIKey, generate_api_key
from app.models.user import User


async def _seed_key(db_session, user: User, name="k") -> APIKey:
    full, prefix, khash = generate_api_key()
    key = APIKey(user_id=user.id, name=name, key_hash=khash, key_prefix=prefix)
    db_session.add(key)
    await db_session.commit()
    await db_session.refresh(key)
    return key


@pytest.mark.asyncio
async def test_create_returns_one_time_plaintext(db_session, client, as_user, test_user):
    as_user(test_user)
    resp = await client.post("/api/api-keys", json={"name": "我的 Cursor"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["key"].startswith("gf_")
    assert body["name"] == "我的 Cursor"
    assert "key_prefix" in body


@pytest.mark.asyncio
async def test_list_own_keys_returns_prefix_not_full(db_session, client, as_user, test_user):
    key = await _seed_key(db_session, test_user, name="ci")
    as_user(test_user)
    resp = await client.get("/api/api-keys")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["id"] == str(key.id)
    assert "key" not in items[0]  # never the full plaintext
    assert items[0].get("key_prefix")


@pytest.mark.asyncio
async def test_update_key(db_session, client, as_user, test_user):
    key = await _seed_key(db_session, test_user)
    as_user(test_user)
    resp = await client.put(f"/api/api-keys/{key.id}", json={"name": "renamed", "rate_limit": 5})
    assert resp.status_code == 200
    assert resp.json()["name"] == "renamed"
    assert resp.json()["rate_limit"] == 5


@pytest.mark.asyncio
async def test_delete_key(db_session, client, as_user, test_user):
    key = await _seed_key(db_session, test_user)
    as_user(test_user)
    resp = await client.delete(f"/api/api-keys/{key.id}")
    assert resp.status_code == 200
    assert "删除" in resp.json()["message"]
    # Gone from DB
    assert (await db_session.execute(select(APIKey).where(APIKey.id == key.id))).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_cannot_touch_other_users_key(db_session, client, as_user, test_user, admin_user):
    other_key = await _seed_key(db_session, admin_user, name="admin-key")
    as_user(test_user)
    listed = await client.get("/api/api-keys")
    assert all(k["id"] != str(other_key.id) for k in listed.json())
    assert (await client.put(f"/api/api-keys/{other_key.id}", json={"name": "x"})).status_code == 404
    assert (await client.delete(f"/api/api-keys/{other_key.id}")).status_code == 404
