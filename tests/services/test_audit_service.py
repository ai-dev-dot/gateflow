"""Tests for audit service: pending log creation + completion recording.

These tests protect:
- Snapshot fields are written at pending time (not JOIN-fetched later)
- record_completion sets status="completed" for 200, "failed" otherwise
- Total tokens = request + response
"""

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog
from app.services.audit_service import AuditService


@pytest.mark.asyncio
async def test_create_pending_log_writes_snapshots(db_session, test_user):
    """Snapshot fields (username, department) are taken at create time."""
    service = AuditService(db_session)
    log = await service.create_pending_log(
        user=test_user,
        model="gpt-4",
        provider="openai",
        path="/v1/chat/completions",
        request_body='{"messages":[]}',
        is_stream=False,
    )
    await db_session.commit()
    await db_session.refresh(log)

    assert log.id is not None
    assert log.status == "pending"
    assert log.username == "alice"
    assert log.department == "工程部"
    assert log.model == "gpt-4"
    assert log.provider == "openai"
    assert log.path == "/v1/chat/completions"
    assert log.is_stream is False


@pytest.mark.asyncio
async def test_record_completion_success(db_session, test_user):
    """200 → status='completed'."""
    service = AuditService(db_session)
    log = await service.create_pending_log(
        user=test_user,
        model="gpt-4",
        provider="openai",
        path="/v1/chat/completions",
        request_body=None,
        is_stream=True,
    )
    await db_session.commit()

    await service.record_completion(
        log, status_code=200, request_tokens=10, response_tokens=20, latency_ms=150
    )
    await db_session.commit()

    assert log.status == "completed"
    assert log.status_code == 200
    assert log.request_tokens == 10
    assert log.response_tokens == 20
    assert log.total_tokens == 30
    assert log.latency_ms == 150
    assert log.completed_at is not None


@pytest.mark.asyncio
async def test_record_completion_failure(db_session, test_user):
    """non-200 → status='failed'."""
    service = AuditService(db_session)
    log = await service.create_pending_log(
        user=test_user,
        model="gpt-4",
        provider="openai",
        path="/v1/chat/completions",
        request_body=None,
        is_stream=False,
    )
    await db_session.commit()

    await service.record_completion(
        log, status_code=503, request_tokens=10, response_tokens=0, latency_ms=5000
    )
    await db_session.commit()

    assert log.status == "failed"
    assert log.status_code == 503


@pytest.mark.asyncio
async def test_record_completion_persists_error_message(db_session, test_user):
    """P1-8: failure diagnostics land in audit_logs.error_message; success
    keeps it NULL."""
    service = AuditService(db_session)
    log = await service.create_pending_log(
        user=test_user,
        model="gpt-4",
        provider="openai",
        path="/v1/chat/completions",
        request_body=None,
        is_stream=False,
    )
    await db_session.commit()

    await service.record_completion(
        log,
        status_code=500,
        request_tokens=10,
        error_message="ValueError('boom')",
    )
    await db_session.commit()
    assert log.error_message == "ValueError('boom')"

    # Success path keeps the field empty
    log2 = await service.create_pending_log(
        user=test_user,
        model="gpt-4",
        provider="openai",
        path="/v1/chat/completions",
        request_body=None,
        is_stream=False,
    )
    await db_session.commit()
    await service.record_completion(log2, status_code=200, request_tokens=1)
    await db_session.commit()
    assert log2.error_message is None


@pytest.mark.asyncio
async def test_record_completion_truncates_error_message(db_session, test_user):
    """P1-8: error_message is capped at 500 chars regardless of input size."""
    service = AuditService(db_session)
    log = await service.create_pending_log(
        user=test_user,
        model="gpt-4",
        provider="openai",
        path="/v1/chat/completions",
        request_body=None,
        is_stream=False,
    )
    await db_session.commit()

    await service.record_completion(log, status_code=500, error_message="x" * 2000)
    await db_session.commit()
    assert log.error_message is not None
    assert len(log.error_message) == 500


@pytest.mark.asyncio
async def test_snapshot_unchanged_after_user_mutation(db_session, test_user):
    """THE core invariant: rename user/department AFTER audit log created,
    old audit log's snapshot fields stay unchanged.

    This is the architectural principle behind the refactor — stats must not
    change because of post-hoc user/department mutations.
    """
    service = AuditService(db_session)
    log = await service.create_pending_log(
        user=test_user,
        model="gpt-4",
        provider="openai",
        path="/v1/chat/completions",
        request_body=None,
        is_stream=False,
    )
    await db_session.commit()
    original_username = log.username
    original_department = log.department

    # Simulate user/department mutations
    test_user.username = "alice-renamed"
    test_user.department.name = "新部门"
    await db_session.commit()

    # Refresh log from DB
    result = await db_session.execute(select(AuditLog).where(AuditLog.id == log.id))
    refreshed = result.scalar_one()
    assert refreshed.username == original_username, "username snapshot must be frozen"
    assert refreshed.department == original_department, "department snapshot must be frozen"


@pytest.mark.asyncio
async def test_request_body_passed_through_unchanged(db_session, test_user, monkeypatch):
    """P1-7: AuditService no longer truncates the request body internally.

    Every caller pre-truncates to ~2KB before calling create_pending_log, so
    the historical 100KB internal ceiling was unreachable dead code. The
    service now stores the body verbatim (encrypting it if
    AUDIT_LOG_FULL_BODY=true).
    """
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "AUDIT_LOG_FULL_BODY", True)

    service = AuditService(db_session)
    body = "x" * 1500  # under both old 2KB caller cap and old 100KB ceiling
    log = await service.create_pending_log(
        user=test_user,
        model="gpt-4",
        provider="openai",
        path="/v1/chat/completions",
        request_body=body,
        is_stream=False,
    )
    await db_session.commit()
    decrypted = service.decrypt_request_body(log.request_body)
    assert decrypted == body, "body must round-trip unchanged"
    assert len(decrypted) == 1500


@pytest.mark.asyncio
async def test_preview_short_body_passes_through_unchanged(db_session, test_user, monkeypatch):
    """Bodies that fit within AUDIT_LOG_PREVIEW_CHARS are returned verbatim
    in the preview field. This is a deliberate trade-off — short prompts
    are visible in list view to ease admin debugging."""
    short_body = "hello world"  # 11 chars, < 80
    service = AuditService(db_session)
    log = await service.create_pending_log(
        user=test_user,
        model="m",
        provider="p",
        path="/x",
        request_body=short_body,
        is_stream=False,
    )
    await db_session.commit()
    assert log.request_body_preview == short_body


@pytest.mark.asyncio
async def test_preview_long_body_head_ellipsis_tail(db_session, test_user, monkeypatch):
    """Bodies longer than AUDIT_LOG_PREVIEW_CHARS are rendered as
    "headN...tailM" so the middle of the prompt is never persisted in
    plaintext. Verified format: total length <= AUDIT_LOG_PREVIEW_CHARS,
    head shows start, tail shows end, the "TAIL" middle marker is
    dropped on purpose (the truncation removes it, which is the point).
    """
    from app.config import get_settings

    settings = get_settings()
    n = settings.AUDIT_LOG_PREVIEW_CHARS
    # Put a long body with a unique secret in the MIDDLE that must NOT
    # survive the preview. "TAIL" marker is at the end, after the secret.
    long_body = "A" * (n * 4) + "SECRET_MIDDLE" + "B" * 50 + "TAIL_AT_END"
    service = AuditService(db_session)
    log = await service.create_pending_log(
        user=test_user,
        model="m",
        provider="p",
        path="/x",
        request_body=long_body,
        is_stream=False,
    )
    await db_session.commit()
    preview = log.request_body_preview
    assert "..." in preview
    assert len(preview) <= n
    # Head: first N/2 chars (the "A..." leading run)
    head_len = n // 2
    assert preview.startswith("A" * head_len)
    # The unique secret in the middle is NOT in plaintext
    assert "SECRET_MIDDLE" not in preview, "middle of prompt must not be persisted"
    # Tail: last M chars include the end-of-body marker
    assert preview.endswith("TAIL_AT_END")
    # The leading "B" run is also gone (between head and tail)
    assert "B" * 50 not in preview


@pytest.mark.asyncio
async def test_full_body_false_drops_encrypted_body(db_session, test_user, monkeypatch):
    """When AUDIT_LOG_FULL_BODY=false, the encrypted body field is NULL.
    Only the preview (possibly truncated) is stored."""
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "AUDIT_LOG_FULL_BODY", False)
    service = AuditService(db_session)
    log = await service.create_pending_log(
        user=test_user,
        model="m",
        provider="p",
        path="/x",
        request_body="x" * 200,
        is_stream=False,
    )
    await db_session.commit()
    assert log.request_body is None
    assert log.request_body_preview  # non-empty (truncated or not)


@pytest.mark.asyncio
async def test_record_admin_access_writes_meta_audit(db_session, admin_user, monkeypatch):
    """When an admin views a log body, a new AuditLog row is created with
    path='/admin/audit-access' recording the action."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "AUDIT_LOG_FULL_BODY", True)

    # First create a target log as a regular user
    from app.models.audit import AuditLog

    target = AuditLog(
        user_id=admin_user.id,  # admin looking at their own log for simplicity
        username=admin_user.username,
        department=admin_user.department.name,
        model="gpt-4",
        provider="openai",
        method="POST",
        path="/v1/chat/completions",
        request_body_preview="some preview",
        is_stream=False,
        status="completed",
    )
    db_session.add(target)
    await db_session.commit()
    await db_session.refresh(target)

    # Now admin views it (meta-audit)
    service = AuditService(db_session)
    meta = await service.record_admin_access(admin_user, target, ip_address="10.0.0.1")
    await db_session.commit()

    assert meta.path == "/admin/audit-access"
    assert meta.user_id == admin_user.id
    assert meta.status == "completed"
    # Preview format: "viewed log=<first-8-hex> user=<first-8-hex> path=..."
    assert "viewed log=" in meta.request_body_preview
    assert str(target.id).split("-")[0] in meta.request_body_preview
    assert str(target.user_id).split("-")[0] in meta.request_body_preview
    assert meta.ip_address == "10.0.0.1"


@pytest.mark.asyncio
async def test_decrypt_request_body_round_trip(db_session, test_user, monkeypatch):
    """decrypt_request_body must recover the original plaintext when
    AUDIT_LOG_FULL_BODY=true."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "AUDIT_LOG_FULL_BODY", True)
    service = AuditService(db_session)
    body = "the quick brown fox jumps over the lazy dog"
    log = await service.create_pending_log(
        user=test_user,
        model="m",
        provider="p",
        path="/x",
        request_body=body,
        is_stream=False,
    )
    await db_session.commit()
    assert service.decrypt_request_body(log.request_body) == body


# ---------- PII 脱敏（spec B1/B7/B8，plan T2） ----------


@pytest.mark.asyncio
async def test_create_pending_log_redacts_when_enabled(db_session, test_user, monkeypatch):
    """ENABLE_PII_REDACTION=true：preview 与 user_agent 落库前脱敏。"""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "ENABLE_PII_REDACTION", True)
    service = AuditService(db_session)
    log = await service.create_pending_log(
        user=test_user,
        model="m",
        provider="p",
        path="/x",
        request_body="reach a@b.com or 13800138000",
        user_agent="cool-agent/1.0 dev@corp.com",
        is_stream=False,
    )
    await db_session.commit()
    assert log.request_body_preview == "reach [EMAIL] or [PHONE]"
    assert log.user_agent == "cool-agent/1.0 [EMAIL]"


@pytest.mark.asyncio
async def test_create_pending_log_disabled_default_passthrough(db_session, test_user, monkeypatch):
    """CRITICAL 回归：未开启（显式默认 false）时 preview/user_agent 与原实现一致。"""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "ENABLE_PII_REDACTION", False)
    service = AuditService(db_session)
    body = "reach a@b.com or 13800138000"
    ua = "Mozilla/5.0 (Windows NT 10.0)"
    log = await service.create_pending_log(
        user=test_user,
        model="m",
        provider="p",
        path="/x",
        request_body=body,
        user_agent=ua,
        is_stream=False,
    )
    await db_session.commit()
    assert log.request_body_preview == body
    assert log.user_agent == ua


@pytest.mark.asyncio
async def test_create_pending_log_redacts_encrypted_full_body(db_session, test_user, monkeypatch):
    """ENABLE=true + FULL_BODY=true：解密后的 full body 也是掩码，无原文。"""
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "ENABLE_PII_REDACTION", True)
    monkeypatch.setattr(s, "AUDIT_LOG_FULL_BODY", True)
    service = AuditService(db_session)
    log = await service.create_pending_log(
        user=test_user,
        model="m",
        provider="p",
        path="/x",
        request_body="mail a@b.com",
        is_stream=False,
    )
    await db_session.commit()
    decrypted = service.decrypt_request_body(log.request_body)
    assert "a@b.com" not in decrypted
    assert "[EMAIL]" in decrypted


@pytest.mark.asyncio
async def test_create_pending_log_user_agent_none_ok(db_session, test_user, monkeypatch):
    """ENABLE=true + user_agent=None（Chat 流式形态）：保持 None、不崩。"""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "ENABLE_PII_REDACTION", True)
    service = AuditService(db_session)
    log = await service.create_pending_log(
        user=test_user,
        model="m",
        provider="p",
        path="/x",
        request_body="plain",
        user_agent=None,
        is_stream=False,
    )
    await db_session.commit()
    assert log.user_agent is None


@pytest.mark.asyncio
async def test_record_completion_redacts_error_message_when_enabled(
    db_session, test_user, monkeypatch
):
    """ENABLE=true：record_completion 的 error_message 先截断再脱敏。"""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "ENABLE_PII_REDACTION", True)
    service = AuditService(db_session)
    log = await service.create_pending_log(
        user=test_user,
        model="m",
        provider="p",
        path="/x",
        request_body=None,
        is_stream=False,
    )
    await db_session.commit()
    await service.record_completion(log, status_code=500, error_message="contact a@b.com")
    await db_session.commit()
    assert log.error_message == "contact [EMAIL]"
