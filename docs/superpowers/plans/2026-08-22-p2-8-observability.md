# P2-8 可观测性：Prometheus metrics + 结构化日志

> 日期：2026-08-22
> 状态：已完成（业务指标 8 测试全过；真实 uvicorn 冒烟：/metrics 含 3 业务指标 + HTTP 指标，LOG_FORMAT=json 单行 JSON 含 request_id，X-Request-ID 请求->响应头贯通）
> 上游：`2026-06-05-gateflow-p1-p2-backlog.md` P2-8（request_id 部分已于 P0-4 完成）
> 目标：补齐 metrics 端点、业务指标、结构化日志三块，生产部署前的可观测性缺口

## 背景

P2-8 原始问题：

- 无 `/metrics` 端点（Prometheus 拉数据用）
- `logger.error` 散落各处，无统一 JSON 格式
- 无 trace_id 串联 DB / HTTP / LLM 调用（request_id ContextVar 已有，但未注入日志）
- 无 audit log 之外的成功率 / P99 latency 指标

已有基础（复用，不重复造）：

- `utils/request_id.py`：每请求 UUID + ContextVar（`current_request_id`）+ 响应头回显
- `AuditLog` 表本身是持久化的调用日志（本次指标从快照字段取 label，与 DB 口径一致）

## 方案决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| HTTP 指标 + /metrics | `prometheus-fastapi-instrumentator` | 路由模板归一化（`/api/audit/logs/{id}` 不按具体 id 炸维度）、并发安全这些坑包已解决；自写易错 |
| 业务指标 | `prometheus-client` Counter/Histogram | instrumentator 自带依赖 |
| 结构化日志 | `python-json-logger` + 自定义 Filter 注入 request_id | 成熟小包；Filter 读 ContextVar，业务代码零改动 |
| 日志格式开关 | `LOG_FORMAT=text|json`（默认 text） | 开发可读、生产 JSON，.env 切换无需改码 |
| metrics 开关 | `METRICS_ENABLED`（默认 true） | 内网部署默认开；关掉即端点消失 |
| /metrics 认证 | 不做 | Prometheus 拉取惯例是裸端点；公网部署应放反向代理后面（README 注明） |

## 业务指标设计

backlog 指定的三组，埋点收敛在 audit 完成态更新的两处公共出口（非流式走
`AuditService.record_completion`，流式走 `StreamForwarder._save_after_stream`），
三条转发路径（OpenAI 网关 / Anthropic 透传 / bridge）自动全覆盖：

| 指标 | 类型 | Labels | 埋点位置 |
|------|------|--------|----------|
| `gateflow_llm_call_total` | Counter | model, provider, status(completed/failed) | 上述两处 |
| `gateflow_llm_latency_seconds` | Histogram | model, provider（buckets 覆盖 100ms-300s） | 同上 |
| `gateflow_audit_log_write_total` | Counter | status | 同上（含 pending 清理的 stale 标记） |

label 值取自 AuditLog 快照字段（model/provider），与审计口径一致。
label 基数风险：model/provider 是管理员配置的有限集合，无用户输入，安全。

## 任务分解

1. 依赖：`prometheus-fastapi-instrumentator` + `python-json-logger` 入 requirements
2. `app/utils/metrics.py`：三个业务指标 + `observe_llm_call()` helper
3. 埋点：`AuditService.record_completion` + `StreamForwarder._save_after_stream`
4. `app/utils/logging_config.py`：`setup_logging(LOG_FORMAT)` + RequestIdFilter
5. `main.py`：Instrumentator 接入（METRICS_ENABLED 开关）+ lifespan 第一行 setup_logging
6. 测试：/metrics 端点、业务指标计数、JSON 日志含 request_id、text 模式回归
7. 文档：CHANGELOG + backlog 标 DONE

## 验收标准

1. `GET /metrics` 返回 Prometheus 文本格式，含 http 请求指标 + 三个业务指标
2. 一次成功转发后 `gateflow_llm_call_total{status="completed"}` +1
3. `LOG_FORMAT=json` 时日志为单行 JSON 且含 request_id 字段；默认 text 不变
4. 全套测试通过，ruff 无新警告
5. CHANGELOG + backlog 更新，单独 commit 引用 P2-8
