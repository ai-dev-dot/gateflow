"""PII redaction for the audit chain. Zero new deps.

纯函数、无 I/O、不读 settings（开关由调用点调用时判断）。
规则守恒「宁漏不误伤」：候选必须过结构与校验门槛才被替换。

PIPELINE:
  text (None/空/纯空白 → 原样)
    └─ MASTER_PATTERN.sub(_replace, text)     # 单次交替正则，组名=类别
         └─ gate(kind, raw):                  # 并集门（plan P4/E5），过校验才掩码
              id_card / bank_card span 重叠 → 同一 raw 跑两个校验：
                  id_checksum(raw) → "[ID_CARD]"
                  luhn(raw)        → "[BANK_CARD]"
                  都不过 → 原样
              其余类别 → 结构匹配即掩码
  幂等：掩码 token 纯 ASCII 字母、无数字/@，不会被原规则二次命中。
  参考 spec: docs/superpowers/specs/2026-08-22-governance-pii-design.md §4.2

调用方统一经 `maybe_redact(text, enabled)` —— 开关判断在调用点（调用时读
settings，禁止 import 时缓存，否则测试 monkeypatch 失效）。
"""

import logging
import re

from app.utils.metrics import observe_pii_redact_failure

logger = logging.getLogger(__name__)

# 类别掩码 token（纯 ASCII 字母、无数字/@——幂等性关键：不会被原规则二次命中）
MASKS = {
    "email": "[EMAIL]",
    "phone": "[PHONE]",
    "id_card": "[ID_CARD]",
    "bank_card": "[BANK_CARD]",
    "aws_key": "[AWS_KEY]",
    "api_key": "[API_KEY]",
}

# 规则正则，模块级预编译。一律显式 [0-9]：Python re.\d 匹配 Unicode 数字类
# （全角 １２３、印度数字），会导致多规则行为不一致甚至把全角数字当 PII 打码。
# 结构式域名，无 TLD 白名单。前瞻 `(?=[...]{1,64}@)` 把每个起始位置的
# 预扫描限到 ≤64 字符内（并隐含 local-part ≤64 的标准语义）——否则无 `@`
# 的长串（纯数字/字母块、base64）上 `[A-Za-z0-9._%+-]+@` 会逐位置贪婪吞后缀
# 再回溯，整体退化为 O(n²)，实测 2KB ~10ms 阻塞事件循环（对抗评审 #1）。
# 后行断言 `(?<![...])` 防止 local-part >64 时 re 从位置 1 重试导致部分匹配
# （`a`*65 + `@x.com` 曾被掩成 `a[EMAIL]`，首字符泄露）：位置 0 前瞻失败后，
# 位置 1..64 前导均为 local-part 字符，lookbehind 逐一挡住，整串不漏掩码。
_EMAIL = (
    r"(?<![A-Za-z0-9._%+-])(?=[A-Za-z0-9._%+-]{1,64}@)"
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)
_PHONE = r"(?<![0-9])1[3-9][0-9]{9}(?![0-9])"  # 中国大陆 11 位；国际格式明确不覆盖
_ID_CARD = r"(?<![0-9])[0-9]{17}[0-9Xx](?![0-9])"  # 18 位 + 校验位算法
_BANK_CARD = r"(?<![0-9])[0-9]{13,19}(?![0-9])"  # 13-19 位 + Luhn
_AWS_KEY = r"AKIA[0-9A-Z]{16}"
_API_KEY = r"sk-[A-Za-z0-9]{20,}"

MASTER_PATTERN = re.compile(
    rf"(?P<email>{_EMAIL})|(?P<phone>{_PHONE})|(?P<id_card>{_ID_CARD})"
    rf"|(?P<bank_card>{_BANK_CARD})|(?P<aws_key>{_AWS_KEY})|(?P<api_key>{_API_KEY})"
)

# 身份证 18 位校验位（GB 11643-1999）：前 17 位 × 权重，模 11 映射 10X98765432
_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK_MAP = "10X98765432"


def luhn(digits: str) -> bool:
    """Luhn checksum（ISO/IEC 7812）。非 13+ 位纯数字输入直接 False。"""
    if not digits or not digits.isdigit() or len(digits) < 13:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def id_checksum(card: str) -> bool:
    """18 位中国居民身份证校验位验证（GB 11643-1999）。"""
    if not card or len(card) != 18:
        return False
    body, check = card[:17], card[17]
    if not body.isdigit() or check.upper() not in "0123456789X":
        return False
    expected = _ID_CHECK_MAP[sum(int(a) * w for a, w in zip(body, _ID_WEIGHTS, strict=True)) % 11]
    return expected == check.upper()


def _replace(match: re.Match) -> str:
    """并集门：id_card(18) 与 bank_card(13-19) 的 span 重叠（18∈[13,19]），
    不能靠组序互斥——先命中组校验不过会"跳过 span"，另一组再无机会。
    对同一 raw 先 id_checksum 再 luhn，任一通过即按对应类别掩码（E5 实证修复）。
    """
    kind = match.lastgroup
    raw = match.group(kind)
    if kind in ("id_card", "bank_card"):
        if id_checksum(raw):
            return MASKS["id_card"]
        if luhn(raw):
            return MASKS["bank_card"]
        return raw
    return MASKS[kind]


def redact_text(text: str) -> str:
    """Redact structured PII from text. None/空/纯空白 → 原样（宁漏不误伤）。"""
    if not text or not text.strip():
        return text
    return MASTER_PATTERN.sub(_replace, text)


def _redact_safe(text: str | None) -> str | None:
    """fail-open 包装（spec B8 / plan P6）：redact 异常绝不阻断审计写入，
    记 warning log + 失败计数 + 落原文。"""
    if text is None or not isinstance(text, str):
        return text
    try:
        return redact_text(text)
    except Exception:
        logger.warning("PII redaction failed; storing original text", exc_info=True)
        observe_pii_redact_failure()
        return text


def maybe_redact(text, enabled: bool):
    """调用点一行入口：enabled 走 fail-open 脱敏，否则原样返回。

    `enabled` 必须在调用点**调用时**读 `settings.ENABLE_PII_REDACTION`，
    禁止模块级 / import 时缓存。
    """
    if not enabled:
        return text
    return _redact_safe(text)
