from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth_middleware import get_current_user
from app.models.user import User
from app.schemas.audit import AuditLogDetail, AuditLogListItem
from app.services.audit_service import AuditService

router = APIRouter(prefix="/api/audit", tags=["审计日志"])


def _to_list_item(log) -> AuditLogListItem:
    """Project an AuditLog ORM row into the list-item response shape.
    NEVER includes the encrypted `request_body` field.
    """
    return AuditLogListItem(
        id=log.id,
        status=log.status,
        timestamp=log.timestamp,
        user_id=log.user_id,
        username=log.username,
        department=log.department,
        api_key_id=log.api_key_id,
        api_key_name=log.api_key_name,
        agent_type=log.agent_type,
        model=log.model,
        provider=log.provider,
        method=log.method,
        path=log.path,
        request_body_preview=log.request_body_preview,
        request_tokens=log.request_tokens,
        response_tokens=log.response_tokens,
        total_tokens=log.total_tokens,
        latency_ms=log.latency_ms,
        status_code=log.status_code,
        error_message=log.error_message,
        is_stream=log.is_stream,
        ip_address=log.ip_address,
        user_agent=log.user_agent,
    )


def _to_detail(log, *, decrypted_body: str | None = None) -> AuditLogDetail:
    """Project an AuditLog ORM row into the detail response shape.

    `decrypted_body` is the plaintext request body, populated ONLY when
    the caller is admin AND explicitly opted in via `?include_body=true`.
    Callers must pass None otherwise.
    """
    base = _to_list_item(log).model_dump()
    return AuditLogDetail(**base, request_body=decrypted_body, response_body=None)


@router.get("/logs", response_model=dict)
async def get_audit_logs(
    user_id: UUID | None = Query(None, description="按用户 ID 筛选"),
    department: str | None = Query(None, description="按部门筛选"),
    model: str | None = Query(None, description="按模型筛选"),
    start_time: datetime | None = Query(None, description="开始时间"),
    end_time: datetime | None = Query(None, description="结束时间"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询审计日志，非管理员只能查看自己的日志。

    永远不返回完整 `request_body` / `response_body`——仅返回 metadata +
    `request_body_preview`（明文前 200 字符）。要看完整 body 请用
    `GET /api/audit/logs/{id}?include_body=true`（仅 admin）。
    """
    service = AuditService(db)

    # 非管理员只能查看自己的日志
    if current_user.role.name != "admin":
        user_id = current_user.id

    result = await service.get_logs(
        user_id=user_id,
        department=department,
        model=model,
        start_time=start_time,
        end_time=end_time,
        page=page,
        page_size=page_size,
    )

    return {
        "total": result["total"],
        "page": result["page"],
        "page_size": result["page_size"],
        "items": [_to_list_item(log) for log in result["items"]],
    }


@router.get("/logs/{log_id}", response_model=AuditLogDetail)
async def get_log_detail(
    log_id: UUID,
    request: Request,
    include_body: bool = Query(
        False,
        description="包含完整请求/响应 body。**仅 admin 可用**——每次访问会写 meta-audit",
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取审计日志详情。

    默认不返回完整 `request_body`（Fernet 加密存储的）。带
    `?include_body=true` 时：
    - 验证调用者角色是 admin
    - 解密 `request_body` 并返回明文
    - 写入一条 meta-audit (`path='/admin/audit-access'`) 记录这次访问

    非 admin 即便带 include_body=true 也会被拒绝（403）。
    """
    audit_service = AuditService(db)
    log = await audit_service.get_log_by_id(log_id, current_user)
    if not log:
        raise HTTPException(status_code=404, detail="日志不存在")

    decrypted = None
    if include_body:
        if current_user.role.name != "admin":
            raise HTTPException(
                status_code=403,
                detail="只有管理员可以查看完整 body",
            )
        if log.request_body is None:
            # FULL_BODY=false was set when this row was created
            decrypted = None
        else:
            try:
                decrypted = audit_service.decrypt_request_body(log.request_body)
            except Exception as err:
                # Decryption failure is itself an audit-worthy event
                raise HTTPException(
                    status_code=500,
                    detail="无法解密日志 body（ENCRYPTION_KEY 可能已变更）",
                ) from err

        # Record meta-audit AFTER successful decryption so the audit
        # log reflects what the admin actually saw (not denied requests).
        await audit_service.record_admin_access(
            current_user,
            log,
            ip_address=request.client.host if request.client else None,
        )
        await db.commit()

    return _to_detail(log, decrypted_body=decrypted)
