import logging

from fastapi import APIRouter

from app.schemas.terms_match import TermsMatchRequest, TermsMatchResponse
from app.services import terms_match_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["terms"])


@router.post(
    "/terms/match",
    response_model=TermsMatchResponse,
    summary="증권 -> 약관 매칭",
    description="증권에서 읽은 보험사·상품명으로 적재된 약관을 고른다. "
    "표기 흔들림(법인 정식명칭·영문 병기·로마숫자·제휴 인수사)을 흡수하고, "
    "개정판이 여럿이면 보험 시작일에 유효했던 판을 고른다. "
    "못 찾아도 실패로 처리하지 않고 level=NONE으로 돌려준다.",
)
def match_terms(request: TermsMatchRequest) -> TermsMatchResponse:
    result = terms_match_service.match(
        request.insurer_name, request.product_name, request.insurance_start_date
    )

    # 값은 찍지 않는다. 증권에서 온 보험사·상품명은 개인정보는 아니지만,
    # 매칭 실패를 쫓을 때 필요한 것은 판정과 어느 약관을 골랐는지다.
    logger.info(
        "약관 매칭: %s -> %s (%s)",
        result.level,
        result.terms_id or "없음",
        result.product or "-",
    )

    return TermsMatchResponse(
        terms_id=result.terms_id or None,
        level=result.level,
        notice=result.notice,
        insurer_name=result.insurer or None,
        product_name=result.product or None,
        revision=result.revision,
    )
