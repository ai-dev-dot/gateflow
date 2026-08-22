"""审计日志后台维护（P2-6 僵尸 pending + 保留期清理）。

两件事，同一个 24h 循环（lifespan 启动）：

1. **僵尸 pending 标记**（P2-6）：`StreamForwarder._save_after_stream` 在
   finally 块中执行；若其中 commit 失败（DB 短暂中断、进程崩溃等），
   audit log 会永远停在 status='pending'。一个月就是 30k+ 条僵尸记录，
   partial index ``ix_audit_logs_status_pending`` 随之膨胀。
2. **保留期删除**：spec 承诺的 ``AUDIT_LOG_RETENTION_DAYS``（默认 90 天，
   GDPR/PIPL"按需最小化保留"）。超过保留期的行直接 DELETE--备份功能
   （pg_dump）已提供归档途径，要历史的先备份。

提供的可测函数：

- :func:`mark_stale_pending_logs`: 单次扫描，把超过阈值的 pending 标为
  failed 并在 ``error_message`` 标注 stale
- :func:`delete_expired_audit_logs`: 分批删除超过保留期的行（<=0 永久保留）
- :func:`audit_maintenance_loop`: lifespan 后台循环，每 24h 一轮
"""

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.utils.datetime_utils import utcnow
from app.utils.metrics import observe_audit_deletion, observe_stale_cleanup

logger = logging.getLogger(__name__)

# A pending log older than this is a zombie: normal requests finish (or
# fail) within minutes -- the forwarder's finally-block save is the last
# write. Still pending past 1h means that save never landed.
STALE_PENDING_SECONDS = 3600

# Background scan cadence. This is hygiene, not user-facing latency.
CLEANUP_INTERVAL_SECONDS = 24 * 3600

# First scan runs shortly after startup (not immediately) so it does not
# compete with startup traffic for DB connections.
CLEANUP_INITIAL_DELAY_SECONDS = 300

# Marker persisted in error_message (P1-8 field) so cleaned rows are
# distinguishable from real upstream failures.
STALE_MARKER = "stale pending: marked failed by cleanup task"

# Retention deletion runs in id-subquery batches: one giant DELETE on a
# months-old table would hold a long transaction / row locks. Both PG and
# SQLite support `id IN (SELECT id ... LIMIT n)`.
DELETE_BATCH_SIZE = 1000


async def mark_stale_pending_logs(
    db: AsyncSession,
    older_than_seconds: int = STALE_PENDING_SECONDS,
    *,
    now=None,
) -> int:
    """把 created_at 早于阈值的 pending 记录标为 failed。

    Returns:
        本次标记的行数（0 = 无僵尸记录）。

    ``completed_at`` 写为关闭时间（而非请求时间）--该值对僵尸记录
    本无意义，取"被清理"的时间戳便于事后排查清理批次。
    """
    current = now or utcnow()
    cutoff = current - timedelta(seconds=older_than_seconds)
    result = await db.execute(
        update(AuditLog)
        .where(
            AuditLog.status == "pending",
            AuditLog.created_at < cutoff,
        )
        .values(
            status="failed",
            error_message=STALE_MARKER,
            completed_at=current,
        )
    )
    await db.commit()
    count = result.rowcount or 0
    # P2-8: stale 清理量进业务指标（无 model/provider 维度，按 status=stale 计）
    observe_stale_cleanup(count)
    return count


async def delete_expired_audit_logs(
    db: AsyncSession,
    retention_days: int,
    *,
    batch_size: int = DELETE_BATCH_SIZE,
) -> int:
    """删除超过保留期的审计日志，返回删除行数。

    ``retention_days <= 0`` 表示永久保留（直接返回 0，不执行 DELETE）。
    分批删除（每批 ``batch_size`` 行）避免大表长事务锁表。
    """
    if retention_days <= 0:
        return 0

    cutoff = utcnow() - timedelta(days=retention_days)
    total = 0
    while True:
        result = await db.execute(
            delete(AuditLog).where(
                AuditLog.id.in_(
                    select(AuditLog.id)
                    .where(AuditLog.created_at < cutoff)
                    .limit(batch_size)
                )
            )
        )
        await db.commit()
        deleted = result.rowcount or 0
        total += deleted
        if deleted < batch_size:
            break

    observe_audit_deletion(total)
    return total


async def audit_maintenance_loop(
    interval_seconds: int = CLEANUP_INTERVAL_SECONDS,
    initial_delay_seconds: int = CLEANUP_INITIAL_DELAY_SECONDS,
) -> None:
    """Lifespan 后台任务：每 24h 一轮审计维护。

    每轮两步：① 僵尸 pending 标 failed（P2-6）；② 按
    ``AUDIT_LOG_RETENTION_DAYS`` 删除过期日志。单轮失败只记日志不中断
    循环--维护是尽力而为的卫生任务，不能因一轮 DB 抖动把后台任务带崩。
    """
    from app.config import get_settings
    from app.database import async_session

    await asyncio.sleep(initial_delay_seconds)
    while True:
        try:
            async with async_session() as db:
                stale = await mark_stale_pending_logs(db)
                if stale:
                    logger.warning(f"Marked {stale} stale pending audit logs as failed")

                retention_days = get_settings().AUDIT_LOG_RETENTION_DAYS
                deleted = await delete_expired_audit_logs(db, retention_days)
                if deleted:
                    logger.warning(
                        f"Deleted {deleted} audit logs past {retention_days}d retention"
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Audit maintenance cycle failed: {e}", exc_info=True)
        await asyncio.sleep(interval_seconds)
