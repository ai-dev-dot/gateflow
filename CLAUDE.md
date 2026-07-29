# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

GateFlow（闸机）是企业 AI 网关 —— 所有大模型调用的统一入口，提供访问控制、成本管理、LLM 调用日志和协议转换。

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
| Alembic | 1.18.4 | 数据库迁移（已启用，alembic/versions/） |

**前端（CDN 引入，无构建链）：**
- Tailwind CSS v4 — 样式
- htmx 2.0 — 交互增强
- ECharts 5 — 图表

### 数据库

PostgreSQL（asyncpg 驱动），连接串在 `.env`，参考 `.env.example`。

## 常用命令

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000   # 启动开发服务器
```

表结构由 **Alembic** 管理：`start.bat` / `start.sh` 启动时自动跑 `alembic upgrade head`（首次建表 + 增量迁移）；启动时自动 seed 管理员账号和 AgentType 默认值。手动迁移：`python -m alembic upgrade head`；改 schema 后生成迁移：`python -m alembic revision --autogenerate -m "描述"`（生成后需人工 review 产物）。

```bash
python -m pytest tests/ -v     # 运行所有测试
python -m pytest tests/ -v -k "test_openai"  # 运行匹配的测试
```

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
├── tests/                  # pytest 测试
├── requirements.txt
├── start.bat               # 一键启动
└── .env                    # 配置（不入 git）
```

### 请求路径

**路径 A：OpenAI 兼容网关** — `POST /v1/chat/completions`（`routers/gateway_forward.py` → `GatewayService`）
**路径 B：Anthropic 兼容网关** — `POST /v1/messages`（`routers/anthropic_forward.py` → `GatewayService`）
**路径 C：Chat 应用** — `POST /api/chat/conversations/{id}/messages/stream`（`routers/chat.py` → `ChatService`）
**路径 D：管理页面** — `GET /pages/*`（`routers/pages.py` → Jinja2 模板渲染）

路径 A/B/C 是 JSON API，路径 D 是 HTML 页面。

### Provider Adapter 模式

`services/provider_adapters/` 使用策略模式隔离不同 LLM 提供商的协议差异：

- `BaseAdapter` — 抽象基类
- `OpenAIAdapter` — OpenAI 兼容协议（默认回退）
- `AnthropicAdapter` — Anthropic Messages API 协议

### 认证

**API 认证**（`/api/*`、`/v1/*`）：`middleware/auth_middleware.py`
- `get_current_user()` — JWT Token + `gf_` 前缀 API Key 双模认证
- `get_auth_context()` — 含 `user`、`api_key_id`、`agent_type`

**页面认证**（`/pages/*`）：`middleware/session.py`
- httpOnly cookie 存储 JWT
- `get_current_user_from_cookie()` / `require_admin_from_cookie()`

### 关键数据流（网关路径）

```
客户端 → 认证中间件 → Router → GatewayService
  → ProviderKeyService.get_available_key()
  → Adapter.build_upstream_url/headers/body
  → httpx.stream/post 转发到上游
  → Adapter.parse_stream_event/extract_response
  → 后台任务更新 AuditLog
```

### 模型层

所有模型使用 UUID 主键 + `TimestampMixin`（`created_at`/`updated_at`）。核心模型：

- `ModelConfig` — 模型路由表（alias → provider + target_model + target_url）
- `ProviderAPIKey` — 上游 API Key 池（Fernet 加密落库）
- `APIKey` — 客户端 API Key（`gf_` 前缀，HMAC-SHA256 哈希）
- `AgentType` — 客户端类型枚举
- `AuditLog` — LLM 调用日志（request_body Fernet 加密）
- `SystemConfig` — 运行时配置单例表（备份目录等）

### 页面模板

页面使用 Jinja2 模板 + htmx + Tailwind CSS，通过 `/api/*` 获取数据，vanilla JS 渲染。

- `base.html` — 布局骨架（侧边栏 + 顶栏 + 内容区）
- `_components.html` — Jinja2 宏（tag、stat_card、card、btn 等）
- `static/js/chat.js` — 聊天流式交互（fetch ReadableStream）
- `static/js/charts.js` — ECharts 初始化

## 注意事项

- 默认管理员：`admin` / `admin123`
- API Key 以 `gf_` 开头，认证中间件通过前缀区分 Key 和 JWT
- 流式响应是透传模式：网关逐块转发，流结束后异步更新日志和统计
- **启动 fail-fast**：`utils/startup_checks.py` 在 lifespan 第一行执行
- **API Key 创建**：`POST /api/api-keys` 返回完整明文（只此一次）；`GET /api/api-keys` 只返 `key_prefix`
- **审计日志 body**：`GET /api/audit/logs` 永远不含 body；`?include_body=true` 仅 admin 可用
- **CORS**：`ALLOWED_ORIGINS` 环境变量控制
- **启动脚本**：`start.bat` / `start.sh` 先跑 `alembic upgrade head` 再启动 uvicorn（单进程单端口）
