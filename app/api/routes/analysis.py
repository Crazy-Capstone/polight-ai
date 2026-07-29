from fastapi import APIRouter, status

from app.schemas.analysis import AnalysisStartRequest

router = APIRouter(tags=["analysis"])


# TODO(4~6단계): 실제로는 백그라운드로 문서 다운로드/파싱/청킹/임베딩 후 Spring에 완료 콜백.
# 지금은 요청을 받아 즉시 접수 응답만 반환하는 계약 검증용 더미.
@router.post("/analysis", status_code=status.HTTP_202_ACCEPTED)
def start_analysis(request: AnalysisStartRequest) -> dict[str, str]:
    return {
        "analysisResultId": request.analysis_result_id,
        "status": "ACCEPTED",
    }
