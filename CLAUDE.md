# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

GateFlow（闸机）是企业 AI 网关 -- 所有大模型调用的统一入口，提供访问控制、成本管理、LLM 调用日志和协议转换。

## 开发流程（SDD，必读）

所有开发遵循 **规范驱动开发（Spec-Driven Development）**：

- 文档在 `docs/superpowers/`（`specs/` 设计稿 + `plans/` 实现计划/backlog），文档是 single source of truth
- **开工前**先核对 `docs/superpowers/plans/2026-06-05-gateflow-p1-p2-backlog.md` 进度表与 git log/代码实际状态，发现过时先同步
- **新功能先写 plan 文档**（`docs/superpowers/plans/YYYY-MM-DD-<topic>.md`，头部标注日期/状态/决策表）再动手
- **每完成一项任务**：更新 backlog 进度表（状态/commit hash/备注）+ `CHANGELOG.md` Unreleased 段落，随代码一起提交；commit message 引用任务编号（如 `fix(perf): P1-4 chat N+1`）
- 验收标准（见 backlog 文末）：全套测试绿色、`ruff` 无新警告、涉及 HTTP 的真实 uvicorn + curl 验证

**文档特殊字符陷阱**：仓库文档/注释里，表格占位符和"破折号"是 em dash（U+2014，非 ASCII `-`）、CHANGELOG 箭头是 `->`（U+2192，非 ASCII `->`）。Edit 工具的 old_string 匹配失败时，先用 `python -c "print(repr(line))"` 查实际码点，或用 python 做行级替换。

## 技术栈

| 依赖 | 版本 | 用途 |
|------|------|------|
| FastAPI | 0.136.3 | Web 框架 |
| Uvicorn | 0.48.0 | ASGI 服务器 |
| SQLAlchemy[asyncio] | 2.0.50 | ORM（async 模式） |
| asyncpg | 0.31.0 | PostgreSQL 异步驱动 |
| Pydantic | 2.13.4 | 数据校验 |
| pydantic-settings | 2.14.1 | 配置管理（.env） |
| httpx | 0.28.1 | 异步 HTTP 客户端（转发请求到上游 LLM） |
| python-jose[cryptography] | 3.5.0 | JWT 签发/验证 |
| passlib[bcrypt] + bcrypt | 1.7.4 / 4.0.1 | 密码哈希 |
| cryptography | (最新) | Fernet 对称加密 + HMAC-SHA256 |
| Jinja2 | 3.1.6 | HTML 模板引擎（管理页面） |
| Alembic | 1.18.4 | 数据库迁移（alembic/versions/） |
| prometheus-fastapi-instrumentator | 8.1.0 | /metrics 端点 + HTTP 指标（路由模板归一化） |
| python-json-logger | 4.2.0 | 结构化日志（LOG_FORMAT=json） |

**前端（CDN 引入，无构建链）：**
- Tailwind CSS v4 - 样式
- htmx 2.0 - 交互增强
- ECharts 5 - 图表

### 数据库

PostgreSQL（asyncpg 驱动），连接串在 `.env`，参考 `.env.example`。

## 常用命令

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000   # 启动开发服务器
python -m pytest tests/ -v     # 全套测试（186 个）
python -m pytest tests/ -v -k "test_cleanup"  # 运行匹配的测试
python -m ruff check app/ tests/  # lint（验收标准：无新警告；历史遗留 13 个在 session.py/pages.py/backup_service.py/env.py）
```

表结构由 **Alembic** 管理：`start.bat` / `start.sh` 启动时自动跑 `alembic upgrade head`（首次建表 + 增量迁移）；启动时自动 seed 管理员账号和 AgentType 默认值。手动迁移：`python -m alembic upgrade head`；改 schema 后生成迁移：`python -m alembic revision --autogenerate -m "描述"`（生成后需人工 review 产物，message 用 ASCII 避免 Windows zh-CN 编码问题）。

管理页面访问：`http://localhost:8000/pages/login`

## 架构

### 项目结构

```
D:\APP\GateFlow\
├── app/
│   ├── templates/          # Jinja2 HTML 模板
│   ├── static/             # CSS/JS（Tailwind + htmx + ECharts CDN）
│   ├── routers/            # API 路由 + 页面路由
│   ├── services/           # 业务逻辑
│   ├── models/             # SQLAlchemy 模型
│   ├── middleware/          # 认证（JWT + cookie session）
│   ├── schemas/            # Pydantic schema
│   └── main.py             # FastAPI 应用入口
├── tests/                  # pytest 测试（SQLite in-memory + StaticPool）
├── requirements.txt
├── start.bat               # 一键启动
└── .env                    # 配置（不入 git）
```

### 请求路径

**路径 A：OpenAI 兼容网关** - `POST /v1/chat/completions`（`routers/gateway_forward.py` -> `GatewayService`）
**路径 B：Anthropic 兼容网关** - `POST /v1/messages`（`routers/anthropic_forward.py` -> `GatewayService` 或 StreamForwarder bridge）
**路径 C：Chat 应用** - `POST /api/chat/conversations/{id}/messages/stream`（`routers/chat.py` -> `ChatService`）
**路径 D：管理页面** - `GET /pages/*`（`routers/pages.py` -> Jinja2 模板渲染）

路径 A/B/C 是 JSON API，路径 D 是 HTML 页面。三条 LLM 转发路径的错误处理与统计落库收敛在两个公共出口：`AuditService.record_completion`（非流式）与 `StreamForwarder._save_after_stream`（流式）--改 audit/统计/指标行为时动这两处，不要在 router 里另写。

### Provider Adapter 模式

`services/provider_adapters/` 使用策略模式隔离不同 LLM 提供商的协议差异：

- `BaseAdapter` - 抽象基类
- `OpenAIAdapter` - OpenAI 兼容协议（默认回退）
- `AnthropicAdapter` - Anthropic Messages API 协议 + `AnthropicBridgeTransformer`（OpenAI<->Anthropic SSE 字节级转换）

### 认证

**API 认证**（`/api/*`、`/v1/*`）：`middleware/auth_middleware.py`
- `get_current_user()` - JWT Token + `gf_` 前缀 API Key 双模认证
- `get_auth_context()` - 含 `user`、`api_key_id`、`agent_type`
- 两者共享 `_resolve_credentials()` helper
- **`last_used_at` 不在请求路径写库**（P1-3）：API Key 认证只往内存 buffer 记 id，lifespan 后台任务每 30s 批量 flush；进程优雅关闭时最后冲一次。测试断言"认证后立即更新 last_used_at"会失败，应断言 buffer 或手动调 `flush_last_used_buffer()`

**页面认证**（`/pages/*`）：`middleware/session.py`
- httpOnly cookie 存储 JWT
- `get_current_user_from_cookie()` / `require_admin_from_cookie()`

### lifespan 后台任务（app/main.py）

两个 `asyncio.create_task`，关闭时 cancel + 收尾：

- `audit_maintenance_loop`（`services/cleanup_service.py`，每 24h，首轮延迟 5 分钟）：① 超 1h 仍 pending 的审计日志标 failed + `error_message` 写 stale 标记（P2-6）；② 按 `AUDIT_LOG_RETENTION_DAYS`（默认 90，`<=0` 永久保留）分批删除过期日志（每批 1000 行 id 子查询）
- `last_used_flush_loop`（`middleware/auth_middleware.py`，每 30s）：见上节 P1-3

### 可观测性（P2-8）

- `GET /metrics`（Prometheus 文本格式，`METRICS_ENABLED=false` 可关；公网部署需反向代理保护）
- 业务指标在 `utils/metrics.py`：`gateflow_llm_call_total{model,provider,status}`、`gateflow_llm_latency_seconds`、`gateflow_audit_log_write_total{status}`（含 stale）、`gateflow_audit_log_deleted_total`；埋点在上述两个公共出口 + cleanup_service，label 取自 AuditLog 快照
- 日志 `LOG_FORMAT=text|json`（默认 text）：`utils/logging_config.py` 的 `RequestIdFilter` 从 ContextVar 注入 request_id 到每条日志；request_id 与 `X-Request-ID` 响应头、错误响应三方对齐

### 关键数据流（网关路径）

```
客户端 -> 认证中间件 -> Router -> GatewayService
  -> ProviderKeyService.get_available_key()
  -> Adapter.build_upstream_url/headers/body
  -> httpx.stream/post 转发到上游
  -> Adapter.parse_stream_event/extract_response
  -> 后台任务更新 AuditLog
```

### 模型层

所有模型使用 UUID 主键 + `TimestampMixin`（`created_at`/`updated_at`）。核心模型：

- `ModelConfig` - 模型路由表（alias -> provider + target_model + target_url）
- `ProviderAPIKey` - 上游 API Key 池（Fernet 加密落库）
- `APIKey` - 客户端 API Key（`gf_` 前缀，HMAC-SHA256 哈希）
- `AgentType` - 客户端类型枚举
- `AuditLog` - LLM 调用日志（request_body Fernet 加密；`error_message` 存失败原因，截断 500 字符，含上游错误 body / 异常 repr / stale 标记）
- `SystemConfig` - 运行时配置单例表（备份目录等）

时间戳统一用 `app/utils/datetime_utils.utcnow()`（`datetime.utcnow()` 已弃用，勿再直接调用）。

### 页面模板

页面使用 Jinja2 模板 + htmx + Tailwind CSS，通过 `/api/*` 获取数据，vanilla JS 渲染。

- `base.html` - 布局骨架（侧边栏 + 顶栏 + 内容区）
- `_components.html` - Jinja2 宏（tag、stat_card、card、btn 等）
- `static/js/chat.js` - 聊天流式交互（fetch ReadableStream）
- `static/js/charts.js` - ECharts 初始化

## 注意事项

- 默认管理员：`admin` / `admin123`
- API Key 以 `gf_` 开头，认证中间件通过前缀区分 Key 和 JWT
- 流式响应是透传模式：网关逐块转发，流结束后异步更新日志和统计
- **启动 fail-fast**：`utils/startup_checks.py` 在 lifespan 第一行执行（P2-8 的 setup_logging 在其之前）
- **API Key 创建**：`POST /api/api-keys` 返回完整明文（只此一次）；`GET /api/api-keys` 只返 `key_prefix`
- **审计日志 body**：`GET /api/audit/logs` 永远不含 body；`?include_body=true` 仅 admin 可用
- **用量统计**：基于 AuditLog 实时聚合，统计窗口随 `AUDIT_LOG_RETENTION_DAYS` 同步缩短（保留期外的数据被删除；需要历史先做备份）
- **CORS**：`ALLOWED_ORIGINS` 环境变量控制
- **启动脚本**：`start.bat` / `start.sh` 先跑 `alembic upgrade head` 再启动 uvicorn（单进程单端口）
- `ENABLE_PII_REDACTION` 配置存在但**未实现**（spec v0.2.0 承诺，需 Presidio 或轻量 regex 方案，.env.example 已标注"当前不生效"）
