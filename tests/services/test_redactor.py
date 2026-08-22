"""Tests for app/services/redactor.py（PII 脱敏规则引擎）。

覆盖 spec §6 + plan T5：
- 每类别 match / non-match
- E5 并集门回归（18 位身份证 span 与银行卡重叠）
- 幂等、None/空/纯空白、全角数字、中文内嵌、截断残留
- fail-open（异常 → 落原文 + 计数）、maybe_redact 开关分支
- G1 回归：日期 / 时间戳 / 订单号 / 版本号不误伤（[DIGITS] 已移除）
"""

from app.services.redactor import (
    MASKS,
    _redact_safe,
    id_checksum,
    luhn,
    maybe_redact,
    redact_text,
)
from app.utils.metrics import PII_REDACT_FAILURES

# 硬编码有效测试数据（2026-08-22 生成）
VALID_ID = "110101198809011235"  # 身份证 18 位校验位通过
L18_LUHN = "123456789012345671"  # Luhn 过、身份证校验不过（E5 关键用例）
CARD16 = "6222021234567894"  # 合法 Luhn 16 位银行卡

AWS_EXAMPLE = "AKIAIOSFODNN7EXAMPLE"  # AKIA + 16 字符
API_EXAMPLE = "sk-abcdefghijklmnopqrstuvwxyz"  # sk- + 20+


# ---------- 校验 helper ----------


def test_luhn_helper():
    assert luhn(CARD16) is True
    assert luhn(L18_LUHN) is True
    assert luhn("6222021234567890") is False  # 改一行数字
    assert luhn("123") is False  # 过短
    assert luhn("ab34") is False  # 非数字
    assert luhn("") is False


def test_id_checksum_helper():
    assert id_checksum(VALID_ID) is True
    assert id_checksum(VALID_ID[:-1] + "0") is False  # 改末位
    assert id_checksum("110101198809011") is False  # 非 18 位
    assert id_checksum("A" * 18) is False


# ---------- 每类别匹配 ----------


def test_email_masked():
    assert redact_text("reach me at john.doe+tag@sub.example.com, thanks") == (
        "reach me at [EMAIL], thanks"
    )


def test_email_no_tld_unmasked():
    # 结构式域名校验：无 .tld 不命中
    assert redact_text("my user@example account") == "my user@example account"


def test_cn_phone_masked():
    assert redact_text("call 13800138000 now") == "call [PHONE] now"


def test_cn_phone_boundary_not_masked():
    assert redact_text("hotline 10086 service") == "hotline 10086 service"  # 5 位
    assert redact_text("code 1380 tail") == "code 1380 tail"  # 截断残留（宁漏）


def test_id_card_valid_masked():
    assert redact_text(f"证件号 {VALID_ID} 在此") == "证件号 [ID_CARD] 在此"


def test_bank_card_luhn_valid_masked():
    assert redact_text(f"卡号 {CARD16} 校验尾") == "卡号 [BANK_CARD] 校验尾"


def test_aws_key_masked():
    assert redact_text(f"key {AWS_EXAMPLE} here") == "key [AWS_KEY] here"


def test_api_key_masked():
    assert redact_text(f"token {API_EXAMPLE} ok") == "token [API_KEY] ok"


# ---------- E5：18 位并集门回归（身份证 × 银行卡 span 重叠） ----------


def test_e5_18digit_luhn_card_masked_as_bank():
    """E5 修复：18 位合法 Luhn 卡曾因 id 组先命中、校验不过后漏网。
    现在并集门必须把它掩为 [BANK_CARD]。"""
    assert luhn(L18_LUHN) is True
    assert id_checksum(L18_LUHN) is False  # 前提自检
    assert redact_text(f"卡号 {L18_LUHN} 结束") == "卡号 [BANK_CARD] 结束"


def test_18digit_valid_id_kept_as_id():
    assert redact_text(f"ID {VALID_ID} 号") == "ID [ID_CARD] 号"


# ---------- 宁漏不误伤：非 PII 数字不受影响 ----------


def test_dates_timestamps_order_version_not_masked():
    for s in ["2026-08-22", "20260822", "1730000000", "NO202608220001", "v2.4.7"]:
        assert redact_text(f"值 {s}") == f"值 {s}", f"{s} 不应被掩码"


def test_invalid_checksum_id_not_masked():
    bad = "110101198809011234"  # 末位 5→4：id 校验不过
    assert id_checksum(bad) is False
    assert luhn(bad) is False  # 前提自检（若偶发命中 bank 会在此暴露）
    assert redact_text(f"编号 {bad} 至此") == f"编号 {bad} 至此"


def test_long_alnum_block_no_pii_unchanged():
    """O(n²) 回归防护（对抗评审 #1）：无 `@` 的长纯字母/数字块必须原样返回。

    修复前 EMAIL 无界量词在无 @ 长串上逐位置回溯，2KB ~10ms 阻塞事件循环；
    这里只断言行为（原文不变），计时由审查手测验证线性化。
    """
    for s in ["x" * 2048, "9" * 2048, "abcdefghijkl" * 170]:
        assert redact_text(s) == s, f"len={len(s)} 的长块不应被改写"


# ---------- 边界：Unicode / 中文 / 幂等 ----------


def test_fullwidth_digits_not_masked():
    full = "电话 １３８００１３８０００"
    assert redact_text(full) == full  # 全角数字不应被 [0-9] 规则命中


def test_chinese_context_masked():
    assert redact_text("请联系 13800138000 或 a@b.com") == "请联系 [PHONE] 或 [EMAIL]"


def test_idempotent():
    s = f"邮件 a@b.com 电话 13800138000，证 {VALID_ID}，卡 {CARD16}"
    once = redact_text(s)
    assert redact_text(once) == once  # 掩码 token 不会被二次命中


def test_none_empty_whitespace_identity():
    assert redact_text("") == ""
    assert redact_text("   ") == "   "
    assert redact_text(None) is None
    assert _redact_safe(None) is None
    assert _redact_safe(123) == 123  # 非字符串原样（防御）


# ---------- maybe_redact 开关分支 ----------


def test_maybe_redact_disabled_identity():
    s = "contact a@b.com"
    assert maybe_redact(s, enabled=False) is s  # 未开启：同一对象原样
    assert maybe_redact(None, enabled=False) is None


def test_maybe_redact_enabled_masks():
    s = "contact a@b.com"
    assert maybe_redact(s, enabled=True) == "contact [EMAIL]"


# ---------- fail-open（spec B8 / plan P6） ----------


def test_redact_safe_fail_open_returns_original_and_counts(monkeypatch):
    def boom(text):
        raise RuntimeError("regex exploded")

    monkeypatch.setattr("app.services.redactor.redact_text", boom)

    def _count():
        # prometheus_client 的 Counter 内部是 MutexValue，不暴露 dict API；
        # 用 collect() 读样本值（无 label → 单样本）。
        for metric in PII_REDACT_FAILURES.collect():
            for sample in metric.samples:
                return sample.value
        return 0

    before = _count()
    assert _redact_safe("sensitive@example.com") == "sensitive@example.com"
    assert _count() == before + 1  # 失败计数 +1（可观测）


def test_masks_tokens_are_ascii_letters_only():
    # 幂等性关键约束：掩码 token 不含数字/@，保证不会被原规则二次命中
    # （下划线允许：ID_CARD）
    for token in MASKS.values():
        assert token[0] == "[" and token[-1] == "]"
        assert "@" not in token and not any(c.isdigit() for c in token)
        inner = token.strip("[]")
        assert inner and all(c.isascii() and (c.isalpha() or c == "_") for c in inner)
