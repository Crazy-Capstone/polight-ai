"""Mask common structured personal identifiers before external AI requests.

This is a defense-in-depth filter for free-form text. It is not a substitute for
backend-side name handling or document-level PDF redaction.
"""

import re


_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Korean resident registration numbers (with or without the hyphen).
    re.compile(r"(?<!\d)\d{6}[- ]?[1-4]\d{6}(?!\d)"),
    # Email addresses.
    re.compile(r"(?i)(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])"),
    # Korean mobile numbers, including the +82 country prefix.
    re.compile(r"(?<!\d)(?:\+?82[-. ]?)?0?(?:10|11|16|17|18|19)[-. ]?\d{3,4}[-. ]?\d{4}(?!\d)"),
    # Other Korean landline/fax numbers.
    re.compile(r"(?<!\d)0(?:2|3[1-3]|4[1-4]|5[1-5]|6[1-4]|70)[-. ]?\d{3,4}[-. ]?\d{4}(?!\d)"),
    # Labeled policy, contract, account, passport, card, and registration numbers.
    re.compile(
        r"(?i)(?:보험\s*)?(?:증권|계약|계좌|통장|여권|카드|주민등록|외국인등록|등록)\s*번호"
        r"\s*[:：#]?\s*(?:[A-Z0-9][A-Z0-9-]{3,31}|\d[\d -]{2,30}\d)"
    ),
    # Long digit-only identifiers, including account/card/policy numbers.
    re.compile(r"(?<!\d)(?:\d[ -]?){9,18}\d(?!\d)"),
    # Birth dates when explicitly identified as such.
    re.compile(
        r"(?:생년월일|생일|출생일)\s*[:：]?\s*"
        r"(?:19|20)\d{2}[./년 -]\s?\d{1,2}[./월 -]\s?\d{1,2}일?"
    ),
)


def redact_pii(text: str) -> str:
    """Mask common structured identifiers without changing surrounding context."""
    redacted = text
    for pattern in _PATTERNS:
        redacted = pattern.sub("[개인정보]", redacted)
    return redacted

