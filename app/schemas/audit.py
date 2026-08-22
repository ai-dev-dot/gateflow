"""Pydantic schemas for audit log API responses.

Two response shapes:
- `AuditLogListItem` — what `GET /api/audit/logs` returns. NEVER includes
  the full `request_body` / `response_body`. Includes a plaintext preview
  of the request body (first 200 chars) so admins/users can debug without
  exposing the full prompt.
- `AuditLogDetail` — what `GET /api/audit/logs/{id}` returns. Same as
  list item; the `?include_body=true` flag (admin only) decrypts and
  adds `request_body` and `response_body` fields to the response.

The split exists so a future "include_body for self only" or "include
preview length tuning" change is local to one schema.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AuditLogListItem(BaseModel):
    """List-item view of an audit log row. Never carries the full body."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    timestamp: datetime
    user_id: UUID
    username: str
    department: str | None = None
    api_key_id: UUID | None = None
    api_key_name: str | None = None
    agent_type: str | None = None
    model: str
    provider: str | None = None
    method: str
    path: str
    request_body_preview: str | None = None
    request_tokens: int
    response_tokens: int
    total_tokens: int
    latency_ms: int | None = None
    status_code: int | None = None
    # Internal diagnostics (upstream error body / exception repr, truncated
    # to 500 chars). Populated only for failed calls. Contains no credentials.
    error_message: str | None = None
    is_stream: bool
    ip_address: str | None = None
    user_agent: str | None = None


class AuditLogDetail(AuditLogListItem):
    """Detail view. Inherits all list-item fields.

    `request_body` and `response_body` are populated ONLY when the caller
    is admin AND explicitly opts in with `?include_body=true`. The router
    is responsible for both checks; this schema just declares the shape.
    """

    request_body: str | None = None  # Decrypted plaintext (admin + opt-in only)
    response_body: str | None = None
