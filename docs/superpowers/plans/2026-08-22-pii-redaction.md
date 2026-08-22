# PII 脱敏实现计划

> 日期：2026-08-22
> 状态：已实现并验收（265 测试绿，ruff 无新警告；E2E 已于 2026-08-22 跑通：PII 开启 37 项断言 + 关闭回归 5 项断言）
> 上游：`docs/superpowers/specs/2026-08-22-governance-pii-design.md`（v0.4，已确认）
> 目标：把 `ENABLE_PII_REDACTION` 从死配置变成真开关——审计链路（request body / preview / error_message / user_agent）落库前 regex 脱敏；开关默认 false，行为零变化。
> 验收基线：spec §6 九条验收口径（本文按此展开测试）。

---

## 1. 实现决策补记（plan 层精化，均等效于且优于 spec 描述）

| # | 决策 | 说明 |
|---|------|------|
| P1 | **user_agent 收缩到 `create_pending_log` 内部脱敏** | spec B7 写"gateway_service.py:86 / anthropic_forward.py:148 两处接线"，但这两处实际是**把 `user_agent` 作为参数传给 `create_pending_log`**。在 `create_pending_log` 内对 `user_agent` 参数脱敏即可覆盖全部调用方（含 chat 路径 None 分支），caller 零改动——比 spec 更精简、语义等价 |
| P2 | fail-open 由 `_redact_safe()` 承载 | 每个调用点一行 `_redact_safe(x)`，内部 try/except + `logger.warning` + 返回原文（spec B8） |
| P3 | 开关必须在**调用时**读 `settings.ENABLE_PII_REDACTION` | 禁止模块级 `REDACT_ON = get_settings().ENABLE_PII_REDACTION` 在 import 时缓存——否则测试里 `monkeypatch.setattr(get_settings(), ...)` 失效（测试基建前提，见 spec §4.1） |
| P4 | **ID/银行卡并集门**（E5，实证 bug） | `ID_CARD`(18) 与 `BANK_CARD`(13-19) 的 18 位 span 重叠；若 id 组先命中且校验不过，回调"返回原样"会让 `re.sub` 跳过该 span，`bank_card` 再无机会 → **18 位合法 Luhn 卡漏网**（实证：`123456789012345671`）。修法：回调对两 kind 走**并集门**（见 T1） |
| P5 | DRY guard → `maybe_redact(text, enabled)` helper | 三个调用点各一行；内部 `_redact_safe`（fail-open）+ 开关判断，消掉重复 if/else |
| P6 | 失败信号 → `gateflow_pii_redact_failure_total` counter | `_redact_safe` 异常分支 `observe_pii_redact_failure()`（加计数）；让 fail-open 可量化观测，符合"功能须可被观察到" |

## 2. 现状代码杠杆（全部复用，无平行实现）

| 位置 | 现状 | 复用 |
|------|------|------|
| `audit_service.create_pending_log`（audit_service.py:64-106） | 已 `settings = get_settings()`；收 request_body + user_agent | 单点脱敏两字段，再走现有 preview/加密逻辑 |
| `audit_service.record_completion`（audit_service.py:116-148） | `error_message[:500]` 直落 | 加 guard + `_redact_safe` |
| `stream_forwarder._save_after_stream`（stream_forwarder.py:310-374） | `error_message[:500]` 直落；**不 import settings** | 补 import + guard + `_redact_safe` |
| `config.py:24` `ENABLE_PII_REDACTION` | 死配置 | 直接读用，零改动 |
| `app/utils/metrics.py` | Counter 业务指标模式（P2-8 先例） | 新增 `gateflow_pii_redact_failure_total` + `observe_pii_redact_failure()`（P6） |

---

## 3. 任务分解

### T1 新增 `app/services/redactor.py`（纯函数模块，零依赖）

```python
"""PII redaction for the audit chain. Zero new deps.

纯函数、无 I/O、不读 settings（开关由调用点调用时判断）。
规则守恒「宁漏不误伤」：候选必须过结构与校验门槛才被替换。

PIPELINE:
  text (None/空/纯空白 → 原样)
    └─ MASTER_PATTERN.sub(_replace, text)     # 单次交替正则，组名=类别
         └─ gate(kind, raw):                  # 并集门（P4/E5），过校验才掩码
              id_card / bank_card span 重叠 → 同一 raw 跑两个校验：
                  id_checksum(raw) → "[ID_CARD]"
                  luhn(raw)        → "[BANK_CARD]"
                  都不过 → 原样
              其余类别 → 结构匹配即掩码
         └─ MASKS[kind]                        # "[EMAIL]" 等类别 token
  幂等：掩码 token 纯 ASCII 字母、无数字/@，不会被原规则二次命中。
  参考 spec: docs/superpowers/specs/2026-08-22-governance-pii-design.md §4.2
"""
```

- 常量：`MASKS`（6 类别）、规则正则、`MASTER_PATTERN = re.compile(r"(?P<email>..)|(?P<phone>..)|(?P<id_card>..)|(?P<bank_card>..)|(?P<aws_key>..)|(?P<api_key>..)")` 模块级预编译
- 正则要点（**一律 `[0-9]` 显式 ASCII**，见 spec §4.2）：
  - `EMAIL`: `[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}`（结构式域名，无 TLD 白名单）
  - `PHONE`: `(?<![0-9])1[3-9][0-9]{9}(?![0-9])`
  - `ID_CARD`: `(?<![0-9])([0-9]{17}[0-9Xx])(?![0-9])` + 18 位校验位算法
  - `BANK_CARD`: `(?<![0-9])([0-9]{13,19})(?![0-9])` + Luhn
  - `AWS_KEY`: `AKIA[0-9A-Z]{16}`；`API_KEY`: `sk-[A-Za-z0-9]{20,}`
- 校验 helper：`luhn(digits: str) -> bool`、`id_checksum(card: str) -> bool`（权重 `[7,9,10,5,8,4,2,1,6,3,7,9,10,5,8,4,2]`，模 11 映射 `"10X98765432"`）
- 公共接口：
  - `redact_text(text: str) -> str`：None/空/纯空白原样（`if not isinstance(text, str) or not text.strip(): return text`）
  - `_redact_safe(text: str | None) -> str | None`：fail-open 包装（try/except → `logger.warning("PII redaction failed; storing original", exc_info=True)` + `observe_pii_redact_failure()` + 返回原文，P6）
  - `maybe_redact(text, enabled: bool) -> str | None`：调用点一行入口——`enabled` 走 `_redact_safe(text)`，否则原样返回（P5，DRY）
- 附带（P6）：`app/utils/metrics.py` 加 `gateflow_pii_redact_failure_total = Counter(...)` + `observe_pii_redact_failure()`（随 T1 commit）
- 验收：`python -m pytest tests/services/test_redactor.py -v`

### T2 `audit_service.py` 接线（create_pending_log + record_completion）

- `create_pending_log`：在已有 `settings = get_settings()` 之后、构造 log 之前：
  ```python
  # P3 开关调用时读；P5 一行 helper
  request_body = maybe_redact(request_body, settings.ENABLE_PII_REDACTION)
  user_agent   = maybe_redact(user_agent, settings.ENABLE_PII_REDACTION)
  ```
  （P1：user_agent 与 request_body 同点脱敏；None/空由 `maybe_redact → _redact_safe` 原样返回）
- `record_completion`：“`log.error_message = error_message[:500] if error_message else None`”改为：
  ```python
  log.error_message = maybe_redact(
      error_message[:500] if error_message else None,
      settings.ENABLE_PII_REDACTION,
  )
  ```
  需在函数内补 `settings = get_settings()`
- import 追加：`from app.services.redactor import maybe_redact`
- 验收：`pytest tests/services/test_audit_service.py -v` 全绿 + 新增用例

### T3 `stream_forwarder.py` 接线（_save_after_stream）

- import 追加：`from app.config import get_settings`、`from app.services.redactor import maybe_redact`
- `_save_after_stream` 的 `audit_log.error_message = ...` 行改为 `maybe_redact(error_message[:500] if error_message else None, settings.ENABLE_PII_REDACTION)`（先 `[:500]` 再打码：spec §4.1 定序）
- 注意该函数在新 session（`async with factory() as db`）内运行，settings 读取与 DB 无关，安全
- 验收：`pytest tests/services/test_stream_forwarder.py -v` 全绿

### T4 配置 / 文档 / 勘误（随代码同 commit）

- `.env.example`：`ENABLE_PII_REDACTION` 注释由"v0.2.0 实现，当前不生效"→"启用后对审计链路（请求体/失败原因/User-Agent）做启发式 regex 脱敏；默认关闭；改后需重启"
- `README.md`「数据存储与隐私」表格下追加一行说明 + `ENABLE_PII_REDACTION` 语义
- `docs/superpowers/specs/2026-06-04-gateflow-mvp-design.md`：
  - §6.3 表格 `ENABLE_PII_REDACTION` 描述"接入 Presidio"→"regex 启发式（不依赖 Presidio），v0.2.0 已实现为 v0.3 落地"（按 spec 附录 A）
  - **勘误**该文档附录 D `AUDIT_LOG_FULL_BODY` 释义（现写"true 不存 body"，应为"true=加密存 full body"）
- 更新本文档头部状态；CHANGELOG Unreleased 加条目（见 §5）

### T5 测试（与 T1-T3 同步编写）

见 §4 测试计划。

---

## 4. 测试计划（按 spec §6 验收展开）

### 覆盖图（新增 CODE PATHS）

```
[+] app/services/redactor.py
  ├── redact_text(None/'')/纯空白 → 原样                        ★★★ 单测
  ├── EMAIL 匹配（+tag、点号、子域名）/ 无 TLD 不匹配            ★★★
  ├── PHONE 匹配 / "10086" 前后数字定界 / 全角不匹配             ★★★
  ├── ID_CARD 校验位过→[ID_CARD] / 校验位不过→原样               ★★★
  ├── BANK_CARD Luhn 过→[BANK_CARD] / Luhn 不过→原样             ★★★
  ├── AWS_KEY / API_KEY 匹配                                    ★★
  ├── 不误伤回归：ISO日期('2026-08-22'/'20260822')/Unix时间戳/订单号/版本号 → 原样  ★★★
  ├── 幂等 redact_text(redact_text(s)) == redact_text(s)        ★★★
  ├── 中文文本内嵌匹配                                          ★★★
  ├── 截断残留（"1380" 4位）→ 原样（宁漏）                       ★★
  ├── [E5 回归] 18位重叠并集门：有效 Luhn 18 位→[BANK_CARD]；有效校验位 ID→[ID_CARD]；19 位 Luhn→[BANK_CARD]  ★★★
  └── maybe_redact(enabled=False) → 原样                        ★★★
[+] audit_service.create_pending_log
  ├── ENABLE=true：preview 含 [EMAIL] 无原文；FULL_BODY=true 时加密 full 解密后含 [EMAIL]   ★★★
  ├── [CRITICAL 回归] ENABLE=false（默认）：preview/user_agent 与原实现字节级一致            ★★★
  ├── user_agent 含 PII：ENABLE=true 时脱敏；false 时原样                                     ★★★
  └── ENABLE=true + user_agent=None → 保持 None、不崩（Chat 流式路径形态）                  ★★
[+] record_completion / _save_after_stream
  ├── error_message 含 PII：true→脱敏；false→原样（回归）                                    ★★★
  ├── fail-open：monkeypatch redact_text 抛异常 → _redact_safe 返回原文、log warning、不 raise  ★★★
  └── [P6] 同场景 gateflow_pii_redact_failure_total +1                                       ★★★
```

### 用例清单（文件级）

| 文件 | 用例 |
|------|------|
| 新增 `tests/services/test_redactor.py` | 上述 redactor 全部：每类别 match/non-match、校验函数（Luhn / id_checksum 边界）、**18 位重叠并集门（E5 回归：Luhn 卡 / 校验位 ID / 19 位各归其类）**、幂等、None/空、全角、中文内嵌、截断残留、`_redact_safe` fail-open + **counter 递增**、`maybe_redact` 关闭分支原样 |
| 扩展 `tests/services/test_audit_service.py` | create_pending_log 开关两态（preview / full body / user_agent、user_agent=None 保持 None）、false 分支回归断言；record_completion error_message 两态 |
| 扩展 `tests/services/test_stream_forwarder.py` | `_save_after_stream` error_message 两态（沿用现有 session_factory 注入） |

### 测试基建注意点

- 开关两态用现有模式 `monkeypatch.setattr(get_settings(), "ENABLE_PII_REDACTION", True)`（改缓存单例，monkeypatch 自动还原），复用 `tests/services/test_audit_service.py:195/299/341` 的隔离方式——**前提**是 T1 P3 的调用时读 settings
- false 分支回归是 **CRITICAL**：断言默认路径 preview / error_message / user_agent 输出与改动前完全一致（防脱敏误伤 + 防意外副作用）

### E2E（验收最后一步，真实 uvicorn + curl）

1. `.env` 设 `ENABLE_PII_REDACTION=true`，启动
2. `POST /v1/chat/completions`（mock 上游）prompt 含 `test@example.com`、`13800138000`
3. `GET /api/audit/logs` → request_body_preview 为 `[EMAIL]`/`[PHONE]` 无原文
4. admin `GET /api/audit/logs/{id}?include_body=true` → 加密 body 解密后为掩码
5. 用带 PII 的 `User-Agent` 头再打一次 → 审计列表 user_agent 已掩码
5-bis. `POST /v1/messages`（Anthropic 桥，mock 上游）prompt 同含邮箱/手机 → 审计 preview 掩码（不同 body 形态单点覆盖验证）
6. 关闭开关重启，重复 2-5-bis → 与现状一致（回归）

---

## 5. 同步与提交流

- **CHANGELOG.md Unreleased** 增加五条（随对应 commit）：
  - `feat(pii): 审计链路 PII 脱敏（ENABLE_PII_REDACTION 生效）`——含范围声明（审计链路、user_agent、error_message；不含 Chat 表/备份/服务器日志，见 spec §4.4）
  - `feat(pii): 引入 app/services/redactor.py（regex 启发式，fail-open，ID/银行卡并集门）`
  - `feat(pii): gateflow_pii_redact_failure_total 失败指标（fail-open 可观测，P6）`
  - `docs: .env.example / README / spec v0.2.0 的 PII 承诺与 AUDIT_LOG_FULL_BODY 释义勘误`
  - `test(pii): redactor + audit/stream 开关两态 + fail-open + 18 位并集门 + false 回归`
- **commit 拆分**（每任务一个，可独立 revert）：
  1. `feat(pii): redactor.py + create_pending_log/record_completion/_save_after_stream 接线 + metrics 指标`（含 T1-T3）
  2. `docs(pii): .env.example/README/旧 spec 修订 + CHANGELOG`（T4）
  - 测试随实现代码同 commit
- **backlog 表**：本项是 backlog 之外的新功能（P1/P2 清零），按仓库惯例只记 CHANGELOG，**不动** P1/P2 进度表

## 6. 验收标准（建议合入门槛）

1. 全套 `python -m pytest tests/ -v` 绿色（含新增用例）
2. `python -m ruff check app/ tests/` 无新警告
3. §4 E2E 七步全过（真实 uvicorn + curl，含 Anthropic 桥冒烟）
4. `ENABLE_PII_REDACTION=false` 默认路径字节级回归（CRITICAL 测试锁定）
5. CHANGELOG + 文档同步随代码提交；commit message 引用 `pii`
6. 每个 commit 独立可 revert，主干不进入 broken 状态