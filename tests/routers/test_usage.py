"""Usage router HTTP tests (P2-1): admin summary/trend + user my-* endpoints."""

import pytest


@pytest.mark.asyncio
async def test_admin_summary_empty(db_session, client, as_user, admin_user):
    as_user(admin_user)
    resp = await client.get("/api/usage/summary", params={"dimension": "user"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["dimension"] == "user"
    assert body["items"] == []


@pytest.mark.asyncio
async def test_admin_trend_empty(db_session, client, as_user, admin_user):
    as_user(admin_user)
    resp = await client.get("/api/usage/trend")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


@pytest.mark.asyncio
async def test_non_admin_summary_forbidden(db_session, client, as_user, test_user):
    as_user(test_user)
    assert (await client.get("/api/usage/summary")).status_code == 403


@pytest.mark.asyncio
async def test_my_summary_and_my_trend(db_session, client, as_user, test_user):
    as_user(test_user)
    s = await client.get("/api/usage/my-summary", params={"dimension": "model"})
    assert s.status_code == 200
    assert s.json()["items"] == []

    t = await client.get("/api/usage/my-trend")
    assert t.status_code == 200
    assert t.json()["data"] == []
