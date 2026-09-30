"""증권과 보유 약관을 대조해, 약관을 다시 봐야 하는 경우만 기록한다.

약관 수집을 정기 점검으로 하면 아무 일이 없는데도 매번 전체를 훑게 된다. 아무도
그 상품 증권을 올리지 않았다면 그 약관이 구판이어도 서비스 영향이 없으므로,
개정판을 미리 갖고 있어야 할 이유가 없다.

그래서 사용자가 올리는 증권을 트리거로 쓴다. 증권 분석에서 보험사·상품명·보험
시작일이 나오는데, 그걸 policy_terms와 맞춰보면 세 갈래로 갈린다.

    MISSING_REVISION 가입 시점 판이 없다(보유 판이 모두 가입일보다 뒤에 시행)
                     -> 확정이다. 챗봇이 지금 다른 판으로 답하고 있다
    STALE            보유 최신판이 가입일보다 1년 이상 오래됐다
                     -> 개정판이 나왔는지 공시실 목록의 시행일만 확인한다
    MISSING_PRODUCT  그 회사 약관은 있는데 이 상품이 없다
                     -> 상품 하나만 받으면 된다
    MISSING_INSURER  그 회사 약관이 아예 없다
                     -> 회사 단위로 수집하거나, 사용자에게 업로드를 안내한다

"시행일보다 가입일이 뒤"는 신호가 아니다. 그게 정상이다 - 2025-06-30 시행판으로
2025-07-01에 가입하는 것이 제대로 된 상태다. 그것을 신호로 쓰면 알림이 전부 참이
되어 쓸모가 없어진다. 그래서 확정(MISSING_REVISION)과 임계값을 넘은 의심(STALE)만
올린다.

운영에서 증권과 약관을 잇는 것은 백엔드지만(analysis_results.matched_terms_id),
"우리가 그 약관을 갖고 있는가"는 우리 쪽 재고 문제다. policy_terms SELECT 권한이
있어 백엔드에 요청하지 않고 직접 확인한다.

기록은 data/terms_alerts.jsonl에 한 줄씩 쌓인다. 사용자를 식별할 값(analysis_result_id,
document_id, user_id)은 넣지 않는다. 알고 싶은 것은 "어느 약관을 확인해야 하나"이고
"누가 올렸나"가 아니다.

    python scripts/show_terms_alerts.py
"""

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.services.terms_matcher import INSURER_THRESHOLD, PRODUCT_THRESHOLD, similarity

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALERTS_PATH = PROJECT_ROOT / "data" / "terms_alerts.jsonl"

OK = "OK"
# 가입 시점에 유효했던 판을 우리가 갖고 있지 않다. 보유 판이 모두 가입일보다 뒤에
# 시행됐다는 뜻이므로 확정이다. 챗봇이 실제로 다른 판으로 답하고 있다.
MISSING_REVISION = "MISSING_REVISION"
# 보유 최신판이 가입일보다 한참 오래됐다. 그 사이 개정판이 나왔을 가능성이 크다.
# 확정은 아니라서 공시실 확인이 필요하다.
STALE = "STALE"
MISSING_PRODUCT = "MISSING_PRODUCT"
MISSING_INSURER = "MISSING_INSURER"

# STALE로 볼 간격.
#
# 이 임계값이 없으면 거의 모든 증권이 걸린다. 가입일은 약관 시행일보다 뒤인 것이
# 정상이기 때문이다 - 2025-06-30 시행판으로 2025-07-01에 가입하는 것이 제대로 된
# 상태다. "시행일보다 가입일이 뒤"를 신호로 쓰면 알림이 전부 참이 되어 쓸모가 없다.
#
# 여행자보험 개정은 연 1~2회 수준이라, 1년 이상 벌어지면 그 사이에 개정이
# 있었을 확률이 높다. 놓치는 쪽(개정됐는데 알림이 안 뜸)은 다음 증권이나
# 표준약관 개정 점검에서 다시 걸린다.
STALE_AFTER_DAYS = 365
# 판단할 근거가 없는 경우. 증권에서 보험사·상품명을 못 읽었거나, 보유 약관의
# effective_date가 비어 있어 비교가 안 된다. 조치 대상이 아니라 관찰 대상이다.
UNKNOWN = "UNKNOWN"
# 챗봇이 termsId 없이 질의를 받은 경우. 그 여행에 연결된 약관이 없다는 뜻이다.
# 보험사·상품명이 요청에 없어 어느 약관인지는 알 수 없고, 빈도만 의미가 있다.
NO_TERMS_ID = "NO_TERMS_ID"

# 조치가 필요한 판정들. 나머지는 기록만 하고 목록에 올리지 않는다.
#
# 순서가 곧 우선순위다(show_terms_alerts.py가 이 순서로 정렬한다).
# 기준은 사용자가 보는 손해 × 조치 비용이다.
#   MISSING_REVISION  틀린 판으로 답하고 있다. 가장 나쁘다
#   MISSING_PRODUCT   근거 없이 답한다. 상품 1건만 받으면 끝난다
#   MISSING_INSURER   근거 없이 답한다. 회사 단위 수집이라 무겁다
#   STALE             확정이 아닌 의심. 확인만 해보면 된다
ACTIONABLE = (MISSING_REVISION, MISSING_PRODUCT, MISSING_INSURER, STALE)


def _append(record: dict) -> None:
    record["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ALERTS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


# 증권의 보험사·상품명으로 보유 약관 후보를 고른다.
#
# 매칭(terms_matcher)과 같은 규칙을 쓴다. 규칙이 갈리면 "챗봇은 이 약관으로 답하고
# 있는데 재고 대조는 없다고 한다" 같은 상태가 된다. 그 상태에서는 알림을 믿을 수
# 없어, 목록을 만들어 둔 목적 자체가 사라진다.
#
# 글자 그대로 비교하지 않는 이유는 표기가 흔들리기 때문이다.
#   증권 "DB손해보험(주)" / "DB손보"      약관 "DB손해보험"
#   증권 "프로미 해외여행보험1"            약관 "프로미 해외여행보험Ⅰ"
def _candidates(
    insurer_name: str, product_name: str | None, held: list[dict]
) -> tuple[list[dict], list[dict]]:
    """(상품까지 맞는 약관, 같은 보험사 약관)."""
    insurer_rows = [
        r
        for r in held
        if similarity(insurer_name, r["insurer_name"], strip_insurer=True) >= INSURER_THRESHOLD
    ]
    if not product_name:
        # 증권에서 상품명을 못 읽었다. 대조할 대상을 정할 수 없으므로 상품 후보는 비운다.
        return [], insurer_rows

    rows = [
        r for r in insurer_rows if similarity(product_name, r["product_name"]) >= PRODUCT_THRESHOLD
    ]
    return rows, insurer_rows


def _verdict(rows: list[dict], insurer_rows: list[dict], start: date | None) -> tuple[str, str]:
    """(판정, 사람이 읽을 이유)."""
    if not rows:
        if insurer_rows:
            return MISSING_PRODUCT, "그 보험사 약관은 있으나 이 상품이 없습니다."
        return MISSING_INSURER, "그 보험사 약관이 없습니다."

    dates = [r["effective_date"] for r in rows if r["effective_date"]]
    if not dates:
        return UNKNOWN, "보유 약관에 시행일이 비어 있어 개정 여부를 비교할 수 없습니다."
    if not start:
        return UNKNOWN, "증권에서 보험 시작일을 읽지 못해 비교할 수 없습니다."

    # 보유 판이 모두 가입일보다 뒤에 시행됐다. 가입 시점 판이 우리에게 없다는
    # 뜻이고, 챗봇은 지금 다른 판으로 답하고 있다. 추정이 아니라 확정이다.
    if min(dates) > start:
        return MISSING_REVISION, (
            f"가입일({start}) 시점 판이 없습니다. 보유한 가장 오래된 판이 {min(dates)}입니다."
        )

    latest = max(dates)
    gap = (start - latest).days
    if gap > STALE_AFTER_DAYS:
        return STALE, (
            f"보유 최신판({latest})이 가입일({start})보다 {gap}일 오래됐습니다. "
            "그 사이 개정판이 나왔는지 확인이 필요합니다."
        )
    return OK, ""


# 증권 1건을 보유 약관과 대조한다. 판정 문자열을 돌려주고, 조치가 필요하면 기록한다.
#
# 실패해도 예외를 내지 않는다. 이건 운영 편의를 위한 기록일 뿐이라, 이것 때문에
# 증권 분석이 죽으면 사용자가 결과를 못 받는다. 그쪽이 훨씬 나쁘다.
def check_certificate(
    insurer_name: str | None,
    product_name: str | None,
    start_date: str | None,
) -> str:
    try:
        dsn = get_settings().database_url
        if not dsn:
            # 파일 저장소로 도는 로컬·평가 환경. 대조할 policy_terms가 없다.
            return UNKNOWN
        if not insurer_name:
            _append({
                "verdict": UNKNOWN,
                "insurer": insurer_name,
                "product": product_name,
                "reason": "증권에서 보험사명을 읽지 못했습니다.",
            })
            return UNKNOWN

        from app.repositories.terms_repository import TermsRepository

        repo = TermsRepository(dsn)
        rows, insurer_rows = _candidates(insurer_name, product_name, repo.list_verified_terms())

        if insurer_rows and not product_name:
            # 그 보험사 약관은 있는데 증권에서 상품명을 못 읽었다. 이걸 MISSING_PRODUCT로
            # 올리면 "상품 하나만 받으면 된다"는 뜻이 되는데, 실제로는 받을 상품을
            # 모른다. 조치 대상이 아니라 관찰 대상이다.
            verdict, reason = UNKNOWN, "증권에서 상품명을 읽지 못해 어느 약관과 대조할지 정할 수 없습니다."
        else:
            verdict, reason = _verdict(rows, insurer_rows, _parse_date(start_date))
        if verdict == OK:
            return verdict

        _append({
            "verdict": verdict,
            "insurer": insurer_name,
            "product": product_name,
            # 대조에 쓴 값. 개인을 식별하지 않고, 어느 시점 기준인지만 알려준다.
            "certificate_start_date": start_date,
            "held_revisions": [
                {
                    "product": r["product_name"],
                    "revision": r["revision"],
                    "effective_date": r["effective_date"].isoformat() if r["effective_date"] else None,
                }
                for r in (rows or insurer_rows)[:5]
            ],
            "reason": reason,
        })
        logger.info("약관 확인 필요 [%s] %s / %s - %s", verdict, insurer_name, product_name, reason)
        return verdict

    except Exception as exc:
        logger.warning("약관 대조에 실패했습니다(분석은 계속): %s", exc)
        return UNKNOWN


# 챗봇이 termsId 없이 질의를 받았다.
#
# 어느 약관이 필요한지는 알 수 없다 - 요청에 보험사·상품명이 없다. 그래도 빈도는
# 의미가 있다. 이 값이 크면 "약관을 못 찾아 근거 없이 답하는" 질의가 그만큼 많다는
# 뜻이고, 그게 사용자가 실제로 겪는 손해다.
def record_missing_terms_id() -> None:
    try:
        _append({"verdict": NO_TERMS_ID})
    except Exception as exc:
        logger.warning("termsId 없음 기록에 실패했습니다: %s", exc)
