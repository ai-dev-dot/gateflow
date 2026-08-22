"""Backup router HTTP tests (P2-1): admin-only config surface.

POST /run is intentionally NOT tested here — it shells out to pg_dump and
its response depends on the runtime DATABASE_URL dialect (501 on SQLite /
500 on PG without a configured pg_dump path), so it is exercised by the
manual E2E gate instead.
"""

import pytest


@pytest.mark.asyncio
async def test_get_config_admin(db_session, client, as_user, admin_user):
    as_user(admin_user)
    resp = await client.get("/api/backup/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["backup_include_audit_logs"] is False  # default policy
    assert "updated_at" in body


@pytest.mark.asyncio
async def test_update_config_and_reflect(db_session, client, as_user, admin_user):
    as_user(admin_user)
    resp = await client.put("/api/backup/config", json={"backup_dir": "C:/tmp/backups-test"})
    assert resp.status_code == 200
    assert resp.json()["backup_dir"] == "C:/tmp/backups-test"

    got = await client.get("/api/backup/config")
    assert got.json()["backup_dir"] == "C:/tmp/backups-test"


@pytest.mark.asyncio
async def test_empty_backup_dir_rejected(db_session, client, as_user, admin_user):
    as_user(admin_user)
    resp = await client.put("/api/backup/config", json={"backup_dir": "   "})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_non_admin_forbidden(db_session, client, as_user, test_user):
    as_user(test_user)
    assert (await client.get("/api/backup/config")).status_code == 403
    assert (
        await client.put("/api/backup/config", json={"backup_dir": "/tmp/x"})
    ).status_code == 403
