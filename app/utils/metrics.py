"""业务指标定义与埋点 helper（P2-8）。

三组指标（backlog P2-8 指定），全部埋在 audit 完成态更新的公共出口，
三条转发路径（OpenAI 网关 / Anthropic 透传 / bridge）自动全覆盖：

- ``gateflow_llm_call_total{model, provider, status}`` 调用结果计数
- ``gateflow_llm_latency_seconds{model, provider}`` 上游延迟直方图
- ``gateflow_audit_log_write_total{status}`` 审计行落库计数（含 P2-6
  清理产生的 stale 标记）

label 值取自 AuditLog 快照字段（model/provider 为管理员配置的有限集合，
无用户输入，基数安全）。HTTP 层指标由 prometheus-fastapi-instrumentator
自动提供（路由模板归一化），此处不重复。

计数器与进程同生命周期，重启清零--长期历史看 AuditLog 表 / Prometheus。
"""

from prometheus_client import Counter, Histogram

LLM_CALLS = Counter(
    "gateflow_llm_call_total",
    "LLM calls forwarded through the gateway, by model/provider/outcome",
    ["model", "provider", "status"],
)

LLM_LATENCY = Histogram(
    "gateflow_llm_latency_seconds",
    "Upstream LLM call latency (covers the full forward, incl. streaming tail)",
    ["model", "provider"],
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
)

AUDIT_WRITES = Counter(
    "gateflow_audit_log_write_total",
    "Audit log rows reaching a final status (incl. stale cleanup)",
    ["status"],
)

AUDIT_DELETED = Counter(
    "gateflow_audit_log_deleted_total",
    "Audit log rows deleted by the retention task (AUDIT_LOG_RETENTION_DAYS)",
)

PII_REDACT_FAILURES = Counter(
    "gateflow_pii_redact_failure_total",
    "PII redaction failures (fail-open: original text stored instead)",
)


def observe_audit_deletion(count: int) -> None:
    """Record rows deleted by the retention task."""
    if count:
        AUDIT_DELETED.inc(count)


def observe_llm_call(model: str, provider: str, status: str, latency_ms: int | None) -> None:
    """Record one finalized LLM call.

    Called from AuditService.record_completion (non-stream paths) and
    StreamForwarder._save_after_stream (stream paths) - the two places an
    AuditLog row reaches its terminal status.
    """
    LLM_CALLS.labels(model=model, provider=provider or "-", status=status).inc()
    AUDIT_WRITES.labels(status=status).inc()
    if latency_ms is not None:
        LLM_LATENCY.labels(model=model, provider=provider or "-").observe(latency_ms / 1000)


def observe_stale_cleanup(count: int) -> None:
    """Record rows marked stale by the P2-6 cleanup task (no latency, no model)."""
    if count:
        AUDIT_WRITES.labels(status="stale").inc(count)


def observe_pii_redact_failure() -> None:
    """Record one PII redaction failure (fail-open path, spec B8 / plan P6)."""
    PII_REDACT_FAILURES.inc()
