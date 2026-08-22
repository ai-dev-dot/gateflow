# GateFlow P1 / P2 待办清单

> 日期：2026-06-05
> 触发：2026-06-05 全量 eng review（架构 + 代码质量 + 测试 + 性能四维）
> 上游报告：`~/.gstack/projects/ai-dev-dot-GateFlow/eng-review-2026-06-05.md`
>
> **2026-06-06 更新：** 前端已从 React 迁移到 Jinja2 + htmx + Tailwind CSS。
> - `frontend/` 目录已删除，Node.js 构建链已移除
> - 前端测试相关项（P2-6 中的 vitest/RTL）已不适用
> - 新增 10 个页面路由测试（`tests/routers/test_pages.py`）
> - 项目结构已扁平化（`backend/` 嵌套消除）
> - 详见 `2026-06-06-frontend-refactor.md`

## 背景

2026-06-05 对 `main` 分支做了一次全量 eng review，发现 **5 个 P0 + 9 个 P1 + 8 个 P2** 问题。

**5 个 P0 已全部修复**（commit `31f6580` / `b66d7ea` / `ba1f890` / `4e60992`），都是安全 / 隐私相关的关键洞：
- P0-1 JWT 占位符 fail-fast
- P0-2 API Key / 上游 Key 加密落盘 + 不再明文回显
- P0-3 审计日志 body 加密 + 受控访问 + meta-audit
- P0-4 6 处 `str(e)` 透传修复
- P0-5 DB 连接池 + CORS 配置化

**P1 / P2 按优先级排序在本 backlog 跟踪。**

> **2026-08-22 状态同步**（与 git log / 代码实际状态对齐）：
> - 17 项中 **11 项 DONE、3 项部分完成、3 项待办**
> - 剩余待办：**P1-3**（认证热路径写优化）、**P1-8**（异常分支写入 audit log）、**P2-6**（僵尸 pending 清理）
> - 部分完成：**P1-9**（partial index 已正确落入 Alembic baseline 迁移，剩 SQLite 测试盲区）、**P2-1**（93 -> 161 tests，CI 已跑 pytest，router HTTP 覆盖仍不全）、**P2-8**（request_id 已做，metrics/结构化日志待做）
> - backlog 之外同期完成的功能（记录在 CHANGELOG）：admin 数据库备份（pg_dump）、暗色主题设计系统、表单 UX 优化、治理项（read-only 开源模式）
> - 1 个 P2 已在 P0-4 修复时顺带完成（无 request_id 中间件）

## 排序原则

P1 优先于 P2。P1 内排序按"影响范围 × 修复风险"：先做只读 / 解耦 / 局部修改的项（DRY、命名、估算重复），再做会动 schema / 改接口的（事务边界、partial index、retention cron），最后做工程治理项（无 Alembic、低覆盖、metrics）。

预估工作量用 AI 压缩表（CC = Claude Code / Codex + gstack 自动化；Human = 传统人工）：

| 符号 | 含义 |
|------|------|
| `CC <5m` | 5 分钟内可改完 |
| `CC <30m` | 半小时内（含测试） |
| `CC <2h` | 半天内 |
| `Human >1d` | 一天以上（含 review 磨合） |

## 修复路线（推荐执行顺序）

**Wave 1（先解耦 + 命名）**：P2-2 → P1-1 → P1-2 → P2-3 → P2-4
**Wave 2（性能 + 正确性）**：P1-4 → P1-5 → P1-6 → P1-7 → P1-3 → P1-8
**Wave 3（schema 演进 + 治理）**：P1-9 → P2-5 → P2-6 → P2-1
**Wave 4（质量提升）**：P2-7 → P2-8 → P0-5 follow-up

---

# P1 清单（9 个）

## P1-1 [DRY 严重] auth_middleware 两个函数 60+ 行复制粘贴

**位置**：`backend/app/middleware/auth_middleware.py:18-74` vs `93-152`
**严重度**：P1（高优先级，但只是代码气味）
**当前状态**：未开始

**问题**：`get_current_user` 和 `get_auth_context` 几乎一模一样（API Key 路径 + JWT 路径，重复 2 遍）。任何认证 bug 要修两处。已知 commit `7a995f4` 修过 race condition 时也只改了两处之一，留下了不一致风险。

**修复方向**：
```python
async def _resolve_credentials(token: str, db: AsyncSession) -> tuple[User, UUID|None, str|None]:
    # 共享的 gf_/JWT 分发逻辑
    ...

async def get_current_user(...):
    user, _, _ = await _resolve_credentials(token, db)
    return user

async def get_auth_context(...):
    user, api_key_id, agent_type = await _resolve_credentials(token, db)
    return AuthContext(user=user, api_key_id=api_key_id, agent_type=agent_type)
```

**工作量**：CC < 30m（含测试） / Human ~1h
**依赖**：无
**风险**：低，纯重构

---

## P1-2 [DRY 严重] Anthropic bridge 80+ 行复制 StreamForwarder + 调私有方法

**位置**：`backend/app/routers/anthropic_forward.py:160-255`（bridge_stream 函数）
**严重度**：P1（高优先级，会随 StreamForwarder 改动默默坏掉）
**当前状态**：未开始

**问题**：`bridge_stream` 函数（80+ 行）是 `StreamForwarder.forward()` 的复制粘贴版本：自己手写 httpx.stream、SSE buffer、token 捕获、错误处理、finally 调 `forwarder._save_after_stream(...)`（**下划线开头 = 私有方法被外部调用，违反 Python 约定**）。已知 P0-3 修复时把 `_save_after_stream` 复制到了 `bridge_stream` 的 finally 块里——任何对 StreamForwarder 的重构都让这里默默坏掉。

**修复方向**：让 StreamForwarder 的 `emit_sse` 钩子支持"行级转换"。具体：
- 增加 `transform_chunk: Callable[[bytes], bytes] | None` 钩子（接收原始 chunk，返回转换后 chunk）
- Anthropic bridge 用 `transform_chunk=lambda c: anthropic.from_openai_sse_chunk_stream(c)` 实现
- 移除 `bridge_stream`，改为 `forwarder.forward(... emit_sse=..., transform_chunk=...)`

如果发现 StreamForwarder 当前钩子不够灵活，先扩展 `forward()` 让它接受行级 transformer，不要再写并行实现。

**工作量**：CC < 2h（含测试） / Human ~半天
**依赖**：无（但要小心不动现有 P0-3 修过的 audit log 路径）
**风险**：中，需要重测 Anthropic bridge HTTP 端到端

---

## P1-3 [性能] 每次认证都做写 commit

**位置**：`backend/app/middleware/auth_middleware.py:42-43, 117-118`
**严重度**：P1（高 QPS 下是真实瓶颈，但当前 0 用户）
**当前状态**：**DONE**（commit `17d7dca`，2026-08-22）--采用推荐方案 A：热路径只往内存 set 记 key id，lifespan 后台任务每 30s 单条 `UPDATE ... WHERE id IN (...)` 批量落库；flush 失败保留 buffer 下轮重试；优雅关闭时最后冲一次 buffer

**问题**：
```python
api_key.last_used_at = datetime.utcnow()
await db.commit()  # ← 热路径写 DB
```

每个 API 请求都触发 1 次 UPDATE + 1 次 commit。在 QPS 100+ 时是真实瓶颈（DB 锁竞争 + 写延迟）。

**修复方向（3 选 1，按推荐度）**：

A. **内存 buffer 批量 flush**（最简单）：
```python
# 在 auth_middleware 维护一个内存 set
last_used_buffer: set[UUID] = set()

async def update_last_used(api_key_id):
    last_used_buffer.add(api_key_id)

# lifespan 后台任务每 30s flush 一次
async def flush_last_used():
    while True:
        await asyncio.sleep(30)
        async with async_session() as db:
            for kid in last_used_buffer:
                await db.execute(update(APIKey).where(APIKey.id == kid).values(last_used_at=now()))
            await db.commit()
```
**风险**：进程崩溃丢 30s 数据。`last_used_at` 不是合规关键字段，可接受。

B. **Redis 写 + DB 异步同步**——引入 Redis 依赖，复杂度高，不推荐

C. **最小修复**：把 commit 改成 session 自动管理，去掉显式 `await db.commit()`。**但** `get_db()` 用 `async with session`，close 时不自动 commit——这个修复可能无效，需要先验证。

**推荐 A**。工作量：CC < 30m

**风险**：低（最坏情况丢 30s 数据，且 `last_used_at` 字段非关键）

---

## P1-4 [性能] N+1 反模式：get_messages 空结果时多查 conversations 列表

**位置**：`backend/app/routers/chat.py:60-63`
**严重度**：P1
**当前状态**：未开始

**问题**：
```python
messages = await service.get_messages(conversation_id, current_user)
if not messages:
    conversations = await service.get_conversations(current_user)  # ← 全量拉
    conv_ids = {c.id for c in conversations}
    if conversation_id not in conv_ids:
        raise HTTPException(404)
```

空消息时再查整个用户的 conversations 列表（**全量**），构建 set 找 ID。

**修复方向**：
```python
# service 层显式返 None 表示 404
async def get_messages(self, conversation_id, user):
    exists = await self.db.scalar(
        select(Conversation.id).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user.id,
        )
    )
    if not exists:
        return None
    result = await self.db.execute(
        select(Message).where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    return result.scalars().all()

# router:
messages = await service.get_messages(conversation_id, current_user)
if messages is None:
    raise HTTPException(404)
return messages
```

**工作量**：CC < 15m
**风险**：低，纯逻辑优化

---

## P1-5 [性能] 整个对话历史无限制拉取

**位置**：`backend/app/services/chat_service.py:107-113, 215-220`
**严重度**：P1
**当前状态**：未开始

**问题**：
```python
result = await self.db.execute(
    select(Message)
    .where(Message.conversation_id == conversation_id)
    .order_by(Message.created_at)  # ← 没 LIMIT
)
history = result.scalars().all()
```

每次发送消息都把整个对话历史拉一遍。1000 条消息的对话 = 1MB Text 字段。LLM context window 也是成本。

**修复方向**：
- 短期：只拉最近 N 条（如 50 条）+ 始终包含 system 消息
- 中期（独立任务）：实现 LLM context 摘要，旧的对话消息压缩成 summary 入库

**工作量**：CC < 30m（短期版）
**风险**：低

---

## P1-6 [可靠性] chat send_message user 消息 commit 后 LLM 失败不回滚

**位置**：`backend/app/services/chat_service.py:104-115, 211-220`
**严重度**：P1
**当前状态**：未开始

**问题**：
```python
self.db.add(user_message)
await self.db.commit()  # ← 先 commit

result = await self.db.execute(...)  # 拉 history
...
ai_content, tokens = await self._call_llm(conversation.model, messages)  # ← 失败不回滚 user message
```

LLM 调用失败时，user message 已经在库，但 AI response 不在库，用户下次进对话只看到自己的问题，看不到错误信息。

**修复方向**：
```python
async with self.db.begin_nested() if self.db.in_transaction() else self.db.begin():
    self.db.add(user_message)
    await self.db.flush()
    ai_content, tokens = await self._call_llm(...)
    ai_message = Message(...)
    self.db.add(ai_message)
# commit 在外层做
```

或者在 LLM 失败时手动 `delete(user_message)` + rollback。

**工作量**：CC < 30m
**风险**：低（注意 LLM 调用本身可能在事务里发生——`_call_llm` 是同步 await，不会有跨事务问题）

---

## P1-7 [死代码] AuditService.MAX_LOG_CONTENT_LENGTH 永远不触发

**位置**：`backend/app/services/audit_service.py:20, 50`
**严重度**：P1
**当前状态**：未开始

**问题**：
```python
# audit_service.py:20
MAX_LOG_CONTENT_LENGTH = 100 * 1024  # 100KB
# audit_service.py:50
truncated_body = request_body[: self.MAX_LOG_CONTENT_LENGTH]  # 看起来会截

# 但调用方 gateway_service.py:79 / chat_service.py:266 / anthropic_forward.py 都先在调用方用 [:2000] 截到 2KB
# 100KB 截断是死代码
```

两个数字不一致（2KB vs 100KB）是历史遗留。P0-3 修复后 `request_body_preview` 用 80 字符，`request_body` 是 Fernet 密文（base64 expansion 1.36x），100KB 明文 → 136KB 密文。

**修复方向**：删除 `MAX_LOG_CONTENT_LENGTH` 字段和内部截断逻辑，统一由调用方控制。**或者**反过来，调用方不截，让 audit_service 集中处理。推荐后者（集中一处好维护），但需要修 3 处调用方。

**工作量**：CC < 15m
**风险**：低

---

## P1-8 [可靠性] _handle_non_stream 内部异常仅日志，无 audit log 记录

**位置**：`backend/app/services/gateway_service.py:163-167`
**严重度**：P1
**当前状态**：**DONE**（commit `86c6397`，2026-08-22）--走中期方案：`AuditLog.error_message` 字段（Text，截断 500 字符，Alembic 迁移 `a58c2f0bdc97`）。三条转发路径（非流式网关 / StreamForwarder 流式三错误分支 / Anthropic 非流式 bridge）的失败原因全部落库；API 与审计页暴露给 admin/user；该字段同时作为 P2-6 stale 标注的载体

**问题**：
```python
except Exception as e:
    logger.error(f"Request error: {e}")
    status_code = 500
    response_body = adapter.format_error(500, {"detail": str(e)})  # P0-4 已部分修
```

P0-4 修了 `str(e)` 透传，但异常分支**仍然没把 error message 写入 audit log**——客户端看到 "Internal error" + request_id，server log 有 stack，但 audit_logs 表里 `error_message` 字段（**当前 model 没有此字段**）为空。

**修复方向**：
- 短期：直接在 `record_completion` 调用前手动 `log.error_message = str(e)[:500]`
- 中期：加 `AuditLog.error_message: Text, nullable=True` 字段（schema 演进，需要 Alembic 配合 P2-5）

**工作量**：CC < 30m（短期版）
**风险**：低

---

## P1-9 [数据库] 部分索引 PG-only，SQLite 测试不覆盖

**位置**：`backend/app/models/audit.py:53-57`、`backend/tests/conftest.py:50-55`
**严重度**：P1
**当前状态**：**部分完成**--选项 B 的迁移侧已随 P2-5 解决（`postgresql_where` 正确落入 Alembic baseline 迁移，生产 PG 会按预期创建 partial index）。剩余：SQLite 测试盲区仍在（测试仍无法验证该 index 行为），如需彻底解决可走选项 A（CI 用真 PG 跑测试）

**问题**：
```python
Index(
    "ix_audit_logs_status_pending",
    "status",
    postgresql_where=text("status = 'pending'"),
)
```

`postgresql_where` 是 PostgreSQL 专有 partial index。conftest 用 SQLite in-memory 测试，SQLAlchemy 在 SQLite 下会**忽略这个 partial index**。

**实际后果**：测试通过 ≠ 生产有效。生产中 pending 索引会按预期工作（PG 支持），但**测试给的是 false sense of security**。

**修复方向**：
- 选项 A：测试用真 PG（testcontainers / Docker 跑测试）—— 重，CI 慢
- 选项 B：把这个 index 单独抽出来只在 PG migration 时创建（用 conditional DDL）
- 选项 C：直接换成普通 B-tree 索引 on `status`，不 partial——性能略差但简单

**推荐 B**（用 Alembic migration 配合 P2-5 一起做），但**P1-9 短期可走 C** 应急。

**工作量**：CC < 30m（走 C 应急） / CC < 2h（走 B 配 Alembic）

---

# P2 清单（8 个，其中 1 个已完成）

## P2-1 [测试] 覆盖率 ~7%

**位置**：`backend/tests/` 整目录
**严重度**：P2
**当前状态**：**部分完成**（2026-08-22 核实）--测试从 93 个增至 **161 个（17 个文件）**，CI 已跑 pytest（`.github/workflows/test.yml`）；P1/P2 修复各带新增测试（P1-1 +10 / P1-2 +15 / P2-3 +7 / P2-4 +9 等）。剩余缺口：router HTTP 路径仍只有 3 个测试文件（anthropic bridge / audit access / pages），auth / users / api_keys / provider_keys / gateway_forward / chat / usage 等端点未覆盖；vitest/RTL 部分因前端迁移（2026-06-06）已不适用，改为 `tests/routers/test_pages.py` 10 个页面路由测试

**现状统计**（基于 2026-06-05 修复后）：

| 范围 | 文件数 | 状态 |
|------|--------|------|
| 已测 | 11 个 | 93/93 通过 |
| 未测（router） | 15+ 个 | auth / users / api_keys / provider_keys / gateway_forward / chat / audit / usage / agent_types / anthropic_forward HTTP 路径 |
| 未测（service） | 5+ 个 | auth_service / chat_service / usage_service / provider_key_service 单元 |
| 未测（frontend） | 25+ 个 | **0 测试** |

**修复方向**（按优先级）：

1. 补 auth 全套 happy path + 2 个 error path
2. 补 provider_keys / api_keys / users / chat 的 service 层
3. 补 chat 端到端：mock upstream → POST /api/chat/.../messages/stream → 验证 SSE 格式
4. 补 anthropic bridge HTTP 路径 E2E
5. 前端加 vitest + RTL：Login / Chat / Audit 三个核心页面 smoke test
6. CI 当前只跑 ruff，**不跑 pytest**——加 `pytest` 到 CI

**工作量**：CC < 5m（CI 加 pytest） + Human > 1d（补全测试） / CC < 2h + Human > 3d
**依赖**：其他 P1 / P2 修完后再补测试更省事
**风险**：低

---

## P2-2 [代码异味] `gateway.py` 和 `gateway_forward.py` 两个 router 同名混淆

**位置**：`backend/app/routers/gateway.py` + `backend/app/routers/gateway_forward.py`
**严重度**：P2
**当前状态**：未开始

**问题**：
- `gateway.py` 路径前缀 `/api/gateway/models`，是 **ModelConfig CRUD**（管理后台用）
- `gateway_forward.py` 路径前缀 `/v1`，是 **OpenAI 兼容的 chat completions**（客户端用）

名字都叫 "gateway" 容易混淆，新人 onboarding 容易找错文件。`main.py` 的 router import 列表也显得乱。

**修复方向**：把 `gateway.py` 改名为 `model_configs.py`，保持路径前缀 `/api/gateway/models` 不变（前端不动）。**或者**改路径前缀为 `/api/model-configs`（更一致，但需前端配合）。

**推荐**：先只改文件名（零风险），路径前缀保留。

**工作量**：CC < 5m
**风险**：低

---

## P2-3 [DRY] usage_service.get_summary 4 段复制粘贴

**位置**：`backend/app/services/usage_service.py:47-101`
**严重度**：P2
**当前状态**：未开始

**问题**：`user` / `department` / `model` / `api_key` 四个分支都是同一个 select + group_by 模式，仅有的差别是 `dimension` 字段和是否要 `username`。

**修复方向**：
```python
def _build_summary_query(dimension_field, include_username, filters):
    cols = [dimension_field.label("dimension"),
            null().label("username") if not include_username else ...,
            func.count(), func.sum(...), ...]
    return select(*cols).where(*filters).group_by(dimension_field)

# 每个分支只传 dimension_field + include_username
```

**工作量**：CC < 30m
**风险**：低

---

## P2-4 [DRY] token 估算函数 3 处重复

**位置**：
- `backend/app/services/chat_service.py:325-337`
- `backend/app/services/gateway_service.py:195-212`
- `backend/app/routers/anthropic_forward.py:128`

**严重度**：P2
**当前状态**：未开始

**问题**：三处都实现了同一个 `len(content) // 3` 估算。chat_service 用 `1/3`，anthropic_forward 也是 `1/3`。逻辑分散。

**修复方向**：抽到 `app/utils/tokens.py` 的 `estimate_tokens(messages: list[dict]) -> int`。

**工作量**：CC < 15m
**风险**：低

---

## P2-5 [DB] 无 Alembic migration，依赖 create_all 自动建表

**位置**：`backend/app/main.py:27-28`
**严重度**：P2
**当前状态**：**DONE**（commit `031f40d`，2026-07-29）--Alembic 已启用，lifespan 里的 `create_all` 和 `ensure_columns` 临时方案已移除；baseline 迁移含全部 11 张表 + partial index（`postgresql_where` 正确落入）；`start.bat`/`start.sh` 启动时自动 `alembic upgrade head`。详见 CHANGELOG「P2-5 启用 Alembic 迁移」条目

**问题**：
```python
async with engine.begin() as conn:
    await conn.run_sync(Base.metadata.create_all)
```

`create_all` 只创建**不存在的表**，不会做 schema 演进：加列、加索引、改类型、外键变化都不会反映到生产 DB。

`f0a9a6f` 提交里说"audit_logs 表缺少 agent_type 列（DB 迁移）"——但这个迁移不是真用 Alembic，是直接 `create_all` 重新跑了一遍（开发期 drop+create）。生产 DB 已经存在的表，列加不上就 500。

本次 P0 修复期间（`31f6580` 等）我们也用了 `drop_all + create_all`，**这是 dev 期 OK 的方案，但生产部署前必须解决**。

**修复方向**：
1. 启用 Alembic（CLAUDE.md 列在 deps 里了，但未使用）
2. 所有 schema 变更走 `alembic revision --autogenerate`
3. init：`alembic init alembic` + 写 `alembic/env.py` 指向现有 `Base.metadata`
4. 给 P0 阶段加的列写迁移：`key_hash` / `key_prefix` / `encrypted_key` / `key_preview` / `request_body_preview`

**工作量**：Human > 1d / CC < 2h（机械写迁移）
**依赖**：P1-9（partial index 也需要走 Alembic 修）
**风险**：高（生产 schema 演进一旦错就是不可逆事故，必须先 backup 再 migrate）

---

## P2-6 [数据治理] 僵尸 pending audit log 无清理机制

**位置**：`backend/app/services/stream_forwarder.py:254-279`
**严重度**：P2
**当前状态**：**DONE**（commit `a02d7ea`，2026-08-22）--`app/services/cleanup_service.py`：lifespan 后台任务启动延迟 5 分钟后每 24h 扫一次，把超过 1h 仍 pending 的记录标 failed 并在 `error_message` 写 stale 标注（复用 P1-8 字段）；单轮失败只记日志不中断循环

**问题**：`_save_after_stream` 在 finally 块中跑。如果 finally 内部 commit 失败（如 DB 短暂中断、序列化冲突），audit log 永远是 `status='pending'`。**没有 cron / 定时任务清理僵尸 pending**。一个月后 DB 里会有 30k+ pending 记录，partial index `ix_audit_logs_status_pending` 会膨胀，get_summary 排除 pending 的过滤反而查到大量过期数据。

**修复方向**：
- 后台任务定期（如 24h）扫描 `status='pending' AND created_at < now() - 1h` 的记录，标记为 `failed` 并备注 "stale"
- 用 `asyncio.create_task` + lifespan 启动
- 或者干脆把 `ix_audit_logs_status_pending` 换成 created_at 上的普通索引（更简单但浪费存储）

**工作量**：CC < 30m
**风险**：低

---

## P2-7 [弃用] `datetime.utcnow()` 11 处使用，Python 3.12+ 已弃用

**位置**：
- `backend/app/middleware/auth_middleware.py:39, 42, 114, 117`
- `backend/app/utils/security.py:23`
- `backend/app/services/stream_forwarder.py:264`
- `backend/app/services/provider_key_service.py:26, 51, 73`
- `backend/app/services/auth_service.py:32`
- `backend/app/services/audit_service.py:92`
- `backend/app/models/base.py:12, 13`
- `backend/app/models/audit.py:25, 45`
- `backend/app/models/chat.py:36`
- `backend/app/models/api_key.py:25, 27`
- `backend/app/models/provider_key.py:35, 36`

**严重度**：P2
**当前状态**：**DONE**（commit `e081195`，2026-07-29）--抽 `app/utils/datetime_utils.utcnow()`（naive UTC，行为与 `datetime.utcnow()` 一致）替换全部调用点，消除运行时 DeprecationWarning

**问题**：`datetime.utcnow()` 在 Python 3.12+ 标记 deprecated，推荐 `datetime.now(UTC)`。项目用 Python 3.13，**每次运行会输出 DeprecationWarning**（当前 230 个 warning 几乎全是这个）。

**修复方向**：
- 用 `datetime.now(UTC)` 替换
- 或更彻底：DB 端用 `server_default=func.now()` 走 DB 时间戳（不过 SQLAlchemy async 处理 timezone-aware 类型较烦）

**推荐**：只换 Python 端（DB 时间戳改起来牵涉到 PG `timestamp with time zone` vs `timestamp without time zone` 的判断）

**工作量**：CC < 30m（机械替换 + 测试）
**风险**：低（注意 DB 读出来可能是 naive datetime，与其他代码交互时类型要对齐）

---

## P2-8 [可观测性] 无 Prometheus metrics / 无结构化日志（request_id 已完成）

**位置**：`backend/app/main.py` 整文件
**严重度**：P2
**当前状态**：**部分完成**——P0-4 修复时引入了 `utils/request_id.py` 中间件，`X-Request-ID` 响应头 + ContextVar 都到位。但 **Prometheus /metrics 端点 + 结构化日志输出**仍缺。

**问题**：
- 无 `/metrics` 端点（Prometheus 拉数据用）
- logger.error 散落各处，无统一 JSON 格式
- 无 trace_id 串联 DB / HTTP / LLM 调用
- 无 audit log 之外的成功率 / P99 latency 指标

**修复方向**：
- 加 `prometheus-fastapi-instrumentator` 或自写 `/metrics` 端点
- 配置 `logging` 用 `python-json-logger` 输出 JSON（含 request_id、path、status、latency_ms）
- 加关键业务 metrics：llm_call_total{model, status}、llm_latency_seconds_bucket{model}、audit_log_write_total{status}

**工作量**：Human > 1d（搭指标体系） / CC < 4h
**风险**：低

---

# 验收标准

每个 P1 / P2 修完后：

1. **测试**：新增测试 + 全套测试通过（93+ 保持绿色）
2. **Linter**：`ruff check backend/` 无新 warning
3. **E2E**（涉及 HTTP 接口的）：真实 uvicorn + curl 验证
4. **CHANGELOG**：在 Unreleased 段加一行
5. **commit**：单独 commit，message 引用任务编号（如 `fix(perf): P1-4 chat N+1`）
6. **回滚检查**：每个 PR 独立可 revert，不应让主干进入 broken 状态

---

# 进度跟踪

每修一个，更新本表（PR 合入后由 Claude 自动同步，或人工手动更新）。
最后同步：**2026-08-22**（依据 git log + 代码核实）。

| 编号 | 状态 | Commit | 备注 |
|------|------|--------|------|
| P1-1 | DONE | 7c3b3dc | 抽 _resolve_credentials，+10 tests，顺带修 SQLite 下 JWT sub 隐式转换 bug |
| P1-2 | DONE | 197042d | bridge_stream 80 行删掉，+transform_chunk/error_sse 钩子，+AnthropicBridgeTransformer，+save_after_stream 公共方法；+15 tests |
| P1-3 | DONE | 17d7dca | 方案 A：内存 buffer + 30s 批量 flush；优雅关闭冲 buffer；+4 tests |
| P1-4 | DONE | 4188d94 | get_messages 改 None/[] 契约，去掉 router 端 N+1 兜底 |
| P1-5 | DONE | 290d7e2 | 抽 _get_capped_history，system 消息全保留 + 最近 50 user/assistant |
| P1-6 | DONE | 63e66bd | 非流式 flush 代替 commit 让 LLM 失败回滚；流式 on_complete 失败时删 orphan |
| P1-7 | DONE | 88d8e70 | 删 MAX_LOG_CONTENT_LENGTH 100KB 死代码，body 透传 |
| P1-8 | DONE | 86c6397 | error_message 字段 + Alembic 迁移；三条转发路径失败原因全部落库；API/审计页暴露；+6 tests |
| P1-9 | 部分 | 031f40d | partial index 已正确落入 Alembic baseline 迁移（P2-5 顺带）；SQLite 测试盲区仍在 |
| P2-1 | 部分 | — | 93 -> 161 tests（17 文件），CI 已跑 pytest；router HTTP 覆盖仍不全 |
| P2-2 | DONE | 03c2fbb | gateway.py -> model_configs.py，路径前缀不变 |
| P2-3 | DONE | 032227b | 抽 _build_summary_query 静态方法，+7 tests 覆盖 4 维度 |
| P2-4 | DONE | f3da9fd | 抽 app/utils/tokens.estimate_tokens，+9 tests；附带修 None/int 被 str() 误估 1 token 的 bug |
| P2-5 | DONE | 031f40d | 启用 Alembic，移除 create_all/ensure_columns 临时方案；baseline 含 11 表 + partial index |
| P2-6 | DONE | a02d7ea | cleanup_service 后台任务每 24h 扫描；>1h pending 标 failed + stale 标注（复用 P1-8 error_message） |
| P2-7 | DONE | e081195 | 抽 app/utils/datetime_utils.utcnow()，替换全部调用点 |
| P2-8 | 部分 | — | request_id 已做（P0-4 顺带）；Prometheus /metrics + 结构化日志待做 |

---

# 相关链接

- 上游 eng review 报告：`~/.gstack/projects/ai-dev-dot-GateFlow/eng-review-2026-06-05.md`
- MVP 设计稿：`docs/superpowers/specs/2026-06-04-gateflow-mvp-design.md`
- 已修 5 个 P0 的 commit：`31f6580` / `b66d7ea` / `ba1f890` / `4e60992` / `9d7e552`
