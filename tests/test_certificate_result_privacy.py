"""증권 분석 콜백은 필요한 보장 데이터만 유지하고 개인정보 원문을 보내지 않는다."""

import json
from pathlib import Path

from app.schemas.analysis import AnalysisStartRequest
from app.services import analysis_service


CERTIFICATE = {
    "insurer_name": "DB손해보험",
    "product_name": "여행자보험 표준형",
    "insured_person_name": "홍길동",
    "insured_person_age": 47,
    "birth_date": "1990-01-02",
    "policy_number": "POLICY-99881-X",
    "insurance_period_start_datetime": "2026-07-30 20:00",
    "insurance_period_end_datetime": "2026-08-02 23:00",
    "coverage_table_complete": True,
    "coverage_table": [
        {
            "coverage_name": "해외여행중 휴대품손해",
            "coverage_amount": "100만원",
            "coverage_conditions": (
                "피보험자 홍길동, 생년월일 1990-01-02, 주민번호 900101-1234567, "
                "전화 010-1234-5678, 증권번호 POLICY-99881-X"
            ),
        }
    ],
}


def test_certificate_callback_omits_raw_result_and_scrubs_identifiers(monkeypatch):
    callbacks = []
    monkeypatch.setattr(analysis_service, "analyze_certificate", lambda _: CERTIFICATE)
    monkeypatch.setattr(analysis_service.terms_watch, "check_certificate", lambda *args: None)
    monkeypatch.setattr(
        analysis_service.spring_client,
        "notify_complete",
        lambda callback: callbacks.append(callback),
    )

    request = AnalysisStartRequest(
        analysisResultId="analysis-safe-id",
        documentId="document-safe-id",
        downloadUrl="https://storage.test/certificate.pdf",
        userId="user-safe-id",
        tripId="trip-safe-id",
    )

    analysis_service._process_certificate(request, Path("certificate.pdf"))

    assert len(callbacks) == 1
    callback = callbacks[0]
    payload = callback.model_dump(by_alias=True)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["rawResultJson"] is not None
    raw_result = json.loads(payload["rawResultJson"])
    assert raw_result["insured_person_name"] == "***"
    assert raw_result["policy_number"] == "***"
    assert raw_result["coverage_table"][0]["coverage_name"] == "해외여행중 휴대품손해"
    for secret in (
        "홍길동",
        "1990-01-02",
        "POLICY-99881-X",
        "900101-1234567",
        "010-1234-5678",
    ):
        assert secret not in serialized

    assert payload["insurerName"] == "DB손해보험"
    assert payload["productName"] == "여행자보험 표준형"
    assert payload["startDate"] == "2026-07-30"
    assert payload["endDate"] == "2026-08-02"
    assert payload["coverageItems"][0]["title"] == "해외여행중 휴대품손해"
    assert payload["coverageItems"][0]["limitAmount"] == 1_000_000
    assert "홍길동" not in payload["summary"]
    assert "홍길동" not in payload["rawResultJson"]
