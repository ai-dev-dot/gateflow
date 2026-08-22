"""Provider API Key router HTTP tests (P2-1).

Pins the no-plaintext contract: create/update never return the full key;
reset clears ban + error counters.
"""

import pytest
from sqlalchemy import select

from app.models.provider_key import ProviderAPIKey
from app.utils.crypto import encrypt_key, key_preview


def _mk_key(name="k", provider="openai") -> ProviderAPIKey:
    return ProviderAPIKey(
        provider=provider,
        encrypted_key=encrypt_key("sk-" + name + "-secret"),
        key_preview=key_preview("sk-" + name + "-secret"),
        name=name,
        is_active=True,
    )


@pytest.mark.asyncio
async def test_create_returns_preview_not_plaintext(db_session, client, as_user, admin_user):
    as_user(admin_user)
    resp = await client.post(
        "/api/gateway/provider-keys",
        json={"provider": "openai", "key": "sk-DEVELOPER-SECRET", "name": "prod"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "key" not in body  # never returns plaintext
    assert body["key_preview"]
    assert body["provider"] == "openai"


@pytest.mark.asyncio
async def test_list_filters_by_provider(db_session, client, as_user, admin_user):
    db_session.add_all(
        [_mk_key("openai-k", "openai"), _mk_key("deepseek-k", "deepseek"), _mk_key("mimo-k", "mimo")]
    )
    await db_session.commit()
    as_user(admin_user)
    resp = await client.get("/api/gateway/provider-keys", params={"provider": "deepseek"})
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["provider"] == "deepseek"


@pytest.mark.asyncio
async def test_non_admin_forbidden(db_session, client, as_user, test_user):
    as_user(test_user)
    assert (await client.get("/api/gateway/provider-keys")).status_code == 403
    assert (
        await client.post(
            "/api/gateway/provider-keys", json={"provider": "x", "key": "k", "name": "n"}
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_update_and_reset_clears_ban(db_session, client, as_user, admin_user):
    pk = _mk_key()
    pk.is_active = False
    pk.is_banned = True
    pk.ban_reason = "401"
    pk.consecutive_errors = 9
    db_session.add(pk)
    await db_session.commit()
    await db_session.refresh(pk)

    as_user(admin_user)
    up = await client.put(f"/api/gateway/provider-keys/{pk.id}", json={"name": "renamed"})
    assert up.status_code == 200
    assert up.json()["name"] == "renamed"

    rst = await client.post(f"/api/gateway/provider-keys/{pk.id}/reset")
    assert rst.status_code == 200
    body = rst.json()
    assert body["is_banned"] is False
    assert body["consecutive_errors"] == 0
    assert body["is_active"] is True


@pytest.mark.asyncio
async def test_delete_provider_key(db_session, client, as_user, admin_user):
    pk = _mk_key()
    db_session.add(pk)
    await db_session.commit()
    await db_session.refresh(pk)

    as_user(admin_user)
    resp = await client.delete(f"/api/gateway/provider-keys/{pk.id}")
    assert resp.status_code == 200
    assert "删除" in resp.json()["message"]
    assert (
        await db_session.execute(select(ProviderAPIKey).where(ProviderAPIKey.id == pk.id))
    ).scalar_one_or_none() is None
