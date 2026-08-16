"""약관/증권 경로 분기 회귀 테스트.

같은 엔드포인트로 둘 다 들어오는데 처리가 완전히 다르다.

  TERMS        파싱 -> 청킹 -> 임베딩 -> 저장. 챗봇이 검색할 근거가 된다
  CERTIFICATE  Studio Agent -> 담보·금액 -> 보장 카드. 화면에 뜨는 내용이 된다

잘못 갈리면 증권을 청킹해 근거로 저장하거나, 약관 160페이지를 증권 분석기에 넣는다.
둘 다 실패가 아니라 "결과가 이상한" 형태로 나타나 원인을 찾기 어렵다.

백엔드가 documentType을 보내지 않아도 동작해야 한다. 백엔드는 다른 작업 중이라
아직 증권 경로가 없고, 그쪽 일정에 우리가 묶이면 안 된다.
"""

import fitz
import pytest

from app.schemas.analysis import AnalysisStartRequest
from app.services import analysis_service

BASE = {
    "analysisResultId": "a-1",
    "documentId": "d-1",
    "userId": "u-1",
    "tripId": "t-1",
    "downloadUrl": "https://x.test/f.pdf",
}


def make_pdf(path, pages: int):
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(path)
    doc.close()
    return path


# 백엔드가 명시하면 그것을 믿는다. 페이지 수보다 우선한다.
@pytest.mark.parametrize("declared", ["TERMS", "CERTIFICATE"])
def test_declared_document_type_wins(tmp_path, declared):
    pdf = make_pdf(tmp_path / "f.pdf", pages=200)
    request = AnalysisStartRequest.model_validate({**BASE, "documentType": declared})

    assert analysis_service._resolve_document_type(request, pdf) == declared


# 안 보내도 동작해야 한다. 증권은 1~2페이지, 약관은 100페이지가 넘는다.
@pytest.mark.parametrize(
    "pages, expected",
    [(1, "CERTIFICATE"), (2, "CERTIFICATE"), (10, "CERTIFICATE"), (11, "TERMS"), (160, "TERMS")],
)
def test_auto_detects_by_page_count(tmp_path, pages, expected):
    pdf = make_pdf(tmp_path / "f.pdf", pages=pages)
    request = AnalysisStartRequest.model_validate(BASE)

    assert analysis_service._resolve_document_type(request, pdf) == expected


# 페이지 수를 못 세면 약관으로 본다. 기존 경로가 기본값이어야 한다.
def test_unreadable_pdf_falls_back_to_terms(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")
    request = AnalysisStartRequest.model_validate(BASE)

    assert analysis_service._resolve_document_type(request, broken) == "TERMS"


def test_document_type_is_optional():
    request = AnalysisStartRequest.model_validate(BASE)

    assert request.document_type is None, "기본값이 생기면 자동 판별이 죽는다"


# ── 경로 분기 ────────────────────────────────────────────────


CERTIFICATE = {
    "insurer_name": "한화손해보험(주)",
    "document_title": "해외여행자보험 가입증명서",
    "coverage_by_age_table": [
        {
            "coverage_category_level_1": "해외의료비 보장",
            "coverage_category_level_2": "",
            "coverage_item_name": "상해",
            "coverage_amount_age_15_80": "US 5만달러",
        }
    ],
    "coverage_description_table": [],
}


@pytest.fixture
def captured(monkeypatch, tmp_path):
    """다운로드와 콜백을 가로채고, 증권 분석은 가짜 결과로 대체한다."""
    sent = {}

    pdf = make_pdf(tmp_path / "cert.pdf", pages=1)
    monkeypatch.setattr(analysis_service, "_download_pdf", lambda url, doc_id: pdf)
    monkeypatch.setattr(
        analysis_service, "analyze_certificate", lambda path: CERTIFICATE
    )
    monkeypatch.setattr(
        analysis_service.spring_client,
        "notify_complete",
        lambda payload: sent.setdefault("complete", payload),
    )
    monkeypatch.setattr(
        analysis_service.spring_client,
        "notify_fail",
        lambda payload: sent.setdefault("fail", payload),
    )
    return sent


# 증권이면 청킹도 임베딩도 하지 않는다. 증권은 화면에 뜰 담보 목록이지
# 챗봇이 검색할 근거가 아니다.
def test_certificate_does_not_chunk_or_embed(monkeypatch, captured):
    def explode(*args, **kwargs):
        raise AssertionError("증권을 약관 경로로 처리했다")

    monkeypatch.setattr(analysis_service, "parse_and_chunk", explode)
    monkeypatch.setattr(analysis_service, "embed_chunks", explode)
    monkeypatch.setattr(analysis_service, "extract_all", explode)

    request = AnalysisStartRequest.model_validate({**BASE, "documentType": "CERTIFICATE"})
    analysis_service.process_analysis(request, repository=None)

    assert "complete" in captured


# 백엔드가 이 둘로 약관을 찾아 연결한다. 빠지면 챗봇이 근거를 댈 수 없다.
def test_certificate_callback_carries_insurer_and_product(captured):
    request = AnalysisStartRequest.model_validate({**BASE, "documentType": "CERTIFICATE"})

    analysis_service.process_analysis(request, repository=None)

    payload = captured["complete"]
    assert payload.insurer_name == "한화손해보험(주)"
    assert payload.product_name == "해외여행자보험 가입증명서"


# 증권 전환의 핵심이 금액이다. 약관에서는 30건 중 3건만 채워졌다.
def test_certificate_callback_carries_amounts(captured):
    request = AnalysisStartRequest.model_validate({**BASE, "documentType": "CERTIFICATE"})

    analysis_service.process_analysis(request, repository=None)

    item = captured["complete"].coverage_items[0]
    assert item.limit_amount == 50000
    assert item.limit_currency == "USD"
    assert item.limit_label == "US 5만달러"


def test_certificate_failure_sends_fail_callback(monkeypatch, captured):
    def explode(path):
        raise analysis_service.CertificateAnalysisError("에이전트 설정 오류")

    monkeypatch.setattr(analysis_service, "analyze_certificate", explode)

    request = AnalysisStartRequest.model_validate({**BASE, "documentType": "CERTIFICATE"})
    analysis_service.process_analysis(request, repository=None)

    assert "complete" not in captured
    assert captured["fail"].error_message == "에이전트 설정 오류"
