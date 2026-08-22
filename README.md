# 闸机 GateFlow・企业 AI 网关

让企业AI，安全有序地流动

闸机 GateFlow 是企业内部所有大模型调用的统一入口。
它帮你管住数据泄露风险，控住AI使用成本，理清所有使用行为，
让全公司从"个人偷偷用AI"，变成"全公司放心用AI"。

## 企业AI落地的5大灾难

这是所有企业现在正在经历的真实痛点，也是闸机诞生的原因：

1.  **数据泄露爆炸**：员工把客户名单、合同、代码、财务数据直接粘贴到公网大模型，核心资产随时可能泄露且完全不可追溯
2.  **成本完全失控**：各部门各自买账号、各自充钱，一年花几百万不知道谁用了、用在哪了、产生了什么价值
3.  **管理一片混乱**：没有统一标准，有人用GPT-4o，有人用DeepSeek，有人用本地模型；不同部门知识库不互通，重复建设
4.  **合规审计空白**：金融、医疗、政务等强监管行业，AI生成内容无法审计、无法溯源，出问题企业和管理者承担法律责任
5.  **集成效率极低**：每个业务系统都要单独对接不同大模型API，重复开发，维护成本极高；模型切换时所有系统都要改代码

闸机 GateFlow，一次性解决以上所有问题。

## 用了闸机，你的团队怎么用 AI？

### 场景一：直接和 AI 对话

> 公司员工打开闸机网页，就能直接和 AI 聊天。不需要懂 API，不需要注册公网账号。所有交互全程记录，企业数据可管可控可追溯。

```
┌──────────────────────────────────────────────────────┐
│  闸机 AI 助手                     [deepseek-chat ▼]  │
├──────────────────────────────────────────────────────┤
│                                                      │
│  🤖 你好！我是闸机 AI 助手，有什么可以帮你的？       │
│                                                      │
│  👤 帮我写一封给客户的邮件，关于产品延期交付的事     │
│                                                      │
│  🤖 好的，以下是邮件草稿：                           │
│     尊敬的客户您好：                                 │
│     感谢您一直以来的支持。由于近期需求量增加...      │
│                                                      │
├──────────────────────────────────────────────────────┤
│  [输入框]                                      [发送] │
└──────────────────────────────────────────────────────┘
```

### 场景二：AI Agent 对接

> 技术团队在 Claude Code、Codex、Dify、Coze、LangChain、Cursor 等工具里，把 API 地址指向闸机，就能让 Agent 走企业统一通道。一行配置，零代码改动。

```python
# 在任何支持 OpenAI 格式的工具里，只需改两行配置
base_url = "http://your-gateflow:8000/v1"   # 指向闸机
api_key  = "gf_your_enterprise_token"        # 闸机发的 Token

# 其他代码完全不用改，Dify/Coze/LangChain/Cursor 均兼容
```

### 场景三：业务系统集成

> 企业的 CRM、ERP、工单系统、代码审查工具……任何系统需要调用 AI，都通过闸机统一入口。一套 API，所有系统通用。

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  CRM 系统   │     │  ERP 系统   │     │  代码审查   │
│  自动生成    │     │  自动分析   │     │  AI 辅助    │
│  客户邮件    │     │  报表数据   │     │  Code Review│
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       └───────────────────┼───────────────────┘
                           │
                           ▼
                 ┌─────────────────┐
                 │   闸机 GateFlow  │
                 │  统一 API 入口   │
                 └────────┬────────┘
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
         DeepSeek      小米 MiMo    更多模型...
```

> 所有调用都经过闸机，自动记录谁用了、用了多少、什么时候用的。企业管理者随时可查。

---

## 核心能力

- **统一 API 网关**：OpenAI / Anthropic 兼容协议，上游 Key 池自动故障转移
- **Web AI 对话**：多模型、流式打字机效果、历史会话保存
- **全量调用审计**：所有 LLM 调用完整记录，失败原因可查，事后可追溯
- **多维度用量统计**：按用户、部门、模型、客户端实时聚合
- **双模身份认证**：JWT 与 API Key 并存，支持部门和角色
- **可观测性**：`/metrics` Prometheus 指标（HTTP + LLM 调用量/延迟），JSON 结构化日志，调用日志保留期可配

## 数据存储与隐私

我们对待 LLM 调用日志的态度：**默认最小化，访问受控，留痕可审计。**

| 存储内容 | 加密 | 谁能看到 |
|----------|------|----------|
| 调用 metadata（模型/tokens/耗时/用户/部门/IP） | 否 | 用户自己（自己的日志）+ admin（全量） |
| Prompt 短预览（≤80 字符完整；超长 head40...tail37 截断） | 否 | 同上（用于 debug 时快速看上下文） |
| 完整 Prompt 全文 | **Fernet 加密** | **只有 admin 显式带 `?include_body=true` 才返**，且每次访问写 meta-audit |
| 完整 Response 全文 | **Fernet 加密** | 同上 |
| 失败原因（error_message，≤500 字符：上游错误摘要/异常摘要） | 否 | 用户自己 + admin（排障用，不含凭据） |

**日志保留期**：调用日志默认保留 90 天，超期由后台任务自动清理（`AUDIT_LOG_RETENTION_DAYS` 可调，`0` 或负数表示永久保留）。需要长期历史请先用管理后台的备份功能导出。

**Admin 访问完整 body 会被记录**：`/admin/audit-access` 路径专门记录"admin 何时查看了谁的日志"，方便后续审计与告警。

**参考业界头部做法**：OpenAI Enterprise、Anthropic Console、Cloudflare AI Gateway、AWS Bedrock 都默认不存 body 或做受控访问。GateFlow 选"存加密 + 受控访问"是因为企业内部 debug 经常需要完整上下文。

**API Key 与上游 Key**：以 Fernet 加密（上游 Key）+ HMAC 哈希（API Key）形式存储，**任何 list 接口都不返回明文**。完整 Key 仅在创建时一次性返回。

## 技术栈

| 依赖 | 版本 | 用途 |
|------|------|------|
| FastAPI | 0.136.3 | Web 框架 |
| Uvicorn | 0.48.0 | ASGI 服务器 |
| SQLAlchemy[asyncio] | 2.0.50 | ORM（async 模式） |
| asyncpg | 0.31.0 | PostgreSQL 异步驱动 |
| Pydantic | 2.13.4 | 数据校验 |
| httpx | 0.28.1 | 异步 HTTP 客户端 |
| python-jose[cryptography] | 3.5.0 | JWT 签发/验证 |
| passlib[bcrypt] | 1.7.4 | 密码哈希 |
| cryptography | — | Fernet 加密 + HMAC-SHA256 |
| Jinja2 | 3.1.6 | HTML 模板引擎 |
| Alembic | 1.18.4 | 数据库迁移 |
| prometheus-fastapi-instrumentator | 8.1.0 | `/metrics` Prometheus 指标端点 |
| prometheus-client | 0.26.0 | 业务指标 Counter/Histogram |
| python-json-logger | 4.2.0 | 结构化日志（`LOG_FORMAT=json`） |
| Tailwind CSS v4 | CDN | 样式 |
| htmx 2.0 | CDN | 交互增强 |
| ECharts 5 | CDN | 图表 |

## 快速开始

### 前置环境

- Python 3.13+
- PostgreSQL 14+（运行中）

---

### macOS / Linux

```bash
# 1. 克隆并配置
git clone https://github.com/ai-dev-dot/gateflow.git
cd GateFlow
cp .env.example .env
# 编辑 .env，至少需要填 DATABASE_URL、JWT_SECRET_KEY、ENCRYPTION_KEY、HMAC_SECRET

# 2. 安装依赖（仅首次）
bash setup.sh

# 3. 启动服务
bash start.sh
```

### Windows

```cmd
REM 在 cmd.exe 中运行
git clone https://github.com/ai-dev-dot/gateflow.git
cd GateFlow
copy .env.example .env
REM 编辑 .env

setup.bat
start.bat
```

---

`start.sh` / `start.bat` 启动时会自动运行数据库迁移（`alembic upgrade head`），首次启动即建表，后续增量迁移也自动执行。

启动后访问：`http://localhost:8000`

- 未登录自动跳转到登录页
- 默认管理员账号：`admin` / `admin123`（首次登录后请改密）
- API 文档：`http://localhost:8000/docs`

## 项目结构

```
GateFlow/
├── app/
│   ├── main.py                 # FastAPI 应用入口
│   ├── config.py               # 配置管理（.env）
│   ├── database.py             # 数据库连接
│   ├── templates_config.py     # Jinja2 模板配置
│   ├── middleware/
│   │   ├── auth_middleware.py   # API 认证（JWT + API Key + Cookie）
│   │   └── session.py          # 页面 Cookie 会话
│   ├── models/                 # SQLAlchemy 模型
│   ├── schemas/                # Pydantic schema
│   ├── routers/                # API 路由 + 页面路由
│   ├── services/               # 业务逻辑
│   │   └── provider_adapters/  # LLM 协议适配器
│   ├── templates/              # Jinja2 HTML 模板
│   ├── static/                 # CSS/JS
│   └── utils/                  # 工具函数
├── tests/                      # pytest 测试
├── .env.example                # 环境变量模板
├── requirements.txt            # Python 依赖
├── setup.bat / setup.sh        # 一键安装
├── start.bat / start.sh        # 一键启动
└── CLAUDE.md                   # AI 开发指南
```

## 系统架构

```
客户端（浏览器 / Agent / 业务系统）
        │
        ▼
┌─────────────────────────────────────┐
│         闸机 GateFlow               │
│  ┌─────────┐  ┌─────────┐  ┌──────┐│
│  │ 统一网关 │  │ 权限中心 │  │审计日志││
│  └────┬────┘  └─────────┘  └──────┘│
│       │                             │
│  ┌────┴────┐  ┌─────────┐  ┌──────┐│
│  │ Key 池  │  │ 用量统计 │  │Web 对话││
│  └─────────┘  └─────────┘  └──────┘│
└───────────────┬─────────────────────┘
                │
    ┌───────────┼───────────┐
    ▼           ▼           ▼
DeepSeek     OpenAI      更多模型...
```

## 许可证

本项目采用 MIT 许可证开源，你可以自由使用、修改和分发，包括商业用途。

如果这个项目对你有帮助，请给我们一个Star！
你的支持是我们持续迭代的最大动力。
