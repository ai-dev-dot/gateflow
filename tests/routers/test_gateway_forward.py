"""Gateway forward router HTTP tests (P2-1): /v1/models + /v1/chat/completions.

Covers the auth guard (401 without credentials), model resolution (404),
and the full forward path against an unreachable upstream (500, P0-4 fixed
message — never leaks internals).
"""

import pytest

from app.models.gateway import ModelConfig
from app.models.provider_key import ProviderAPIKey
from app.utils.crypto import encrypt_key, key_preview


async def _seed_gateway(db_session, model_alias="m3", target_url="http://127.0.0.1:9"):
    mc = ModelConfig(
        model_alias=model_alias,
        provider="openai",
        target_model="x",
        target_url=target_url,
        is_active=True,
    )
    pk = ProviderAPIKey(
        provider="openai",
        encrypted_key=encrypt_key("sk-e2e"),
        key_preview=key_preview("sk-e2e"),
        name="e2e",
        is_active=True,
    )
    db_session.add(mc)
    db_session.add(pk)
    await db_session.commit()
    return mc


@pytest.mark.asyncio
async def test_models_list_requires_auth(client):
    assert (await client.get("/v1/models")).status_code == 401


@pytest.mark.asyncio
async def test_models_list_returns_active_models(db_session, client, as_auth_context, test_user):
    await _seed_gateway(db_session)
    db_session.add(
        ModelConfig(model_alias="inactive-m", provider="openai", target_model="x",
                    target_url="http://127.0.0.1:9", is_active=False)
    )
    await db_session.commit()
    as_auth_context(test_user)
    resp = await client.get("/v1/models")
    assert resp.status_code == 200
    ids = [m["id"] for m in resp.json()["data"]]
    assert "m3" in ids
    assert "inactive-m" not in ids


@pytest.mark.asyncio
async def test_chat_completions_requires_auth(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "m3", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_chat_completions_unknown_model_404(db_session, client, as_auth_context, test_user):
    await _seed_gateway(db_session)  # only "m3" exists
    as_auth_context(test_user)
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "nope", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_chat_completions_upstream_failure_returns_fixed_500(
    db_session, client, as_auth_context, test_user
):
    """Unreachable upstream → 500 with a fixed P0-4 message (no internal leak)."""
    await _seed_gateway(db_session)
    as_auth_context(test_user)
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "m3", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 500
    detail = resp.json().get("error", {}).get("message", "")
    assert "Connect" not in detail and "127.0.0.1" not in detail  # P0-4: no internals leaked
