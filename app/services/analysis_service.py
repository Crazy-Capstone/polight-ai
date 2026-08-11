import json
import logging
import time
from pathlib import Path

import httpx

from app.clients import spring_client
from app.clients.spring_client import SpringCallbackError
from app.core.auth import HEADER_NAME
from app.core.config import get_settings
from app.repositories import VectorRepository, get_vector_repository
from app.repositories.base import ChunkScope
from app.schemas.analysis import (
    AnalysisCompleteCallback,
    AnalysisFailCallback,
    AnalysisStartRequest,
)
from app.services.callback_mapper import to_payload
from app.services.chunking_service import parse_and_chunk
from app.services.coverage_extractor import extract_all
from app.services.embedding_providers import get_provider
from app.services.embedding_service import embed_chunks

# policy_chunks.embedding이 vector(1536)이고, 우리가 쓰는 upstage-1536도 1536이다.
# provider.dimensions가 비어 있는(축소하지 않는) 벤더를 쓸 때의 기본값.
DEFAULT_EMBEDDING_DIMENSION = 1536

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_PDF_DIR = PROJECT_ROOT / "data" / "raw_pdfs"


# 약관 PDF를 받아온다.
#
# 주소가 어떤 형태로 올지 확정되지 않았다. S3 presigned URL이면 인증이 필요 없고,
# Spring이 직접 파일을 내려주는 엔드포인트면 내부 API 키가 필요하다.
# 어느 쪽인지 물어보고 기다리는 대신, 키가 있으면 항상 헤더에 실어 보낸다.
# presigned URL은 서명에 포함되지 않은 헤더를 무시하므로 있어도 무해하다.
#
# 재시도를 두는 이유는 이 단계 실패가 곧 분석 전체 실패이기 때문이다.
# 일시적인 네트워크 문제로 약관 하나를 통째로 날리는 것은 아깝다.
DOWNLOAD_MAX_ATTEMPTS = 3


def _download_pdf(download_url: str, document_id: str) -> Path:
    RAW_PDF_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = RAW_PDF_DIR / f"{document_id}.pdf"

    settings = get_settings()
    headers = (
        {HEADER_NAME: settings.internal_api_key} if settings.internal_api_key else {}
    )

    last_error: Exception | None = None
    for attempt in range(1, DOWNLOAD_MAX_ATTEMPTS + 1):
        try:
            with httpx.stream(
                "GET", download_url, headers=headers, timeout=60.0, follow_redirects=True
            ) as response:
                response.raise_for_status()
                with pdf_path.open("wb") as f:
                    for data in response.iter_bytes():
                        f.write(data)
            return pdf_path
        except httpx.HTTPError as e:
            last_error = e
            # 4xx는 다시 받아도 같다. 주소가 틀렸거나 만료됐거나 권한이 없다.
            if isinstance(e, httpx.HTTPStatusError) and 400 <= e.response.status_code < 500:
                break
            if attempt < DOWNLOAD_MAX_ATTEMPTS:
                logger.warning("PDF 내려받기 실패 (%d/%d): %s", attempt, DOWNLOAD_MAX_ATTEMPTS, e)
                time.sleep(2.0 * attempt)

    raise RuntimeError(f"약관 PDF를 내려받지 못했습니다 ({download_url}): {last_error}")


# 화면에 노출되는 요약 문구.
#
# analysis_results.summary는 현재 프론트에 보이는 유일한 분석 텍스트다.
# "청크 268개" 같은 내부 수치를 쓰면 사용자에게 의미가 없으므로 담보 이름을 넣는다.
def _build_summary(items: list) -> str:
    if not items:
        return "약관에서 보장 항목을 찾지 못했습니다. 약관 형식을 확인해 주세요."

    names = ", ".join(item.title for item in items[:3])
    if len(items) > 3:
        names += f" 외 {len(items) - 3}건"
    return f"보장 항목 {len(items)}개를 확인했습니다: {names}"


# 청크를 그대로 뒤집어 보내던 방식을 카테고리 기반 추출로 교체했다.
#
# 이전에는 청크 하나가 보장 항목 하나가 돼서, "제33조(약관의 해석)"이나 "손해액 ×" 같은
# 조항 제목 조각이 130개 넘게 나갔다. coverage_items 테이블에 넣어도 화면에 쓸 수 없다.
# 이제는 카테고리별로 조각을 모아 담보 단위로 정리하므로 "해외여행중 휴대품손해 특별약관"
# 같은 실제 담보명이 나오고 한도 금액도 뽑힌다.
#
# 콜백 전송 자체의 실패(Spring 다운 등)는 파이프라인 성공/실패와 별개 문제이므로
# 여기서 삼키고 로그만 남긴다. process_analysis의 예외 처리로 되돌리지 않는다.
def _safe_notify_complete(
    analysis_result_id: str,
    chunks: list[dict],
    chunk_id_map: dict[str, str] | None = None,
) -> None:
    items, warnings = extract_all(chunks)
    for warning in warnings:
        logger.warning("보장항목 추출 경고: %s", warning)

    payloads = [to_payload(item, chunk_id_map or {}) for item in items]

    settings = get_settings()
    provider = get_provider(settings.embedding_provider)
    payload = AnalysisCompleteCallback(
        analysisResultId=analysis_result_id,
        summary=_build_summary(items),
        coverageItems=payloads,
        # analysis_results에 자리가 있는데 비어 있던 컬럼들.
        # 어떤 모델로 만든 임베딩인지 남겨두지 않으면, 모델을 바꿨을 때
        # 어느 문서를 재색인해야 하는지 알 수 없다.
        embeddingModel=provider.doc_model,
        embeddingDimension=provider.dimensions or DEFAULT_EMBEDDING_DIMENSION,
        # 디버깅·재처리용 원본. Spring이 파싱하지 않고 TEXT로 보관한다.
        rawResultJson=json.dumps(
            [p.model_dump(by_alias=True) for p in payloads], ensure_ascii=False
        ),
    )
    try:
        spring_client.notify_complete(payload)
    except SpringCallbackError:
        logger.error("완료 콜백 전달 실패 (분석 자체는 성공): %s", analysis_result_id)


def _safe_notify_fail(analysis_result_id: str, error_message: str) -> None:
    payload = AnalysisFailCallback(analysisResultId=analysis_result_id, errorMessage=error_message)
    try:
        spring_client.notify_fail(payload)
    except SpringCallbackError:
        logger.error("실패 콜백 전달도 실패: %s", analysis_result_id)


# BackgroundTasks로 호출되는 진입점.
#
# repository를 인자로 받는 이유는 두 가지다. (1) 테스트에서 가짜 저장소를 넣어 DB/API 없이
# 파이프라인을 검증할 수 있고, (2) pgvector로 갈아탈 때 이 함수를 수정할 필요가 없다.
#
# 저장이 콜백보다 먼저 일어나야 한다. Spring의 coverage_item_sources가 chunk_id를 FK로
# 참조하기 때문에, 청크가 없는 상태에서 완료 콜백을 보내면 Spring 쪽 INSERT가 실패한다.
def process_analysis(
    request: AnalysisStartRequest,
    repository: VectorRepository | None = None,
) -> None:
    repository = repository or get_vector_repository()

    scope = ChunkScope(
        user_id=request.user_id,
        trip_id=request.trip_id,
        document_id=request.document_id,
        policy_id=request.policy_id,
    )

    try:
        pdf_path = _download_pdf(request.download_url, request.document_id)
        chunks = parse_and_chunk(pdf_path, scope=scope)
        embeddings = embed_chunks(chunks)
        repository.save(
            chunks,
            embeddings,
            analysis_result_id=request.analysis_result_id,
            scope=scope,
        )
    except Exception as e:
        logger.exception("analysis pipeline failed: %s", request.analysis_result_id)
        _safe_notify_fail(request.analysis_result_id, str(e))
        return

    # pgvector 저장소는 INSERT하며 policy_chunks.id(UUID)를 만들고 청크에 실어준다.
    # coverage_item_sources가 그 UUID를 FK로 참조하므로 콜백에 그대로 전달한다.
    # 저장소 인스턴스가 아니라 청크에서 읽는 이유는, 저장소가 싱글턴이라
    # 동시 분석 시 인스턴스 상태를 쓰면 서로 덮어쓰기 때문이다.
    chunk_id_map = {
        c["chunk_id"]: c["policy_chunk_id"] for c in chunks if c.get("policy_chunk_id")
    }
    _safe_notify_complete(request.analysis_result_id, chunks, chunk_id_map)
