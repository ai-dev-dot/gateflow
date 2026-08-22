"""Stream forwarding abstraction for LLM upstream calls.

Consolidates the three previously-duplicated streaming paths:
- Gateway: pass-through OpenAI SSE (no transformation)
- Chat: parse + transform to OpenAI SSE for frontend
- Anthropic bridge: parse + transform OpenAI → Anthropic SSE

This module owns:
- HTTP transport (httpx.stream with timeout)
- SSE line buffering and event parsing via adapter
- Upstream error mapping (4xx/5xx → adapter.error_sse)
- ReadTimeout / Exception handling
- Audit log creation (pending) and completion update
- Provider key statistics update

Callers customize via optional hooks:
- emit_sse(event) -> str:  how to serialize each event to client
- transform_chunk(bytes) -> bytes: byte-level protocol bridge (e.g. OpenAI→Anthropic SSE)
- error_sse(message, type) -> str: client-protocol error formatter (defaults to upstream)
- on_complete(db, content, status_code): post-stream persistence (e.g. save AI message)

流结束后的统计更新使用新 session（不污染原请求事务）。
"""

import logging
import time
from collections.abc import Awaitable, Callable
from uuid import UUID

import httpx
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.services.provider_adapters.base import BaseAdapter, StreamEvent
from app.services.provider_key_service import ProviderKeyService
from app.utils.datetime_utils import utcnow
from app.utils.http_client import get_http_client

logger = logging.getLogger(__name__)

# Default timeout for streaming LLM calls. 5 min matches upstream typical limits.
STREAM_TIMEOUT = httpx.Timeout(300.0, read=300.0)

# Hook signatures
EmitSSE = Callable[[StreamEvent], str]
# Returns: SSE string to send to client. Return empty string to skip.

TransformChunk = Callable[[bytes], bytes]
# Stateful byte→byte transformer applied to each upstream chunk before
# yielding in passthrough mode. Used by protocol bridges (e.g. Anthropic→OpenAI)
# to convert SSE formats on the fly. The transformer is invoked with the
# ORIGINAL upstream bytes; token stats are captured from the original chunk.

ErrorSSE = Callable[[str, str], str]
# (message, error_type) → SSE string for client. Defaults to adapter.error_sse
# in the upstream protocol; bridges pass a client-protocol formatter here.

OnComplete = Callable[[AsyncSession, str, int], Awaitable[None]]
# Called once after stream ends (success or failure). Args: db, full_content, status_code.
# Used by Chat/bridge paths to persist AI message.


class StreamForwarder:
    """Forward a streaming LLM request to upstream, stream to client, persist stats.

    Single entry point: `forward()` returns a `StreamingResponse` ready to be
    returned from a FastAPI route. All three stream paths in this app go through here.

    ┌────────────────────────────────────────────────────────────────┐
    │                       StreamForwarder                          │
    │                                                                │
    │  ┌─────────────┐    ┌──────────────┐    ┌──────────────────┐  │
    │  │ client.stream│ →  │ parse_stream_│ →  │ emit_sse(event)?  │  │
    │  │  POST URL   │    │   event()    │    │ yield to client   │  │
    │ └─────────────┘    └──────────────┘    └──────────────────┘  │
    │         │                    │                     │            │
    │         ↓                    ↓                     ↓            │
    │   upstream errors     input_tokens/         passthrough /     │
    │   → error_sse         output_tokens         transformed       │
    │                                                                │
    │  finally:                                                     │
    │  - new session → update audit log                              │
    │  - update provider key stats                                   │
    │  - on_complete(db, full_content, status_code)  [if provided]   │
    └────────────────────────────────────────────────────────────────┘
    """

    def __init__(
        self,
        db: AsyncSession,
        adapter: BaseAdapter,
        session_factory=None,
    ):
        """Initialize the forwarder.

        Args:
            db: The request-scoped session (used for adapter-related ops).
            adapter: Provider protocol adapter.
            session_factory: Optional async session factory for the post-stream
                save (new session). Defaults to app.database.async_session.
                Tests inject a factory bound to a test engine.
        """
        self.db = db
        self.adapter = adapter
        self._session_factory = session_factory

    async def forward(
        self,
        *,
        upstream_url: str,
        upstream_headers: dict,
        forward_body: dict,
        audit_log: AuditLog,
        provider_key_id: UUID,
        request_tokens: int,
        emit_sse: EmitSSE | None = None,
        transform_chunk: TransformChunk | None = None,
        error_sse: ErrorSSE | None = None,
        accumulate_text: bool = False,
        on_complete: OnComplete | None = None,
    ) -> StreamingResponse:
        """Stream upstream to client. Persist audit + key stats when done.

        Args:
            upstream_url: full upstream endpoint (built by adapter)
            upstream_headers: includes auth (built by adapter)
            forward_body: request body to forward (built by adapter)
            audit_log: an already-persisted pending AuditLog row (must have .id)
            provider_key_id: ProviderAPIKey.id for stats attribution
            request_tokens: pre-estimated input token count
            emit_sse: if None, raw bytes are passed through (gateway path).
                      If set, called for each event; return SSE string to send.
            transform_chunk: optional stateful byte→byte transformer applied to
                             each upstream chunk before yielding in passthrough
                             mode. Used by protocol bridges (e.g. Anthropic
                             client + OpenAI upstream) to convert SSE bytes
                             on the fly. Token stats are still parsed from the
                             ORIGINAL chunk using the upstream adapter.
            error_sse: optional (message, error_type) → SSE string. Defaults
                       to ``adapter.error_sse`` (upstream protocol). Bridges
                       pass a client-protocol formatter so error events use
                       the client's expected format.
            accumulate_text: if True, build full_content from event.text
                             (for Chat/bridge saving AI message).
            on_complete: async hook called after stream with (db, full_content, status_code).
                         Used by Chat/bridge to persist AI message in same transaction as stats.

        Returns:
            FastAPI StreamingResponse ready to return from a route.
        """
        adapter = self.adapter
        # Resolve error SSE formatter: caller-supplied or upstream-protocol default.
        error_formatter = error_sse if error_sse is not None else adapter.error_sse
        start_time = time.monotonic()

        async def stream_generator():
            full_content = ""
            response_tokens = 0
            input_tokens = 0
            status_code = 200
            error_message: str | None = None
            buffer_lines: list[str] = []
            client = await get_http_client()

            try:
                async with client.stream(
                    "POST",
                    upstream_url,
                    headers=upstream_headers,
                    json=forward_body,
                    timeout=STREAM_TIMEOUT,
                ) as upstream_response:
                    status_code = upstream_response.status_code

                    if status_code != 200:
                        # Drain error body for logging, then yield error SSE
                        error_body = b""
                        async for chunk in upstream_response.aiter_bytes():
                            error_body += chunk
                        error_text = error_body.decode("utf-8", errors="replace")
                        logger.warning(f"Upstream error {status_code}: {error_text[:500]}")
                        error_message = error_text[:500]
                        yield error_formatter(f"Upstream returned {status_code}", "upstream_error")
                        return

                    async for chunk in upstream_response.aiter_bytes():
                        # Parse ORIGINAL chunk for stats (uses upstream adapter).
                        # Token counts must come from the upstream protocol, not
                        # any client-protocol transform applied below.
                        chunk_text = chunk.decode("utf-8", errors="replace")
                        for line in chunk_text.split("\n"):
                            line = line.strip()
                            if not line:
                                if buffer_lines:
                                    event = adapter.parse_stream_event(buffer_lines)
                                    if event:
                                        if event.input_tokens:
                                            input_tokens = event.input_tokens
                                        if event.output_tokens:
                                            response_tokens = event.output_tokens
                                        # Fallback: count text chunks as rough token estimate
                                        if (
                                            emit_sse is None
                                            and transform_chunk is None
                                            and event.text
                                            and not event.output_tokens
                                        ):
                                            response_tokens += 1
                                        if accumulate_text and event.text:
                                            full_content += event.text
                                        if emit_sse is not None:
                                            sse = emit_sse(event)
                                            if sse:
                                                yield sse
                                    buffer_lines = []
                                continue
                            buffer_lines.append(line)

                        # Apply client-protocol byte transform (e.g. OpenAI→Anthropic SSE)
                        # AFTER stats are captured from the original chunk.
                        if transform_chunk is not None:
                            chunk = transform_chunk(chunk)

                        # Raw passthrough mode: yield bytes directly to client.
                        if emit_sse is None:
                            yield chunk

            except httpx.ReadTimeout:
                logger.error("Upstream read timeout")
                yield error_formatter("Upstream read timeout", "timeout")
                status_code = 504
                error_message = "Upstream read timeout"
            except Exception as e:
                # P0-4: never leak str(exception) to client. Log full
                # context (with request_id) and yield a fixed message
                # SSE event that includes the request_id for support
                # correlation.
                from app.utils.errors import get_request_id_safe

                rid = get_request_id_safe()
                logger.error(f"[{rid}] Stream error: {e!r}", exc_info=True)
                yield error_formatter(
                    f"Internal error (request_id: {rid})",
                    "internal_error",
                )
                status_code = 500
                error_message = repr(e)[:500]
            finally:
                latency_ms = int((time.monotonic() - start_time) * 1000)
                await self._save_after_stream(
                    audit_log_id=audit_log.id,
                    provider_key_id=provider_key_id,
                    status_code=status_code,
                    request_tokens=request_tokens,
                    response_tokens=response_tokens,
                    input_tokens=input_tokens,
                    latency_ms=latency_ms,
                    full_content=full_content,
                    on_complete=on_complete,
                    error_message=error_message,
                )

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def save_after_stream(
        self,
        *,
        audit_log_id: UUID,
        provider_key_id: UUID,
        status_code: int,
        request_tokens: int,
        response_tokens: int = 0,
        input_tokens: int = 0,
        latency_ms: int = 0,
        full_content: str = "",
        on_complete: OnComplete | None = None,
        error_message: str | None = None,
    ) -> None:
        """Public alias of :meth:`_save_after_stream` for callers outside the
        streaming generator (e.g. one-shot POST bridges).

        Updates the audit log status, runs the optional on_complete hook, and
        bumps provider key statistics in a fresh session so the work survives
        caller cancellation.
        """
        await self._save_after_stream(
            audit_log_id=audit_log_id,
            provider_key_id=provider_key_id,
            status_code=status_code,
            request_tokens=request_tokens,
            response_tokens=response_tokens,
            input_tokens=input_tokens,
            latency_ms=latency_ms,
            full_content=full_content,
            on_complete=on_complete,
            error_message=error_message,
        )

    async def _save_after_stream(
        self,
        *,
        audit_log_id: UUID,
        provider_key_id: UUID,
        status_code: int,
        request_tokens: int,
        response_tokens: int,
        input_tokens: int,
        latency_ms: int,
        full_content: str,
        on_complete: OnComplete | None,
        error_message: str | None = None,
    ) -> None:
        """Update audit log + key stats in a fresh session.

        Uses `async_session()` (new session) so this work happens independently of
        the caller's request-scoped transaction. If the caller's request is
        cancelled, the stats still get persisted (we use a new session here).
        """
        try:
            # Use injected session_factory if provided (tests); else global.
            factory = self._session_factory
            if factory is None:
                from app.database import async_session as _default_factory

                factory = _default_factory

            async with factory() as db:
                result = await db.execute(select(AuditLog).where(AuditLog.id == audit_log_id))
                audit_log = result.scalar_one_or_none()
                if audit_log:
                    audit_log.status = "completed" if status_code == 200 else "failed"
                    audit_log.status_code = status_code
                    audit_log.request_tokens = request_tokens
                    audit_log.response_tokens = response_tokens
                    audit_log.total_tokens = request_tokens + response_tokens
                    audit_log.latency_ms = latency_ms
                    audit_log.completed_at = utcnow()
                    audit_log.error_message = error_message[:500] if error_message else None

                # Caller hook (e.g. save AI message + conversation title)
                if on_complete is not None:
                    await on_complete(db, full_content, status_code)

                # Provider key stats
                key_service = ProviderKeyService(db)
                if status_code == 200:
                    await key_service.update_key_success(
                        provider_key_id, request_tokens, response_tokens
                    )
                else:
                    await key_service.update_key_error(provider_key_id, status_code)

                await db.commit()
        except Exception as e:
            logger.error(f"Post-stream save failed: {e}", exc_info=True)
