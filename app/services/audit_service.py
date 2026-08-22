"""审计日志服务：记录和查询 API 调用日志

提供统一的 pending 创建 + 完成后更新接口。StreamForwarder 在流结束后
调用 `record_completion()` 统一处理 status 字段（"completed"/"failed"）和
token 累加（避免之前 gateway_service 与 chat_service 两处实现漂移的问题）。

数据存储策略（见 README "数据存储与隐私" + spec §6.3）：
- `request_body_preview`：明文前 200 字符，永远写入
- `request_body`：仅当 `AUDIT_LOG_FULL_BODY=true` 时写入（Fernet 加密）
  永远不通过 list/detail 接口自动返回，必须显式 `?include_body=true` + admin
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.audit import AuditLog
from app.utils.crypto import decrypt_key, encrypt_key
from app.utils.datetime_utils import utcnow
from app.utils.metrics import observe_llm_call


class AuditService:
    """审计日志服务：记录和查询 API 调用日志"""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ---------- 写入路径（流式转发时使用）----------

    async def create_pending_log(
        self,
        user,
        model: str,
        provider: str,
        path: str,
        request_body: str | None,
        is_stream: bool = False,
        api_key_id: UUID | None = None,
        api_key_name: str | None = None,
        agent_type: str | None = None,
        method: str = "POST",
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuditLog:
        """创建一条待处理的审计日志，flush() 后日志已有 id。

        注意：调用方需要负责 commit（或由 StreamForwarder 统一处理）。
        这里用 flush 而不是 commit，避免在请求作用域内强制落盘。

        Body handling (per data policy):
        - `request_body_preview` = first N chars, always plaintext
        - `request_body` = Fernet-encrypted full body, only when
          `AUDIT_LOG_FULL_BODY=true`; None otherwise

        Callers are expected to truncate `request_body` to a reasonable
        size before passing it in. The previous 100KB internal ceiling
        was removed in P1-7 because every caller already pre-truncates
        to ~2KB and the internal ceiling was unreachable in practice.
        """
        settings = get_settings()

        preview = None
        encrypted_body = None

        if request_body:
            # Always: short plaintext preview for list/detail display.
            # For long bodies, render "head50...tail20" so a short prompt
            # is not fully exposed. For bodies that fit within the budget,
            # the preview is the full body (rare for long prompts, common
            # for short chat messages — explicit trade-off documented in
            # design spec §6.3).
            if len(request_body) <= settings.AUDIT_LOG_PREVIEW_CHARS:
                preview = request_body
            else:
                head = settings.AUDIT_LOG_PREVIEW_CHARS // 2  # 40 chars default
                tail = settings.AUDIT_LOG_PREVIEW_CHARS - head - 3  # 37 chars default
                preview = f"{request_body[:head]}...{request_body[-tail:]}"

            # Conditionally: encrypted full body
            if settings.AUDIT_LOG_FULL_BODY:
                encrypted_body = encrypt_key(request_body)

        log = AuditLog(
            user_id=user.id,
            username=user.username,
            department=user.department.name if user.department else None,
            model=model,
            provider=provider,
            method=method,
            path=path,
            request_body=encrypted_body,
            request_body_preview=preview,
            is_stream=is_stream,
            status="pending",
            api_key_id=api_key_id,
            api_key_name=api_key_name,
            agent_type=agent_type,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.add(log)
        await self.db.flush()
        return log

    @staticmethod
    def decrypt_request_body(encrypted: str) -> str:
        """Decrypt an `AuditLog.request_body` field. Raises if the value
        is not a valid Fernet token (corrupted row or wrong ENCRYPTION_KEY).
        """
        return decrypt_key(encrypted)

    async def record_completion(
        self,
        log: AuditLog,
        status_code: int,
        request_tokens: int = 0,
        response_tokens: int = 0,
        latency_ms: int = 0,
        error_message: str | None = None,
    ) -> None:
        """记录一次请求的完成状态（在流结束/响应结束后调用）。

        状态判断统一在此处：
        - status_code == 200 → "completed"
        - 其他 → "failed"
        """
        log.status_code = status_code
        log.request_tokens = request_tokens
        log.response_tokens = response_tokens
        log.total_tokens = request_tokens + response_tokens
        log.latency_ms = latency_ms
        log.completed_at = utcnow()
        log.status = "completed" if status_code == 200 else "failed"
        # 内部诊断信息（上游错误 body / 异常 repr），截断 500 字符。
        # 仅落库供审计视图查看，永不回显给 LLM 客户端（P0-4）。
        log.error_message = error_message[:500] if error_message else None

        # P2-8: 业务指标（非流式路径的公共出口；流式在 _save_after_stream）
        observe_llm_call(
            model=log.model,
            provider=log.provider or "-",
            status=log.status,
            latency_ms=latency_ms,
        )

    # ---------- 读取路径（管理后台用）----------

    async def record_admin_access(
        self,
        admin_user,
        target_log: AuditLog,
        ip_address: str | None = None,
    ) -> AuditLog:
        """Write a meta-audit row when an admin views another user's log body.

        Path is fixed to `/admin/audit-access` so this category of access
        can be queried/alerted on independently of regular LLM calls.
        The `request_body_preview` carries a short key=value summary
        (truncated to the standard preview budget by the caller of
        create_pending_log). The full UUID of the target log is
        intentionally preserved in a follow-up implementation via a
        separate side table; for now the first 8 hex chars of the
        target log id is the unique-enough correlation key in preview.
        """
        target_short = str(target_log.id).split("-")[0]  # first 8 hex chars
        user_short = str(target_log.user_id).split("-")[0]
        # Build preview outside the create_pending_log path so we control
        # the exact format (which differs structurally from an LLM body).
        settings = get_settings()
        meta_preview_raw = f"viewed log={target_short} user={user_short} path={target_log.path}"
        if len(meta_preview_raw) <= settings.AUDIT_LOG_PREVIEW_CHARS:
            meta_preview = meta_preview_raw
        else:
            head = settings.AUDIT_LOG_PREVIEW_CHARS // 2
            tail = settings.AUDIT_LOG_PREVIEW_CHARS - head - 3
            meta_preview = f"{meta_preview_raw[:head]}...{meta_preview_raw[-tail:]}"

        log = AuditLog(
            user_id=admin_user.id,
            username=admin_user.username,
            department=admin_user.department.name if admin_user.department else None,
            model="-",
            provider="-",
            method="GET",
            path="/admin/audit-access",
            request_body_preview=meta_preview,
            is_stream=False,
            status="completed",
            ip_address=ip_address,
            user_agent=None,
        )
        self.db.add(log)
        await self.db.flush()
        return log

    async def get_log_by_id(self, log_id: UUID, user) -> AuditLog | None:
        """Get audit log by ID, non-admin can only see own logs"""
        query = select(AuditLog).where(AuditLog.id == log_id)
        if user.role.name != "admin":
            query = query.where(AuditLog.user_id == user.id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_logs(
        self,
        user_id: UUID | None = None,
        department: str | None = None,
        model: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """查询审计日志，支持多维度筛选和分页"""
        query = select(AuditLog)
        count_query = select(func.count(AuditLog.id))

        # 构建筛选条件
        filters = []
        if user_id:
            filters.append(AuditLog.user_id == user_id)
        if department:
            filters.append(AuditLog.department == department)
        if model:
            filters.append(AuditLog.model == model)
        if start_time:
            filters.append(AuditLog.timestamp >= start_time)
        if end_time:
            filters.append(AuditLog.timestamp <= end_time)

        if filters:
            query = query.where(*filters)
            count_query = count_query.where(*filters)

        # 获取总数
        total_result = await self.db.execute(count_query)
        total = total_result.scalar()

        # 分页查询
        offset = (page - 1) * page_size
        query = query.order_by(AuditLog.timestamp.desc()).offset(offset).limit(page_size)
        result = await self.db.execute(query)
        logs = result.scalars().all()

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": logs,
        }
