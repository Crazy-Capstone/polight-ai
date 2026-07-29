import json
import logging
from pathlib import Path

import httpx

from app.schemas.analysis import AnalysisCompleteCallback, AnalysisFailCallback, CoverageItemPayload
from app.schemas.analysis import AnalysisStartRequest
from app.services.chunking_service import chunk_pages
from app.services.embedding_service import embed_chunks
from app.services.pdf_service import extract_pages

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_PDF_DIR = PROJECT_ROOT / "data" / "raw_pdfs"
CHUNKS_DIR = PROJECT_ROOT / "data" / "chunks"
EMBEDDINGS_DIR = PROJECT_ROOT / "data" / "embeddings"


def _download_pdf(download_url: str, document_id: str) -> Path:
    RAW_PDF_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = RAW_PDF_DIR / f"{document_id}.pdf"

    with httpx.stream("GET", download_url, timeout=30.0) as response:
        response.raise_for_status()
        with pdf_path.open("wb") as f:
            for data in response.iter_bytes():
                f.write(data)

    return pdf_path


# DB 연결 전까지는 chunk/embedding 결과를 로컬 파일로 남겨 확인 가능하게 함.
# 내일 pgvector 연결되면 이 저장 로직을 repositories/ 구현체 호출로 교체.
def _save_results(analysis_result_id: str, chunks: list[dict], embeddings: dict[str, list[float]]) -> None:
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)

    with (CHUNKS_DIR / f"{analysis_result_id}_chunks.json").open("w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)

    with (EMBEDDINGS_DIR / f"{analysis_result_id}_embeddings.json").open("w", encoding="utf-8") as f:
        json.dump(embeddings, f, ensure_ascii=False)


# TODO(6단계): app/clients/spring_client.py로 교체해 실제 Spring 콜백 API를 호출.
# 지금은 계약(AnalysisCompleteCallback/Fail)만 만들어서 로그로 확인.
def _notify_complete(analysis_result_id: str, chunks: list[dict]) -> None:
    payload = AnalysisCompleteCallback(
        analysisResultId=analysis_result_id,
        summary=f"chunk {len(chunks)}개 생성 완료",
        coverageItems=[
            CoverageItemPayload(
                title=chunk["section_title"],
                coverageStatus=chunk["coverage_type"],
                sourceChunkIds=[chunk["chunk_id"]],
            )
            for chunk in chunks
            if chunk.get("matched_category")
        ],
    )
    logger.info("analysis complete (stub callback): %s", payload.model_dump(by_alias=True))


def _notify_fail(analysis_result_id: str, error_message: str) -> None:
    payload = AnalysisFailCallback(analysisResultId=analysis_result_id, errorMessage=error_message)
    logger.error("analysis failed (stub callback): %s", payload.model_dump(by_alias=True))


# BackgroundTasks로 호출되는 진입점. 여기서 예외를 삼켜서 fail 콜백으로 전환한다
# (그렇지 않으면 백그라운드 태스크 예외가 조용히 로그에만 남고 Spring은 영영 응답을 못 받음).
def process_analysis(request: AnalysisStartRequest) -> None:
    try:
        pdf_path = _download_pdf(request.download_url, request.document_id)
        pages = extract_pages(pdf_path)
        chunks = chunk_pages(pages, source_file=pdf_path.name)
        embeddings = embed_chunks(chunks)
        _save_results(request.analysis_result_id, chunks, embeddings)
        _notify_complete(request.analysis_result_id, chunks)
    except Exception as e:
        logger.exception("analysis pipeline failed: %s", request.analysis_result_id)
        _notify_fail(request.analysis_result_id, str(e))
