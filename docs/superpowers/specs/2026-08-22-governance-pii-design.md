# 闸机 GateFlow PII 脱敏 设计稿

> 日期：2026-08-22
> 版本：v0.4（工程评审修订稿，待确认）
> 状态：设计稿
> 上游：
> - 2026-08-22 `/autoplan`：方向收敛。成本引擎、限流强制先后**否决**（用户原则：功能须服务于被观察到的真实需求，无依据/无法实测的不做）
> - 2026-08-22 `/plan-ceo-review`（两轮）+ 独立对抗评审（质量分 6/10）：
>   - 严重：验收口径 4 与 `[DIGITS]` 兜底规则自相矛盾 → **移除 `[DIGITS]` 兜底规则**（它是唯一"防御性过渡"，正是误报噪音源）
>   - 严重：规则引擎缺执行模型 → 补充 `re.sub` 交替 + 校验回调的执行序
>   - 中：Python `re.\d` 匹配 Unicode 数字类 → 规则一律显式 `[0-9]`；国际/拼接格式零覆盖→列入局限
>   - 中：`error_message` 截断/打码顺序未钉死 → 定序「先截断再打码」
>   - G2（CEO 边界）：审计链路脱敏 ≠ 企业数据脱敏 → 范围声明收窄为「审计链路」，Chat 表/备份挂 out-of-scope
> - 2026-08-22 `/plan-eng-review`（含工程外部声音代码核实）：
>   - `user_agent` 客户端可控明文，是"审计链路打码"唯一绕行口 → **纳入脱敏**（决策）
>   - redact 异常兜底 → **fail-open**：log + 存原文，审计写入绝不断、请求绝不 500（决策）
>   - 服务器 stdout/file 日志上游错误体仍原文 → 局限点名（决策）
>   - 验收口径 6 限定：非流式 Chat 不写审计日志（既有行为）
>
> 流程：本文确认后 -> 写 `plans/2026-08-22-pii-redaction.md` 实现计划 -> 实现。

---

## 1. 背景与目标

`ENABLE_PII_REDACTION` 是死配置：全仓库仅 `config.py:24` 出现一次，`.env.example` 标注"当前不生效"，spec v0.2.0 §6.3 承诺过实现。README 第一大卖点就是"数据泄露爆炸"。

**范围声明（G2，诚实收窄）**：本期覆盖 **审计链路**——LLM 请求体写入审计日志的位置（`create_pending_log` 的 preview + 加密 full body）、失败原因（`error_message`）、客户端 `user_agent`（客户端可控明文，纳入脱敏，见 B7）。**不覆盖** Chat 对话记录表（`Conversation/Message` 明文）、pg_dump 备份、服务器 stdout/file 日志（见 §4.4 局限 7）——那属于更深的语义决策或非 DB 范围，见 §5。

**目标**：`ENABLE_PII_REDACTION=true` 时，审计链路写入前对请求体/失败原因做轻量 regex 脱敏（preview 与加密 full body、error_message 只见掩码）；`false`（默认）时行为与现状完全一致。不保证语义级（上下文推断）敏感内容。

**为什么此项被保留、其余被砍（追溯）**：限流与成本依赖"运行真实流量/真实报价"才能获得依据，当前 0 企业用户无法观察——过渡开发。PII 不依赖运行数据：规则集静态、开关默认关闭、行为可离线验证、改动面单一切入点。它是"兑现承诺 + 收窄审计链路的明文泄露面"，不是"提前建能力"。

---

## 2. 使用场景

| 场景 | 主角 | 现在的处境 | 落地后 |
|------|------|-----------|--------|
| 合规打码 | 安全/合规 | 员工在 prompt 贴客户名单（邮箱/手机号/身份证），明文 preview 落库，admin 解密 full body 同样见明文 | 开 `ENABLE_PII_REDACTION` 后审计链路写前打码，preview / full body / error_message 只见掩码 |

---

## 3. 设计决策总表

| # | 决策点 | 选择 | 备选 | 理由 |
|---|--------|------|------|------|
| B1 | 作用范围 | **审计链路**：request body（preview + `AUDIT_LOG_FULL_BODY=true` 时的加密 full body）+ `error_message` + `user_agent`，在各写入点内部、落库前 | 只 peek preview / 覆盖 Chat 表与备份 | 模型当前只有这些字段；preview 明文必须打码；full body 加密但 admin 可解密也要打码；user_agent 是客户端可控明文（唯一绕行口）；单一切入点 |
| B2 | 掩码格式 | **类别 token**（`[EMAIL]`/`[PHONE]`/`[ID_CARD]`/`[BANK_CARD]`/`[AWS_KEY]`/`[API_KEY]`） | 统一 `***` | 审计人员需知道"原本是邮箱还不是普通数字"；统一掩盖抹掉分类信息 |
| B3 | 开关粒度 | **一个开关**（`ENABLE_PII_REDACTION`），preview/full/error_message 同开同关 | 分开关 | "是否脱敏"是一个决策；"每类规则独立开关"是**模块级常量数组**（代码内），**非**新增配置项——避免 config 长出 7 个开关复辟 B3 否决的设计 |
| B4 | 实现 | **纯 regex 启发式，零新依赖** | Presidio（spaCy NLP） | Presidio 依赖重、模型更新要人维护；regex 对结构化 PII 够用 |
| B5 | `error_message` | **纳入脱敏** | 不脱敏 | error_message 明文、≤500、用户/管理员可见，上游内容审核类错误会回显触发内容；不纳即与脱敏目标自相矛盾 |
| B6 | 数字兜底 | **不做 `[DIGITS]` 兜底** | 连续 ≥8 位数字兜底 | 该规则是唯一"防御性过渡"：必然命中日期/时间戳/订单号，制造验收矛盾与误报噪音；具体规则集做好边界即可，宁漏不误伤 |
| B7 | 客户端 `user_agent` | **纳入脱敏**（与 body 同一 redactor） | 声明为已接受局限 | user_agent 是客户端可控明文且在列表 API 原样返回（gateway_service.py:86 / anthropic_forward.py:148），是"审计链路打码"承诺的唯一绕行口；纳入成本仅一行 |
| B8 | 失败兜底 | **fail-open**：`redact_text` 异常 → `logger.warning` + 落原文，审计写入绝不断、请求绝不 500 | fail-closed（宁缺审计也不漏 PII） | 与 P0-4"审计是诊断数据不拖主链路"一致；异常可观测（log + metric），不会静默失败 |

---

## 4. 设计

### 4.1 数据流（脱敏落在哪一步）

```
现状：
调用方(router/service) --json.dumps[:2000]--> create_pending_log --(preview + [可选]Fernet加密full)--> DB
                                           error_message --[:500]--> record_completion / _save_after_stream --> DB

目标：
调用方 --json.dumps[:2000]--> create_pending_log
                                   │ redact_text(body)          ← redactor.py
                                   ├── preview（脱敏后：短 body 全文可见、超长 head40...tail37，与现有截断口径一致）
                                   └── [FULL_BODY] Fernet加密（脱敏后密文）
调用方错误 --> error_message --[:500]截断--> redact_text --> 写库      ← 定序：先截断再打码（改动最小，与现有 `[:500]` 行为一致）
```

- 切入 `AuditService.create_pending_log`（audit_service.py:64-106）：拿到 `request_body` 先脱敏，再生成 preview / 加密
- 掩码 token 长度会计入 `AUDIT_LOG_PREVIEW_CHARS` 预算（`[EMAIL]` 7 字符），preview 仍按现有 head/tail 逻辑
- `error_message` 两处写库（`record_completion` audit_service.py:140 / `_save_after_stream` stream_forwarder.py:349）：`[:500]` 截断后过 `redact_text`
- `user_agent` 两处写库（gateway_service.py:86 / anthropic_forward.py:148）：落库前过同一 `redact_text`（B7）
- 所有 redact 调用统一走 `_redact_safe(text) -> str` helper：内部 try/except，异常时 `logger.warning` + 返回原文（fail-open，B8）
- 现有代码杠杆：`create_pending_log` 是网关 / Anthropic bridge / 流式 Chat 的统一写入点，一处改动全覆盖；**非流式 Chat 不写审计**（既有行为，不在范围）；`ENABLE_PII_REDACTION` 已存在（`_save_after_stream` 当前不读 settings，接线时补 import）

### 4.2 规则集与执行模型

**执行模型（无歧义，工程师可直接实现）**：

```python
# app/services/redactor.py
def redact_text(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        return text                      # None/空/纯空白原样返回

    def _replace(match: re.Match) -> str:
        kind = match.lastgroup            # 交替组名 = 类别
        raw = match.group(kind)
        if kind == "id_card" and not luhn_like_checksum(raw):    # 校验位算法
            return raw                   # 校验不过 = 不是身份证，不掩码（宁漏不误伤）
        if kind == "bank_card" and not luhn(raw):
            return raw                   # Luhn 不过 = 不是银行卡
        return MASKS[kind]               # "[EMAIL]" / "[PHONE]" / ...

    return MASTER_PATTERN.sub(_replace, text)
```

- `MASTER_PATTERN` = 单条交替正则 `(EMAIL)|(PHONE)|(ID_CARD)|(BANK_CARD)|(AWS_KEY)|(API_KEY)`，**一次性 `re.sub`**，靠组的交替顺序消歧
- **`ID_CARD`(18) 与 `BANK_CARD`(13-19) 的 18 位 span 重叠**（18 ∈ [13,19]），不能靠组序互斥：先命中组合校验不过会"跳过 span"，另一组再无机会。回调对两 kind 走**并集门**：`id_checksum` 过 → `[ID_CARD]`，否则 `luhn` 过 → `[BANK_CARD]`，都不过 → 原样（E5 实证修复，见 plan P4）；其余类别无重叠
- **全部规则显式用 `[0-9]`**：Python `re` 的 `\d` 匹配 Unicode 数字类（全角 `１２３`、印度数字），会导致多规则行为不一致甚至把全角数字当 PII 打码
- 掩码 token（`[EMAIL]` 纯 ASCII 字母）不含数字/`@`，不会被原规则二次命中——**幂等**：`redact_text(redact_text(s)) == redact_text(s)`，作为显式单测
- 正则模块级预编译；掩码与规则集中在 `app/services/redactor.py` 单文件
- 调用方统一经 `_redact_safe()` 包装（内部 try/except + `logger.warning`），redact 异常绝不阻断审计写入（B8）

**类别规则**：

| 类别 | 掩码 | 规则要点（边界可测试） |
|------|------|------------------------|
| EMAIL | `[EMAIL]` | 标准邮箱结构 + `\.[A-Za-z]{2,}` 结构式域名校验（**不维护 TLD 白名单**）；支持 `user.name+tag@` |
| PHONE | `[PHONE]` | `(?<![0-9])1[3-9][0-9]{9}(?![0-9])`（中国大陆 11 位）。**国际格式（含无空格 `+86` 前缀、海外号码）明确不覆盖**，列入局限 |
| ID_CARD | `[ID_CARD]` | 18 位 `[0-9]{17}[0-9Xx]` + **校验位算法验证**；校验不过则原样返回（宁漏不误伤）。15 位旧证**不纳入**，列入局限 |
| BANK_CARD | `[BANK_CARD]` | 13-19 位 `[0-9]{13,19}` + **Luhn 校验**；不过则原样返回。带空格/连接符的排版形态不覆盖，列入局限 |
| AWS_KEY | `[AWS_KEY]` | `AKIA[0-9A-Z]{16}` |
| API_KEY | `[API_KEY]` | `sk-[A-Za-z0-9]{20,}` |

**明确移除**：`[DIGITS]` 兜底。理由见 B6——它是误报噪音源，且移除后验收口径 4 才能成立。

### 4.3 配置与文档

- 沿用 `ENABLE_PII_REDACTION`（config.py:24，bool，默认 false）
- **运行期改配置不生效**：`get_settings()` 是 `lru_cache`，需重启进程（与现有其他配置一致）
- 同步修订 `.env.example` 注释与 README 隐私章节的"当前不生效"标注为"启用后对审计链路做启发式脱敏"
- spec v0.2.0 附录 A 的 Presidio 字样修订为"regex 启发式（不依赖 Presidio）"

### 4.4 已知局限（明写进 README/spec，不隐藏）

1. **启发式非保证**：regex 对结构化 PII 有效，对语义性敏感内容无效，漏检存在
2. **国际/拼接格式不覆盖**：无空格 `+86` 前缀手机号、海外手机号、带空格/连接符的银行卡、15 位旧版身份证——宁漏不误伤
3. **截断交互**：调用方先 `[:2000]` 截断再脱敏，跨边界 PII 可能被切半残留少量明文（如只余 `1380` 4 位）；已无 `[DIGITS]` 兜底，**残留段 < 对应规则位数时明文残留**，接受并声明
4. **历史数据不追溯**：存量日志不回写；"开启前已存数据不受影响"
5. **与加密的关系**：脱敏降低明文泄露面，不是加密替代；full body 仍按原策略 Fernet 加密
6. **范围边界**：不覆盖 Chat 对话表与备份（见 §5）
7. **服务器日志 sink**：上游错误体 / exception repr 在 `logger.warning` / `logger.error` 中是原文（DB 之外的 sink），本期 DB 打码不覆盖 stdout/file 日志——部署侧需日志脱敏或访问控制（已评估，非本期范围）
8. **卡号被数字包围逃逸**：13-19 位银行卡若与前后数字连成更长的 digit-run（如 `1` + 卡号 + `1`），整段 run 校验不过即不掩码（宁漏不误伤的设计，未做 digit-run 内滑窗 Luhn 子段识别——超出本 spec 已验证范围，列为开放项）
9. **`sk-` 前缀误报**：`sk-[A-Za-z0-9]{20,}` 可能命中词尾恰为"sk-" + 20+ 字母数字的罕见词（如 `risk-2026Q3report…`），换取 API Key 类覆盖必发。误报不泄露，但会不可逆改写审计预览——已知权衡（regex 启发式的既定代价）

---

## 5. 明确不做（Out of scope，本期）

| 项 | 原因 | 归宿 |
|----|------|------|
| 成本金额 / 定价 / 预算 | 用户决策：国内计价不公开不可维护 | 条件触发再启用 |
| 限流 / 白名单强制 / 上游 rpm-tpm | 用户决策：无真实使用依据、无法实测 | 有真实流量后定规则 |
| Chat 对话表（`Message.content`）脱敏 | 改对话明文=改变用户可见内容，是独立语义决策，需真实使用场景触发 | TODO：有真实场景再评估 |
| 备份（pg_dump）脱敏 | 同理，非审计链路 | 同上 |
| Presidio / 语义级脱敏 | 依赖过重 | 有"语义级需求"证据再评估 |
| 历史审计数据脱敏回写 | 非本期目标 | 未来按需 |
| 分布式/性能预检优化 | HOLD SCOPE 姿态：不扩展 | 有性能证据再说 |
| Docker / SSO / 数据导出 | 与本期无关 | roadmap |

---

## 6. 验收口径（行为级，供 plan 展开为测试）

1. `true` 时：邮箱/手机/身份证（校验通过）/银行卡（Luhn 通过）/AWS Key/API Key 在 preview 与加密 full body 中均为对应掩码 token，无原文（full body 项在 `AUDIT_LOG_FULL_BODY=true` 下验证）
2. `true` 时：`error_message`（先 `[:500]` 截断再打码）与 `user_agent` 中上述结构化 PII 掩码
3. **幂等**：`redact_text(redact_text(s)) == redact_text(s)`；掩码 token 不被二次命中
4. **不误伤**（具体规则）：ISO 日期（`2026-08-22`/`20260822`）、Unix 时间戳、订单号（`NO202608220001`）、版本号（`v2.4.7`）、15 位旧证、校验不过的 18 位数字不被掩码——`[DIGITS]` 已移除，无需担心长数字兜底
5. `false`（默认）时：行为与现状完全一致（回归测试锁定：日志字段字节级不变；守卫在 `if settings.ENABLE_PII_REDACTION:` 分支内且公共路径零副作用）
6. 所有经 `create_pending_log` 的路径（OpenAI 网关 / Anthropic bridge / 流式 Chat）单点覆盖验证；**非流式 Chat 不写审计日志**（既有行为，不在验证范围）
7. 空/None/纯空白输入原样返回；全角数字 `[0-9]` 不误匹配的显式用例
8. **fail-open 显式用例**：`redact_text` 抛异常（注入极端输入）时，审计写入仍完成、请求不 500、原文落库且 log 出现 warning（不见静默失败）
9. 全套测试绿色、ruff 无新警告、真实 uvicorn + curl 验证（沿用 backlog 验收口径）

---

## 附录 A：spec v0.2.0 对 PII 的承诺原文（需兑现或显式修订）

`docs/superpowers/specs/2026-06-04-gateflow-mvp-design.md` §6.3：

> `ENABLE_PII_REDACTION` | `false` | `true` 时接入 Presidio 自动识别 email/phone/身份证/银行卡/AWS Key 等并打码（v0.2.0 实现）

本期以 B4 决策（regex 方案）兑现"打码"承诺；Presidio 字样修订为"regex 启发式"。**顺带勘误同一文档的 `AUDIT_LOG_FULL_BODY` 释义**（该文附录 D 写"`true` **不存** body"，实际实现是 `true`=加密存 full body）——实现时一并修订，避免三处文档三套说法。

## 附录 B：本期变更总览

| 位置 | 现状 | 本期动作 |
|------|------|---------|
| `app/services/redactor.py` | 不存在 | 新增（规则常量数组 + `MASTER_PATTERN` + `redact_text` + `_redact_safe`，模块级预编译） |
| `AuditService.create_pending_log` | 不脱敏 | preview/加密生成前调 `_redact_safe` |
| `AuditService.record_completion` / `StreamForwarder._save_after_stream` | `error_message` 直落 | `[:500]` 后调 `_redact_safe`（B5）；`_save_after_stream` 补 import settings |
| `gateway_service.py:86` / `anthropic_forward.py:148` | `user_agent` 明文直落 | 落库前调 `_redact_safe`（B7） |
| `config.py:24` + `.env.example` + README | 死配置/"当前不生效" | 生效 + 文档修订 |
| `docs/superpowers/specs/2026-06-04-gateflow-mvp-design.md` | Presidio 承诺 + FULL_BODY 释义错误 | 兑现 + 勘误 |
| 数据库 schema | — | **零变更**（脱敏在内存完成） |

## 附录 C：本次评审裁剪记录（追溯）

| 轮次 | 方向 | 决策 | 依据 |
|------|------|------|------|
| 2026-08-22 `/autoplan` | 成本引擎 | 否决 | 计价数据不可维护 |
| 2026-08-22 `/autoplan` | 限流/白名单/rpm-tpm | 否决 | 无真实使用依据、无法实测 |
| 2026-08-22 `/plan-ceo-review` R2 + 独立评审 | PII spec | HOLD SCOPE | 补齐执行模型、移除 `[DIGITS]` 兜底、定序、范围收窄为审计链路 |