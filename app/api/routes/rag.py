import logging

from fastapi import APIRouter, Depends, Request

from app.repositories import VectorRepository, get_vector_repository
from app.schemas.rag import RagQueryRequest, RagQueryResponse
from app.services.rag_service import answer_question

logger = logging.getLogger(__name__)

router = APIRouter(tags=["rag"])


# 챗봇 요청에 termsId/coverages가 실제로 어떤 값으로 도착하는지 진단한다.
#
# 백엔드는 보낸다고 하고 챗봇은 증권 정보를 못 쓰는 상황이라, 원인을 셋으로 가른다:
#   키가 없음   -> 백엔드가 필드를 안 보냄
#   값이 비어옴  -> 그 여행에 완료된 증권 분석이 없음(데이터)
#   오는데 안 씀 -> AI 쪽
# 개인정보(userId 등)는 찍지 않고, 키 존재/개수/길이만 남긴다.
def _log_request_shape(raw: dict) -> None:
    terms = raw.get("termsId", raw.get("terms_id", "<<키없음>>"))
    if isinstance(terms, str):
        terms_repr = f"len={len(terms)}" if terms else "빈문자열"
    else:
        terms_repr = repr(terms)
    if "coverages" in raw:
        cov = raw["coverages"]
        cov_repr = f"개수={len(cov)}" if isinstance(cov, list) else repr(cov)
    else:
        cov_repr = "<<키없음>>"
    logger.info(
        "챗봇 요청 진단: termsId=%s, coverages=%s, coveragesComplete=%s, 최상위키=%s",
        terms_repr, cov_repr, raw.get("coveragesComplete", raw.get("coverages_complete", "<<키없음>>")),
        sorted(raw.keys()),
    )


# 저장소는 Depends로만 받는다. pgvector로 갈아탈 때 get_vector_repository() 안에서
# 구현체만 바꾸면 되고, 이 라우터와 rag_service는 수정할 필요가 없다.
@router.post(
    "/rag/query",
    response_model=RagQueryResponse,
    summary="약관 질의응답",
    description="질문을 임베딩해 해당 계약(policyId)의 약관 청크를 검색하고, "
    "보장 조항에 짝지어진 면책 조항을 함께 근거로 넣어 답변을 생성한다. "
    "응답의 sources는 LLM이 생성한 문장이 아니라 검색된 원문에서 잘라낸 인용이다.",
)
async def query_policy(
    request: RagQueryRequest,
    http_request: Request,
    repository: VectorRepository = Depends(get_vector_repository),
) -> RagQueryResponse:
    # 파싱 전 원본 body로 키 존재 여부까지 진단한다(파싱된 모델은 키 없음/빈값을 구분 못 함).
    try:
        _log_request_shape(await http_request.json())
    except Exception as exc:  # 로깅 실패가 요청을 죽이면 안 된다
        logger.warning("요청 진단 로깅 실패: %s", exc)
    return answer_question(request, repository)
