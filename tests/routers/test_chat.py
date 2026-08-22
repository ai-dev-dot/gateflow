"""Chat router HTTP tests (P2-1).

Concurrency/LLM flows are covered at the service layer; here we cover the
HTTP surface: conversation CRUD, ownership 404s, and non-stream send to a
missing conversation (which short-circuits before any LLM call).
"""

import pytest


@pytest.mark.asyncio
async def test_create_and_list_conversations(db_session, client, as_user, test_user):
    as_user(test_user)
    created = await client.post("/api/chat/conversations", json={"model": "deepseek-chat"})
    assert created.status_code == 201
    conv = created.json()
    assert conv["model"] == "deepseek-chat"
    assert conv["id"]

    listed = await client.get("/api/chat/conversations")
    assert listed.status_code == 200
    assert [c["id"] for c in listed.json()] == [conv["id"]]


@pytest.mark.asyncio
async def test_get_messages_missing_conversation_404(db_session, client, as_user, test_user):
    import uuid

    as_user(test_user)
    resp = await client.get(f"/api/chat/conversations/{uuid.uuid4()}/messages")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_send_message_missing_conversation_404(db_session, client, as_user, test_user):
    """Non-stream send to a conversation that doesn't exist returns 404 without an LLM call."""
    import uuid

    as_user(test_user)
    resp = await client.post(
        f"/api/chat/conversations/{uuid.uuid4()}/messages", json={"content": "hi"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_conversation_twice(db_session, client, as_user, test_user):
    as_user(test_user)
    created = await client.post("/api/chat/conversations", json={"model": "m"})
    conv_id = created.json()["id"]

    first = await client.delete(f"/api/chat/conversations/{conv_id}")
    assert first.status_code == 204
    second = await client.delete(f"/api/chat/conversations/{conv_id}")
    assert second.status_code == 404
