"""Core gateway service that forwards requests to upstream LLM providers.

Uses a BaseAdapter for protocol differences and StreamForwarder for the
common stream transport / audit / stats pipeline.
"""

import json
import logging
import time

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.api_key import APIKey
from app.models.audit import AuditLog
from app.models.gateway import ModelConfig
from app.models.user import User
from app.services.audit_service import AuditService
from app.services.provider_adapters.base import BaseAdapter
from app.services.provider_key_service import ProviderKeyService
from app.services.stream_forwarder import StreamForwarder
from app.utils.http_client import get_http_client
from app.utils.tokens import estimate_tokens

logger = logging.getLogger(__name__)


class GatewayService:
    """Core gateway service. See StreamForwarder for the streaming pipeline."""

    def __init__(self, db: AsyncSession, adapter: BaseAdapter):
        self.db = db
        self.adapter = adapter

    async def forward_request(
        self,
        user: User,
        model_config: ModelConfig,
        request_body: dict,
        is_stream: bool,
        request: Request,
        path: str = "/v1/chat/completions",
        api_key_id: str | None = None,
        agent_type: str | None = None,
    ):
        """
        1. Get an available provider key
        2. Create a pending audit log
        3. Forward to upstream (stream or non-stream)
        4. StreamForwarder updates audit log + key stats at end
        """
        key_service = ProviderKeyService(self.db)
        provider_key = await key_service.get_available_key(model_config.provider)

        if not provider_key:
            raise GatewayError(
                status_code=503,
                detail=f"No available API key for provider: {model_config.provider}",
            )

        # 快照：client api key 的 name（请求发生时的状态，之后不改）
        api_key_name = None
        if api_key_id:
            api_key_name = await self.db.scalar(select(APIKey.name).where(APIKey.id == api_key_id))

        # Estimate request tokens from full body
        request_tokens = self._estimate_request_tokens(request_body)

        # Create pending audit log (via AuditService)
        audit_service = AuditService(self.db)
        audit_log = await audit_service.create_pending_log(
            user=user,
            model=request_body.get("model", model_config.model_alias),
            provider=model_config.provider,
            path=path,
            request_body=json.dumps(request_body, ensure_ascii=False)[:2000],
            is_stream=is_stream,
            api_key_id=api_key_id,
            api_key_name=api_key_name,
            agent_type=agent_type,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:500],
        )
        await self.db.commit()
        await self.db.refresh(audit_log)

        # Build upstream request via adapter
        upstream_url = self.adapter.build_upstream_url(model_config.target_url)
        upstream_headers = self.adapter.build_headers(provider_key.get_decrypted_key())
        forward_body = self.adapter.build_request_body(
            request_body,
            model_config.target_model,
            {
                "temperature": model_config.default_temperature,
                "max_tokens": model_config.default_max_tokens,
            },
        )

        if is_stream:
            forwarder = StreamForwarder(self.db, self.adapter)
            # Passthrough mode: emit_sse=None yields raw bytes (preserves usage events)
            return await forwarder.forward(
                upstream_url=upstream_url,
                upstream_headers=upstream_headers,
                forward_body=forward_body,
                audit_log=audit_log,
                provider_key_id=provider_key.id,
                request_tokens=request_tokens,
            )
        else:
            return await self._handle_non_stream(
                upstream_url=upstream_url,
                upstream_headers=upstream_headers,
                forward_body=forward_body,
                audit_log=audit_log,
                provider_key_id=provider_key.id,
                request_tokens=request_tokens,
            )

    async def _handle_non_stream(
        self,
        *,
        upstream_url: str,
        upstream_headers: dict,
        forward_body: dict,
        audit_log,
        provider_key_id,
        request_tokens: int,
    ):
        """Non-streaming: wait for full response, then update audit + stats.

        For non-streaming, we don't need StreamForwarder's SSE pipeline.
        We replicate the audit + key stats persistence pattern (new session)
        to keep behavior consistent with the streaming path.
        """
        adapter = self.adapter
        client = await get_http_client()
        start_time = time.monotonic()
        status_code = 200
        response_tokens = 0
        response_body = {}
        error_message: str | None = None

        try:
            response = await client.post(
                upstream_url,
                headers=upstream_headers,
                json=forward_body,
                timeout=httpx.Timeout(300.0),
            )
            status_code = response.status_code
            response_body = response.json()
            if status_code == 200:
                _, _, response_tokens = adapter.extract_response(response_body)
            else:
                logger.warning(f"Upstream error {status_code}: {response.text[:500]}")
                error_message = response.text[:500]
        except httpx.ReadTimeout:
            logger.error("Upstream read timeout")
            status_code = 504
            response_body = adapter.format_error(504, {"detail": "Upstream read timeout"})
            error_message = "Upstream read timeout"
        except Exception as e:
            # P0-4: never leak str(exception) to client.
            from app.utils.errors import get_request_id_safe

            rid = get_request_id_safe()
            logger.error(f"[{rid}] Request error: {e!r}", exc_info=True)
            status_code = 500
            response_body = adapter.format_error(
                500,
                {"detail": "Internal error", "request_id": rid},
            )
            # P1-8: persist the exception detail in the audit log (internal
            # diagnostics only, never echoed to the client).
            error_message = repr(e)[:500]

        latency_ms = int((time.monotonic() - start_time) * 1000)

        # Persist audit + key stats in new session (same pattern as StreamForwarder)
        try:
            async with async_session() as db:
                result = await db.execute(select(AuditLog).where(AuditLog.id == audit_log.id))
                log = result.scalar_one_or_none()
                if log:
                    audit_service = AuditService(db)
                    await audit_service.record_completion(
                        log, status_code, request_tokens, response_tokens, latency_ms,
                        error_message=error_message,
                    )
                key_service = ProviderKeyService(db)
                if status_code == 200:
                    await key_service.update_key_success(
                        provider_key_id, request_tokens, response_tokens
                    )
                else:
                    await key_service.update_key_error(provider_key_id, status_code)
                await db.commit()
        except Exception as e:
            logger.error(f"Non-stream post-update failed: {e}", exc_info=True)

        if status_code != 200:
            return JSONResponse(status_code=status_code, content=response_body)
        return response_body

    @staticmethod
    def _estimate_request_tokens(request_body: dict) -> int:
        """Rough estimation of input tokens from request body.

        Assumes ~3 characters per token (mix of English/Chinese, conservative).
        This is a rough estimate; actual usage comes from upstream's usage field.
        """
        return estimate_tokens(request_body.get("messages", []))


class GatewayError(Exception):
    """Custom exception for gateway errors."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)
