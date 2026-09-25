"""증권의 보험사·상품명으로 적재된 약관을 고른다. 백엔드가 호출한다.

운영에서 증권과 약관을 잇는 것은 백엔드지만, 표기 흔들림을 흡수하는 규칙은 우리
쪽에만 있다. 실제로 이런 것들이 증권에서 나왔다.

    삼성화재해상보험주식회사   -> 삼성화재            법인 정식명칭
    해외여행보험 (Travel Overseas) -> 해외여행보험    영문 병기
    한화손해보험(주)           -> 캐롯 해외여행보험    제휴 판매, 인수사가 다름
    프로미 해외여행보험1        -> 프로미 해외여행보험Ⅰ  로마숫자

백엔드는 정규화만 하고 유사도 비교를 하지 않아 위 경우가 전부 NONE으로 떨어졌다.
연결 30건 중 8건이 그래서 빠졌다. 규칙을 양쪽에 두 벌로 두면 한쪽만 고쳐지므로,
우리가 가진 것을 그대로 열어 백엔드는 호출만 하게 한다.

후보는 DB(policy_terms)에서 읽는다. 레지스트리 파일이 아니라 실제로 적재된 것이
기준이어야, 파일에만 있고 DB에 없는 약관을 가리키는 일이 없다. 다만 별칭은 파일에만
있으므로(policy_terms에 그 컬럼이 없다) 같은 (보험사, 상품, 개정판)으로 붙여준다.
"""

import logging
from datetime import date

from app.core.config import get_settings
from app.services.terms_matcher import (
    NONE_MATCH,
    TermsMatch,
    PRODUCT_THRESHOLD,
    _product_score,
    find_terms,
    load_registry,
    normalize,
    pick_revision,
    same_insurer_entries,
)

logger = logging.getLogger(__name__)


def _alias_index() -> dict[tuple, dict]:
    """(보험사, 상품, 개정판) -> 레지스트리 항목. 별칭을 DB 행에 붙이는 데 쓴다."""
    try:
        entries = load_registry()
    except (OSError, ValueError, KeyError) as exc:
        logger.warning("레지스트리를 읽지 못해 별칭 없이 매칭합니다: %s", exc)
        return {}
    return {
        (normalize(e["insurer"]), normalize(e["product"]), e.get("revision") or ""): e
        for e in entries
    }


def _candidates() -> list[dict]:
    """적재된 VERIFIED 약관을 매처가 읽는 모양으로. id는 policy_terms.id(UUID)다."""
    dsn = get_settings().database_url
    if not dsn:
        # 파일 저장소로 도는 로컬·평가 환경. 이때 id는 레지스트리 id(파일명)다.
        return load_registry()

    from app.repositories.terms_repository import TermsRepository

    aliases = _alias_index()
    rows = TermsRepository(dsn).list_verified_terms()

    candidates = []
    for row in rows:
        key = (normalize(row["insurer_name"]), normalize(row["product_name"]), row["revision"] or "")
        registry_entry = aliases.get(key, {})
        candidates.append({
            "id": str(row["terms_id"]),
            "insurer": row["insurer_name"],
            "product": row["product_name"],
            "revision": row["revision"],
            "effective_date": row["effective_date"],
            "aliases": registry_entry.get("aliases", []),
            "underwriter_aliases": registry_entry.get("underwriter_aliases", []),
        })
    return candidates


def match(insurer: str, product: str | None, start_date: date | None) -> TermsMatch:
    """증권 1건에 맞는 약관. 못 찾아도 예외를 내지 않고 NONE을 돌려준다."""
    entries = _candidates()
    if not entries:
        logger.info("적재된 약관이 없습니다")
        return NONE_MATCH

    # 1. 상품까지 좁힌다. 여기서 표기 흔들림이 흡수된다.
    resolved = find_terms(insurer, product or "", registry=entries)
    if not resolved.is_usable or resolved.level == "INSURER":
        # 상품을 못 찾았으면 개정판을 고를 것도 없다.
        return resolved

    # 2. 같은 상품의 판들 중 가입일에 유효했던 것을 고른다.
    #
    # find_terms는 점수만 보고 하나를 주므로 판 선택은 따로 한다. 후보는 "이긴 상품과
    # 같은 점수"로 좁힌다 - 0.65로 넓게 잡으면 "해외여행보험"이 "365연간해외여행보험"과
    # 한 묶음이 되어 다른 상품의 판이 뽑힌다.
    same_insurer = same_insurer_entries(insurer, entries)
    score = max(_product_score(product or "", e) for e in same_insurer)
    same_product = [e for e in same_insurer if _product_score(product or "", e) >= score]

    chosen, level = pick_revision(same_product, start_date)
    return TermsMatch(
        chosen["id"], chosen["insurer"], chosen["product"], chosen.get("revision"),
        level, resolved.score,
    )
