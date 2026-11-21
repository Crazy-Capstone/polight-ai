"""증권 분석 결과에서 개인정보를 제거하거나 재사용 가능한 문구를 정리한다."""

import re
from collections.abc import Iterable
from typing import Any

from app.schemas.analysis import CoverageItemPayload

MASK = "***"

# 짧고 흔한 키는 전체가 일치할 때만 민감정보로 본다.
EXACT = frozenset({
    "age", "name", "gender", "sex", "tel", "zip", "card", "account",
    "나이", "성별", "이름", "주소", "전화", "메일", "우편번호",
})

# 길고 구별되는 개인정보 키 조각.
SENSITIVE = (
    "insured", "payer", "holder", "applicant", "beneficiary", "representative",
    "성명", "피보험자", "계약자", "수익자", "가입자", "대표자",
    "birth", "생년", "생일", "ssn", "주민", "resident", "여권", "passport",
    "phone", "mobile", "연락처", "휴대", "email", "address", "우편",
    "policyno", "policynumber", "증권번호", "계약번호", "certno", "certificateno",
    "계좌", "카드번호",
)

# 보험사·상품·담보 이름은 분석과 연결에 필요하므로 보존한다.
KEEP = (
    "productname", "insurername", "coveragename", "companyname", "planname",
    "특약명", "담보명",
)

TEXT_PATTERNS = (
    (re.compile(r"\d{6}\s*-\s*\d{7}"), "******-*******"),
    (re.compile(r"(?:01[016-9]|0[2-9]\d?)-?\d{3,4}-?\d{4}"), "***-****-****"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "***@***"),
    (
        re.compile(r"(?:피보험자|계약자|수익자|가입자|성명|이름)\s*[:：]?\s*[가-힣]{2,4}"),
        "개인정보 ***",
    ),
    (
        re.compile(r"(?:생년월일|생년|DOB)\s*[:：]?\s*\d{4}[.-]?\d{2}[.-]?\d{2}", re.I),
        "생년월일 ***",
    ),
    (
        re.compile(r"(?:증권번호|계약번호|여권번호|계좌번호|카드번호)\s*[:：]?\s*[A-Z0-9-]{5,}", re.I),
        "식별번호 ***",
    ),
)


def _normalized_key(key: str) -> str:
    return re.sub(r"[\s_-]", "", key).lower()


def is_sensitive(key: str) -> bool:
    lowered = _normalized_key(key)
    if any(item in lowered for item in KEEP):
        return False
    return lowered in EXACT or any(item in lowered for item in SENSITIVE)


def _is_age_key(key: str) -> bool:
    lowered = _normalized_key(key)
    return lowered == "age" or "age" in lowered or "나이" in lowered


def _string_leaves(node: Any) -> Iterable[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _string_leaves(value)
    elif isinstance(node, list):
        for value in node:
            yield from _string_leaves(value)


def collect_sensitive_values(node: Any) -> set[str]:
    """민감 필드에 들어 있는 문자열을 모아 다른 출력 문자열에서도 가린다."""
    values: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if is_sensitive(str(key)) and not _is_age_key(str(key)):
                for candidate in _string_leaves(value):
                    candidate = candidate.strip()
                    if len(candidate) >= 3 and not candidate.isdigit():
                        values.add(candidate)
                    elif candidate.isdigit() and len(candidate) >= 6:
                        values.add(candidate)
            values.update(collect_sensitive_values(value))
    elif isinstance(node, list):
        for value in node:
            values.update(collect_sensitive_values(value))
    return values


def scrub_text(value: str, sensitive_values: Iterable[str] = ()) -> str:
    for pattern, replacement in TEXT_PATTERNS:
        value = pattern.sub(replacement, value)
    for secret in sorted(set(sensitive_values), key=len, reverse=True):
        if secret:
            value = value.replace(secret, MASK)
    return value


def sanitize_tree(node: Any, sensitive_values: Iterable[str]) -> Any:
    if isinstance(node, dict):
        return {key: sanitize_tree(value, sensitive_values) for key, value in node.items()}
    if isinstance(node, list):
        return [sanitize_tree(value, sensitive_values) for value in node]
    if isinstance(node, str):
        return scrub_text(node, sensitive_values)
    return node


def sanitize_certificate_text(value: str | None, certificate: dict) -> str | None:
    if value is None:
        return None
    return scrub_text(value, collect_sensitive_values(certificate))


def sanitize_certificate_payloads(
    certificate: dict,
    payloads: list[CoverageItemPayload],
) -> list[CoverageItemPayload]:
    """추출 결과 텍스트에서 증권 원본의 식별정보가 되풀이되면 제거한다."""
    sensitive_values = collect_sensitive_values(certificate)
    cleaned: list[CoverageItemPayload] = []
    for payload in payloads:
        data = sanitize_tree(payload.model_dump(), sensitive_values)
        cleaned.append(CoverageItemPayload.model_validate(data))
    return cleaned


def redact(node: Any, removed: list[str], path: str = "") -> Any:
    """민감 키의 값은 마스킹하고, 나머지 문자열에서도 알려진 값과 패턴을 가린다."""
    return _redact_with_values(node, removed, path, collect_sensitive_values(node))


def _redact_with_values(node: Any, removed: list[str], path: str, sensitive_values: set[str]) -> Any:
    if isinstance(node, dict):
        result = {}
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            if is_sensitive(str(key)):
                removed.append(here)
                result[key] = MASK
            else:
                result[key] = _redact_with_values(value, removed, here, sensitive_values)
        return result
    if isinstance(node, list):
        return [
            _redact_with_values(value, removed, f"{path}[{i}]", sensitive_values)
            for i, value in enumerate(node)
        ]
    if isinstance(node, str):
        return scrub_text(node, sensitive_values)
    return node
