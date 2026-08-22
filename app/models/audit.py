import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base
from app.utils.datetime_utils import utcnow


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status = Column(String(20), default="pending", nullable=False)
    timestamp = Column(DateTime, default=utcnow, nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    username = Column(String(50), nullable=False)
    department = Column(String(100), nullable=True)
    api_key_id = Column(UUID(as_uuid=True), ForeignKey("api_keys.id"), nullable=True)
    api_key_name = Column(String(100), nullable=True)  # 快照：请求发生时 client key 的 name
    agent_type = Column(String(50), nullable=True)
    model = Column(String(100), nullable=False)
    provider = Column(String(50), nullable=True)
    method = Column(String(10), nullable=False)
    path = Column(String(255), nullable=False)
    # Fernet-encrypted full request body. Populated only when
    # `AUDIT_LOG_FULL_BODY=true`. None otherwise. NEVER returned in any
    # list response — only via `GET /api/audit/logs/{id}?include_body=true`
    # (admin-only, with meta-audit write).
    request_body = Column(Text, nullable=True)
    # Plaintext preview of the request body, first N characters (see
    # settings.AUDIT_LOG_PREVIEW_CHARS). Safe to return in list responses.
    request_body_preview = Column(Text, nullable=True)
    request_tokens = Column(Integer, default=0, nullable=False)
    response_tokens = Column(Integer, default=0, nullable=False)
    total_tokens = Column(Integer, default=0, nullable=False)
    latency_ms = Column(Integer, nullable=True)
    status_code = Column(Integer, nullable=True)
    # Internal error detail (upstream error body / exception repr / "stale"
    # marker from the pending-cleanup task). Server-side diagnostics only -
    # never echoed to the LLM client (P0-4); safe to surface in admin/user
    # audit views since it carries no credentials.
    error_message = Column(Text, nullable=True)
    is_stream = Column(Boolean, default=False, nullable=False)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_audit_logs_timestamp", "timestamp"),
        Index("ix_audit_logs_user_timestamp", "user_id", "timestamp"),
        Index("ix_audit_logs_dept_timestamp", "department", "timestamp"),
        Index("ix_audit_logs_model_timestamp", "model", "timestamp"),
        Index(
            "ix_audit_logs_status_pending",
            "status",
            postgresql_where=text("status = 'pending'"),
        ),
    )
