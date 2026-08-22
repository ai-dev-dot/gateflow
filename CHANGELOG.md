# Changelog

所有重要变更记录在此。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Fixed
- **PII 脱敏 EMAIL 超长 local-part 部分匹配**（QA 对抗测试发现）：e1c016a 的性能前瞻 `(?=[...]{1,64}@)` 只约束起始位置 0，re 从位置 1 重试时前瞻仍成功，致 `a*65 + @x.com` 被掩成 `a[EMAIL]`（首字符明文残留 + 掩码语义错误）。加 negative lookbehind `(?<![A-Za-z0-9._%+-])` 挡住从 local-part 字符中间起始的重试，超长 local-part 整串不命中（宁漏不误伤，RFC 5321 上限 64）；+回归测试（64 边界 / 65、70 超长 / 前置分隔符后合法邮箱）

## [0.3.1] - 2026-08-22

### Fixed
- **PII 脱敏 EMAIL 规则线性化**（对抗评审发现）：无 `@` 的长串（纯数字/字母块、base64）上 EMAIL 无界量词逐位置回溯致 O(n²)，2KB 最坏 ~14ms 阻塞事件循环。加有界前瞻后实测 2KB -> ~1.3ms、4/8/16KB 近似线性。默认开关关闭不受影响；+回归测试

## [0.3.0] - 2026-08-22

### Added
- **审计链路 PII 脱敏**（`ENABLE_PII_REDACTION` 生效，spec v0.2.0 承诺落地）：开启后，LLM 请求体（明文 preview 与 `AUDIT_LOG_FULL_BODY=true` 时的加密 full body）、失败原因 `error_message`、客户端 `User-Agent` 在落库前做启发式 regex 打码，邮箱/手机号/身份证/银行卡/AWS Key/API Key 掩码为 `[EMAIL]` 等类别占位。新增 `app/services/redactor.py`（纯函数、零依赖、单次交替正则 + 校验位/Luhn 并集门、幂等）；**fail-open**（redact 异常只记 log + 计入 `gateflow_pii_redact_failure_total`，绝不阻断审计写入）；默认关闭 = 零行为变化；范围限定审计链路（不覆盖 Chat 对话记录表、备份、服务器日志，界外为显式决策）；修改配置需重启
- README / `.env.example` / `spec v0.2.0` 文档同步：`ENABLE_PII_REDACTION` 语义与 `AUDIT_LOG_FULL_BODY` 释义勘误（原写"true 不存 body"，实为"true=加密存 full body"）
- **P2-1 补齐 router HTTP 测试**：新增 10 个 router 的 HTTP 覆盖（auth / users / api_keys / provider_keys / gateway_forward / usage / chat / agent_types / model_configs / backup）+ `tests/routers/conftest.py` 共享 `client`/`as_user`/`as_auth_context` 夹具；全套 183 -> 263 tests（backlog P2-1 收口 DONE）
- **P1-9 SQLite 盲区收口**：新增 `tests/models/test_audit_index.py`，锁定审计 pending partial index（PG-only）的 `postgresql_where` 模型声明；SQLite 无法执行 PG 运行时行为的盲区显式声明接受——生产 PG 由 Alembic baseline 迁移承载（backlog P1-9 收口 DONE）

## [0.2.0] - 2026-08-22

### Added
- **Admin 数据库备份页面**（`/backup`）：通过 `pg_dump` 异步导出数据库为 `.sql` 文件，配置（备份目录 + 是否包含 LLM 调用日志）存单行 `system_config` 表，admin 改完无需重启立刻生效。默认不备份 `audit_logs` 数据（仅保留 schema）。四端点：`GET/PUT /api/backup/config`、`POST /api/backup/run`、`GET /api/backup/history`
- Anthropic 兼容协议支持（`POST /v1/messages`）+ Provider Adapter 架构
- 按客户端类型（agent_type）维度统计用量
- 用量趋势端点 `GET /api/usage/trend`
- 审计日志详情端点 `GET /api/audit/logs/{id}`
- 普通用户用量看板（独立接口、页面、菜单）
- 部门管理（增删改）
- 修改密码功能
- 用户端 API Key 管理页面
- Chat 页流式输出（打字机效果）
- 后端 Chat 流式输出端点
- 用量统计页 Tabs 切换（模型 / 部门 / 用户 / 客户端）
- Provider API Key 池按可用 key 自动故障转移
- **加密基础设施**：Fernet 对称加密 + HMAC-SHA256 工具（`utils/crypto.py` / `utils/hashing.py`）
- **每请求 UUID** 中间件（`utils/request_id.py`），跨日志关联，响应头回显 `X-Request-ID`
- **启动 fail-fast 检查**（`utils/startup_checks.py`）：JWT_SECRET_KEY 占位符 / 长度校验
- **错误响应安全工具**（`utils/errors.py`）：固定文案 + request_id，不泄露内部异常
- **P2-6 僵尸 pending 审计日志定时清理**：lifespan 后台任务每 24h 扫描（启动后延迟 5 分钟首轮），把超过 1h 仍 pending 的审计日志标为 failed 并在 `error_message` 标注 stale（复用 P1-8 字段），防止 `ix_audit_logs_status_pending` partial index 膨胀；单轮失败仅记日志不中断循环；进程退出时任务随之取消
- **P1-3 认证热路径写优化**：API Key 认证不再每个请求 UPDATE+commit `last_used_at`（QPS 高时的 DB 锁竞争瓶颈），改为记内存 set，lifespan 后台任务每 30s 批量 flush 一次（单条 `UPDATE ... WHERE id IN (...)`）；flush 失败保留 buffer 下轮重试；进程优雅关闭时做最后一次 flush，崩溃最多丢 30s 的 `last_used_at`（非合规关键字段，可接受）
- **P2-8 可观测性**：`GET /metrics` 端点（Prometheus 文本格式；`prometheus-fastapi-instrumentator` 提供**路由模板归一化**的 HTTP 指标，`METRICS_ENABLED=false` 可整体关闭）；业务指标三组--`gateflow_llm_call_total{model,provider,status}`、`gateflow_llm_latency_seconds{model,provider}`（buckets 100ms-300s）、`gateflow_audit_log_write_total{status}`（含 P2-6 stale 清理计数），埋点收敛在 audit 完成态的两处公共出口（`record_completion` / `_save_after_stream`），三条转发路径自动全覆盖，label 取自 AuditLog 快照（与审计口径一致）；结构化日志 `LOG_FORMAT=text|json`（默认 text 控制台可读；json 模式单行输出含 `timestamp/level/logger/request_id/message`，request_id 由 logging Filter 从 ContextVar 注入、业务代码零改动，与 `X-Request-ID` 响应头、错误响应三方对齐）；`httpx` 日志降级 WARNING。接入方式：Prometheus 抓取任务指向 `/metrics` 即可（业务指标均以 `gateflow_` 前缀命名）
- **审计日志保留期清理落地**（spec v0.2.0 承诺，此前 `AUDIT_LOG_RETENTION_DAYS` 是死配置）：超过保留期（默认 90 天，`<=0` 永久保留）的审计日志由后台任务每 24h 分批 DELETE（每批 1000 行 id 子查询，避免大表长事务锁表）；循环与 P2-6 合并为 `audit_maintenance_loop`（原 `stale_pending_cleanup_loop` 改名）；新指标 `gateflow_audit_log_deleted_total`。**注意**：用量统计基于 AuditLog 实时聚合，保留期外的数据删除后统计窗口同步缩短（保留期的应有语义）；需要历史的先做备份（admin 备份页可选包含 audit_logs 数据）

### Changed
- **P2-5 启用 Alembic 迁移**：schema 演进由 Alembic 接管，移除 lifespan 里的 `Base.metadata.create_all` 和 `system_config.ensure_columns`（手动 ALTER TABLE）临时方案。`start.bat`/`start.sh` 启动时自动 `alembic upgrade head`；baseline 迁移含全部 11 张表 + partial index（`ix_audit_logs_status_pending` 的 `postgresql_where` 正确落入迁移）。改 schema 流程：`alembic revision --autogenerate -m "..."` → review → `alembic upgrade head`
- **P2-7 消除 `datetime.utcnow()` 弃用警告**：抽 `app/utils/datetime_utils.utcnow()`（naive UTC，行为与旧 API 完全一致）替换全部调用点（middleware / services / models），消除 Python 3.12+ DeprecationWarning
- **P1-8 失败原因落审计日志**：`AuditLog` 新增 `error_message` 字段（Text，截断 500 字符，Alembic 迁移 `a58c2f0bdc97`）。三条转发路径的失败原因全部落库：非流式网关（上游错误 body / ReadTimeout / 异常 repr）、流式路径（`StreamForwarder` 的上游非 200 / 超时 / 异常三个分支）、Anthropic 非流式 bridge。`GET /api/audit/logs` 及详情接口在失败记录上返回该字段；审计页面失败 tag hover 显示错误摘要。仅内部诊断信息（不含凭据），永不回显给 LLM 客户端（P0-4 边界不变）
- **重命名**：`app/routers/gateway.py` → `app/routers/model_configs.py`（路径前缀 `/api/gateway/models` 不变），消除与 `gateway_forward.py` 的命名混淆
- **DRY auth_middleware**：抽 `_resolve_credentials(credentials, db) -> (User, api_key_id, agent_type)` 共享 helper，`get_current_user` / `get_auth_context` 都基于它。**附带修复**：JWT 路径用显式 `UUID(sub)` 转换，SQLite 测试环境也能跑通（之前依赖 PG 隐式转换）
- **DRY Anthropic bridge**：`StreamForwarder.forward()` 加 `transform_chunk`（bytes→bytes）和 `error_sse`（client-protocol 错误格式）钩子；非流式路径加公共 `save_after_stream()` 方法。`anthropic_forward.py` 的 80 行 `bridge_stream` 闭包删除，改为 `forwarder.forward(... transform_chunk=AnthropicBridgeTransformer(), error_sse=anthropic.error_sse)`，不再调用 `_save_after_stream` 私有方法
- **DRY usage_service**：抽 `_build_summary_query(dimension_field, *, include_username, group_by_extra, filters)` 静态方法，`get_summary` 4 个维度（user/department/model/api_key）从 4 段 14 行 select 缩成 4 个 4 行调用
- **DRY token 估算**：抽 `app/utils/tokens.estimate_tokens(messages)` 工具（CHARS_PER_TOKEN=3 启发式），替换 chat_service / gateway_service / anthropic_forward 三处重复实现。**附带改进**：Anthropic content block 列表（`[{"type":"text","text":"..."}]`）现在被正确求和，旧 inline 版本用 `str(content)` 强制转换会把 `None`/`int` 等错误类型估成 1 token
- README 重写为「核心能力 + 技术栈」风格
- 用量统计改为从 AuditLog 实时聚合，删除 `UsageStat` 聚合表
- 审计日志新增 `api_key_name` 快照字段，所有统计维度（user / department / api_key）均基于 audit_log 快照聚合，保证历史不可变
- **API Key 存储**：`key` 明文 → `key_hash`（HMAC-SHA256）+ `key_prefix`（明文前 11 字符）。`APIKeyResponse` 只返 `key_prefix`；`APIKeyCreated` 一次性返完整明文
- **ProviderAPIKey 存储**：`key` 明文 → `encrypted_key`（Fernet 密文）+ `key_preview`（前 4 + ... + 后 4）。`ProviderKeyResponse` 只返 `key_preview`
- **审计日志 body**：`request_body` 明文 → Fernet 加密（条件写入，由 `AUDIT_LOG_FULL_BODY` 控制）；新增 `request_body_preview`（前 80 字符，短 body 完整、超长 head40...tail37 截断）
- **审计日志访问控制**：`GET /api/audit/logs/{id}` 默认不返回 body；`?include_body=true` 仅 admin 可用，每次访问写 meta-audit（路径 `/admin/audit-access`）
- **CORS**：从硬编码 `localhost:3000` 改为读 `ALLOWED_ORIGINS` 环境变量
- **DB engine**：`pool_size=20 / max_overflow=20 / pool_pre_ping=True / pool_recycle=1800s`（可通过 env 调整）
- **异常处理**：6 处 `str(e)` 透传给客户端的代码改为 `logger.error` + 固定文案 + request_id

### Fixed
- 修复前后端分页格式不一致
- 修复 Chat 页面模型字段和对话创建

### Changed (governance)
- 仓库改为 read-only 开源模式：代码公开但**不接收 PR**
- 删除 `.github/dependabot.yml`（自动 PR 也算外部贡献）
- 重写 `CONTRIBUTING.md`：明确说明单人维护、不接受代码贡献
- `README.md` 贡献指南段改为指向新 CONTRIBUTING.md 的简短说明
- `SECURITY.md` 软化响应时间承诺（无 SLA），补充 Provider API Key 加密 + audit log body 加密两条已知安全考量

### Removed
- **P1-7 死代码**：`AuditService.MAX_LOG_CONTENT_LENGTH` (100KB) 字段 + `create_pending_log` 内部截断逻辑删除。所有 call site (gateway_service / chat_service / anthropic_forward) 调用前已经 `[:2000]` 截到 2KB，100KB 内部上限永远到不了

### Changed (perf)
- **P1-4 chat N+1**：`ChatService.get_messages` 改契约为 `list[Message] | None`——`None` 表示 404（对话不存在或非本人），`[]` 表示对话存在但为空。Router 不再为了消歧而全量拉 `get_conversations()`，404 路径从 O(N) 降到 1 次索引查询
- **P1-5 chat 历史 LIMIT 50**：抽 `ChatService._get_capped_history()` helper，`send_message` / `send_message_stream` 都通过它拉历史。**策略**：system 消息全保留，user/assistant 只取最近 50 条按时间正序。1000 条消息的对话从 1MB 文本降到 ≤50 条（按 4KB/条估 = 200KB），保护 LLM context window 和 DB 读开销
- **P1-6 chat 失败回滚**：`send_message` 把 user_message 改 `flush` 而非 `commit`，让 user + AI message 共享一个事务，LLM 失败时 user_message 也回滚；`send_message_stream` 因 on_complete 跑在新 session，结构上必须先 commit user_message，改在 `on_complete` hook 里 `status_code != 200 or not full_content` 时删除 orphan user_message。两种路径都不再留"用户发了问题但 AI 没回复"的悬空记录
- 修复 auth 中间件懒加载与 chat/gateway 流式保存的 race condition（AI 消息"消失"问题）
- 修复测试发现的 7 个 bug

### Security
- **P0-1**：启动时检测 JWT_SECRET_KEY 是否为占位符 / 长度 < 32 字符，发现即 fail-fast
- **P0-2**：上游 API Key 与客户端 API Key 不再明文存储，list 接口不再返回明文完整 Key
- **P0-3**：审计日志完整 body 不再明文持久化；默认不通过 API 返回；admin 访问 body 每次留痕
- **P0-4**：6 处 `str(e)` / `response.text` 透传给客户端的代码已修复，避免泄露内部异常细节
- **P0-5**：DB 连接池显式配置（防 stale 连接 + 突发流量耗尽）；CORS allow_origins 改为环境变量（避免生产锁死 localhost）
