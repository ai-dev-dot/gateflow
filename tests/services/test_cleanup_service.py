"""Tests for zombie pending audit log cleanup (P2-6).

Covers:
- Old pending rows -> failed + stale marker in error_message
- Fresh pending rows are left alone (still in-flight requests)
- Completed/failed rows are never touched
- Returns the number of rows marked
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog
from app.services.audit_service import AuditService
from app.services.cleanup_service import STALE_MARKER, mark_stale_pending_logs
from app.utils.datetime_utils import utcnow


async def _make_log(db_session, test_user, *, status="pending", age_seconds=0):
    service = AuditService(db_session)
    log = await service.create_pending_log(
        user=test_user,
        model="gpt-4",
        provider="openai",
        path="/v1/chat/completions",
        request_body=None,
        is_stream=True,
    )
    # created_at defaults to now; backdate for the requested age.
    log.created_at = utcnow() - timedelta(seconds=age_seconds)
    if status != "pending":
        log.status = status
    await db_session.commit()
    await db_session.refresh(log)
    return log


@pytest.mark.asyncio
async def test_marks_old_pending_as_failed(db_session, test_user):
    """Pending older than the threshold becomes failed with the stale marker."""
    zombie = await _make_log(db_session, test_user, age_seconds=7200)  # 2h old

    count = await mark_stale_pending_logs(db_session, older_than_seconds=3600)

    assert count == 1
    result = await db_session.execute(select(AuditLog).where(AuditLog.id == zombie.id))
    row = result.scalar_one()
    assert row.status == "failed"
    assert row.error_message == STALE_MARKER
    assert row.completed_at is not None


@pytest.mark.asyncio
async def test_fresh_pending_untouched(db_session, test_user):
    """Pending younger than the threshold stays pending (in-flight request)."""
    fresh = await _make_log(db_session, test_user, age_seconds=60)  # 1 min

    count = await mark_stale_pending_logs(db_session, older_than_seconds=3600)

    assert count == 0
    result = await db_session.execute(select(AuditLog).where(AuditLog.id == fresh.id))
    row = result.scalar_one()
    assert row.status == "pending"
    assert row.error_message is None


@pytest.mark.asyncio
async def test_completed_and_failed_untouched(db_session, test_user):
    """Terminal rows are never re-marked, no matter their age."""
    done = await _make_log(db_session, test_user, status="completed", age_seconds=9999)
    failed = await _make_log(db_session, test_user, status="failed", age_seconds=9999)
    # A row that already carries a real error keeps it (no stale overwrite).
    failed.error_message = "upstream 500"
    await db_session.commit()

    count = await mark_stale_pending_logs(db_session, older_than_seconds=3600)

    assert count == 0
    result = await db_session.execute(
        select(AuditLog).where(AuditLog.id.in_([done.id, failed.id]))
    )
    rows = {r.id: r for r in result.scalars().all()}
    assert rows[done.id].status == "completed"
    assert rows[done.id].error_message is None
    assert rows[failed.id].status == "failed"
    assert rows[failed.id].error_message == "upstream 500"


@pytest.mark.asyncio
async def test_returns_total_marked(db_session, test_user):
    """Multiple zombies are all marked in one pass; count matches."""
    await _make_log(db_session, test_user, age_seconds=7200)
    await _make_log(db_session, test_user, age_seconds=86400)
    await _make_log(db_session, test_user, age_seconds=30)  # fresh, excluded

    count = await mark_stale_pending_logs(db_session, older_than_seconds=3600)

    assert count == 2
