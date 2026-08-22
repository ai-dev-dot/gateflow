"""Anthropic Messages API gateway endpoint.

提供原生 Anthropic 兼容端点 POST /v1/messages，让 Claude Code 等 Anthropic 原生
客户端可以直接用 GateFlow。

当上游是 OpenAI 兼容 provider 时（如 DeepSeek），gateway 自动在协议之间翻译。
所有路径通过 StreamForwarder 抽象走统一审计 + 统计 + 错误处理。
"""

import json
import logging
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth_middleware import AuthContext, get_auth_context
from app.models.gateway import ModelConfig
from app.services.audit_service import AuditService
from app.services.gateway_service import GatewayError, GatewayService
from app.services.provider_adapters import get_adapter
from app.services.provider_adapters.anthropic_adapter import (
    AnthropicAdapter,
    AnthropicBridgeTransformer,
)
from app.services.provider_key_service import ProviderKeyService
from app.services.stream_forwarder import StreamForwarder
from app.utils.errors import get_request_id_safe
from app.utils.http_client import get_http_client
from app.utils.tokens import estimate_tokens

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Anthropic Gateway"])


@router.post("/messages")
async def messages(
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """
    Anthropic Messages API endpoint.

    - 上游是 Anthropic：直接透传
    - 上游是 OpenAI 兼容：协议翻译（request + response / stream）
    """
    try:
        body = await request.json()
    except Exception as err:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from err

    model_alias = body.get("model")
    if not model_alias:
        raise HTTPException(status_code=400, detail="Missing required field: model")

    messages = body.get("messages")
    if not messages or not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="Missing or invalid field: messages")

    if not body.get("max_tokens"):
        raise HTTPException(status_code=400, detail="Missing required field: max_tokens")

    is_stream = body.get("stream", False)

    # Find model config
    result = await db.execute(
        select(ModelConfig).where(
            ModelConfig.model_alias == model_alias,
            ModelConfig.is_active == True,
        )
    )
    model_config = result.scalar_one_or_none()

    if not model_config:
        raise HTTPException(status_code=404, detail=f"Model not found or inactive: {model_alias}")

    upstream_adapter = get_adapter(model_config.provider)

    if upstream_adapter.provider_name == "anthropic":
        # Upstream is Anthropic: pass through directly via GatewayService
        gateway_service = GatewayService(db, upstream_adapter)
        try:
            return await gateway_service.forward_request(
                user=auth.user,
                model_config=model_config,
                request_body=body,
                is_stream=is_stream,
                request=request,
                path="/v1/messages",
                api_key_id=auth.api_key_id,
                agent_type=auth.agent_type,
            )
        except GatewayError as e:
            return JSONResponse(
                status_code=e.status_code,
                content=upstream_adapter.format_error(e.status_code, {"detail": e.detail}),
            )

    # ----- OpenAI-compatible upstream: bridge the protocol -----
    # Both streaming and non-streaming paths go through StreamForwarder
    # so audit log is written for ALL Anthropic→OpenAI bridging (P0-3 fix).
    anthropic = AnthropicAdapter()
    openai_body = anthropic.to_openai_request(body, model_config.target_model)
    openai_body["stream"] = is_stream

    key_service = ProviderKeyService(db)
    provider_key = await key_service.get_available_key(model_config.provider)
    if not provider_key:
        return JSONResponse(
            status_code=503,
            content=anthropic.format_error(503, {"detail": "No available API key"}),
        )

    upstream_url = upstream_adapter.build_upstream_url(model_config.target_url)
    upstream_headers = upstream_adapter.build_headers(provider_key.get_decrypted_key())
    forward_body = upstream_adapter.build_request_body(
        openai_body,
        model_config.target_model,
        {
            "temperature": model_config.default_temperature,
            "max_tokens": model_config.default_max_tokens,
        },
    )

    # Estimate tokens (use full message body)
    request_tokens = estimate_tokens(messages)

    # Create pending audit log via AuditService (this is the P0-3 fix:
    # previously this path wrote NO audit log)
    audit_service = AuditService(db)
    audit_log = await audit_service.create_pending_log(
        user=auth.user,
        model=model_config.model_alias,
        provider=model_config.provider,
        path="/v1/messages",
        request_body=json.dumps(body, ensure_ascii=False)[:2000],
        is_stream=is_stream,
        api_key_id=auth.api_key_id,
        api_key_name=None,  # could look up same as gateway
        agent_type=auth.agent_type,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
    )
    await db.commit()
    await db.refresh(audit_log)

    # P1-2 refactor: the streaming and non-streaming bridges now both reuse
    # StreamForwarder (via the public save_after_stream entry point) instead
    # of duplicating transport/audit/stats logic and calling the private
    # _save_after_stream. The byte→byte SSE bridge is encapsulated in
    # AnthropicBridgeTransformer and plugged in as a transform_chunk hook.
    forwarder = StreamForwarder(db, upstream_adapter)

    if is_stream:
        # Streaming bridge: OpenAI SSE → Anthropic SSE via stateful transformer.
        return await forwarder.forward(
            upstream_url=upstream_url,
            upstream_headers=upstream_headers,
            forward_body=forward_body,
            audit_log=audit_log,
            provider_key_id=provider_key.id,
            request_tokens=request_tokens,
            transform_chunk=AnthropicBridgeTransformer(),
            error_sse=anthropic.error_sse,
        )

    # ----- Non-streaming bridge: one-shot POST + transform response -----
    return await _bridge_non_streaming(
        forwarder=forwarder,
        anthropic=anthropic,
        upstream_url=upstream_url,
        upstream_headers=upstream_headers,
        forward_body=forward_body,
        audit_log_id=audit_log.id,
        provider_key_id=provider_key.id,
        request_tokens=request_tokens,
    )


async def _bridge_non_streaming(
    *,
    forwarder: StreamForwarder,
    anthropic: AnthropicAdapter,
    upstream_url: str,
    upstream_headers: dict,
    forward_body: dict,
    audit_log_id: UUID,
    provider_key_id: UUID,
    request_tokens: int,
) -> JSONResponse | dict:
    """One-shot POST against an OpenAI-compatible upstream, convert the
    response to Anthropic format, and persist audit + provider key stats
    via the public ``StreamForwarder.save_after_stream`` entry point.
    """
    try:
        client = await get_http_client()
        response = await client.post(
            upstream_url,
            headers=upstream_headers,
            json=forward_body,
            timeout=httpx.Timeout(300.0),
        )
    except Exception as e:
        # P0-4: never leak str(exception) to client.
        rid = get_request_id_safe()
        logger.error(f"[{rid}] Anthropic bridge error: {e!r}", exc_info=True)
        await forwarder.save_after_stream(
            audit_log_id=audit_log_id,
            provider_key_id=provider_key_id,
            status_code=500,
            request_tokens=request_tokens,
            error_message=repr(e)[:500],
        )
        return JSONResponse(
            status_code=500,
            content=anthropic.format_error(500, {"detail": "Internal error", "request_id": rid}),
        )

    if response.status_code == 200:
        openai_response = response.json()
        usage = openai_response.get("usage", {})
        await forwarder.save_after_stream(
            audit_log_id=audit_log_id,
            provider_key_id=provider_key_id,
            status_code=200,
            request_tokens=request_tokens,
            response_tokens=usage.get("completion_tokens", 0),
            input_tokens=usage.get("prompt_tokens", 0),
            latency_ms=0,
        )
        return anthropic.from_openai_response(openai_response)

    # Upstream error: persist failure stats and return a fixed Anthropic error.
    # P0-4: don't echo upstream's raw error text to the client (logged
    # server-side instead).
    await forwarder.save_after_stream(
        audit_log_id=audit_log_id,
        provider_key_id=provider_key_id,
        status_code=response.status_code,
        request_tokens=request_tokens,
        error_message=response.text[:500],
    )
    return JSONResponse(
        status_code=response.status_code,
        content=anthropic.format_error(
            response.status_code,
            {"detail": f"Upstream returned {response.status_code}"},
        ),
    )
