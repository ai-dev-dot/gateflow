"""僵尸 pending audit log 清理（P2-6）。

`StreamForwarder._save_after_stream` 在 finally 块中执行；若其中 commit
失败（DB 短暂中断、进程崩溃等），audit log 会永远停在 status='pending'。
一个月就是 30k+ 条僵尸记录，partial index ``ix_audit_logs_status_pending``
随之膨胀。本服务提供：

- :func:`mark_stale_pending_logs`: 单次扫描，把超过阈值的 pending 标为
  failed 并在 ``error_message`` 标注 stale（纯函数式，可测）
- :func:`stale_pending_cleanup_loop`: lifespan 后台循环，每 24h 扫一次
"""

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.utils.datetime_utils import utcnow
from app.utils.metrics import observe_stale_cleanup

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


async def mark_stale_pending_logs(
    db: AsyncSession,
    older_than_seconds: int = STALE_PENDING_SECONDS,
    *,
    now=None,
) -> int:
    """把 created_at 早于阈值的 pending 记录标为 failed。

    Returns:
        本次标记的行数（0 = 无僵尸记录）。

    ``completed_at`` 写为关闭时间（而非请求时间）——该值对僵尸记录
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


async def stale_pending_cleanup_loop(
    interval_seconds: int = CLEANUP_INTERVAL_SECONDS,
    initial_delay_seconds: int = CLEANUP_INITIAL_DELAY_SECONDS,
) -> None:
    """Lifespan 后台任务：周期性扫描并标记僵尸 pending。

    单轮失败只记日志不中断循环——清理是尽力而为的卫生任务，不能
    因为一轮 DB 抖动把后台任务带崩。
    """
    from app.database import async_session

    await asyncio.sleep(initial_delay_seconds)
    while True:
        try:
            async with async_session() as db:
                count = await mark_stale_pending_logs(db)
                if count:
                    logger.warning(
                        f"Marked {count} stale pending audit logs as failed"
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Stale pending cleanup failed: {e}", exc_info=True)
        await asyncio.sleep(interval_seconds)
