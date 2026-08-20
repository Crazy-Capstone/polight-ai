"""증권 분석이 실패했을 때 원인을 찾을 수 있는지, 그리고 실패가 사용자에게
어떻게 보이는지에 대한 회귀 테스트.

실제로 이런 일이 있었다. 프론트 화면에 다음 문구가 그대로 떴다.

    증권에서 보장 담보를 찾지 못했습니다. 에이전트 출력 형식을 확인하십시오.

두 가지가 동시에 잘못됐다.

  사용자 쪽   "에이전트 출력 형식을 확인하십시오"는 사용자가 할 수 있는 일이 아니다
  개발자 쪽   원인을 찾으려 했더니 남은 것이 이 문구뿐이었다. 에이전트 출력은
              어디에도 기록되지 않아 Studio 콘솔을 열어야만 알 수 있었다

그래서 셋을 고정한다. 출력 구조는 로그에 남고, 값은 절대 남지 않고,
사용자에게는 사용자용 문구만 나간다.
"""

import fitz
import pytest

from app.schemas.analysis import AnalysisStartRequest
from app.schemas.db_limits import MAX_LENGTHS
from app.services import analysis_service
from app.services.certificate_adapter import describe_structure, to_payloads

BASE = {
    "analysisResultId": "a-1",
    "documentId": "d-1",
    "userId": "u-1",
    "tripId": "t-1",
    "downloadUrl": "https://x.test/f.pdf?X-Amz-Signature=deadbeef",
    "documentType": "CERTIFICATE",
}


def make_pdf(path, pages: int):
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def captured(monkeypatch, tmp_path):
    sent = {}
    pdf = make_pdf(tmp_path / "cert.pdf", pages=1)
    monkeypatch.setattr(analysis_service, "_download_pdf", lambda url, doc_id: pdf)
    monkeypatch.setattr(
        analysis_service.spring_client, "notify_complete",
        lambda payload: sent.setdefault("complete", payload),
    )
    monkeypatch.setattr(
        analysis_service.spring_client, "notify_fail",
        lambda payload: sent.setdefault("fail", payload),
    )
    return sent


# ── 구조 요약 ────────────────────────────────────────────────


# 원인을 가르는 데 필요한 것은 값이 아니라 모양이다. 키가 바뀐 것인지
# 표가 비어 있는 것인지만 알면 다음 행동이 정해진다.
def test_structure_shows_keys_and_row_counts():
    described = describe_structure({
        "insurer_name": "한화손해보험(주)",
        "coverage_by_age_table": [{"coverage_item_name": "상해", "coverage_amount_age_15_80": "5만원"}],
        "coverage_description_table": [],
    })

    assert "coverage_by_age_table[1]" in described
    assert "coverage_item_name" in described
    assert "coverage_description_table[0]" in described


# 증권에는 피보험자 이름·생년월일·증권번호가 들어 있고 로그는 지우기 어렵다.
# 값이 한 글자라도 새면 이 진단은 쓸 수 없는 것이 된다.
def test_structure_never_contains_values():
    described = describe_structure({
        "insured_person_name": "홍길동",
        "insured_birth_date": "1990-01-01",
        "policy_no": "1234-5678",
        "coverage_by_age_table": [{"coverage_item_name": "상해", "amount": "5,000만원"}],
    })

    for value in ("홍길동", "1990-01-01", "1234-5678", "상해", "5,000만원"):
        assert value not in described, f"{value}가 로그에 남는다"


# 키 이름이 바뀌었을 때 그것이 보여야 한다. 이 케이스가 실제로 터진 것이다.
def test_structure_reveals_renamed_keys():
    described = describe_structure({"coverages": [{"name": "상해"}]})

    assert "coverages[1]" in described
    assert "coverage_by_age_table" not in described


# ── 실패 사유 ────────────────────────────────────────────────


# 담보가 0건인 것과 애초에 증권이 아닌 것은 사용자가 할 일이 다르다.
# 전자는 "다른 증권으로", 후자는 "증권을 올려주세요"다. 같은 문구로 끝내면
# 사용자는 증권을 다시 올려도 같은 실패를 본다.
#
# 두께로 가르지 않는다. 처음에 페이지 수로 판정했다가 틀렸다 - 아래 테스트 참고.
def test_non_certificate_document_says_so(monkeypatch, captured):
    monkeypatch.setattr(
        analysis_service, "analyze_certificate",
        lambda path: {"content": "...", "elements": []},
    )

    analysis_service.process_analysis(
        AnalysisStartRequest.model_validate(BASE), repository=None
    )

    assert "증권이 아닌" in captured["fail"].error_message


# 157페이지짜리 증권이 실재한다. 현대해상은 증권 2장과 약관 153장을 한 파일로
# 발급한다. 두께로 판정하면 이 정상 증권을 "증권이 아니다"로 되돌린다.
def test_thick_bundled_certificate_is_not_called_a_wrong_document(
    monkeypatch, captured, tmp_path
):
    pdf = make_pdf(tmp_path / "bundled.pdf", pages=157)
    monkeypatch.setattr(analysis_service, "_download_pdf", lambda url, doc_id: pdf)
    monkeypatch.setattr(
        analysis_service, "analyze_certificate",
        lambda path: {
            "policy_number": "F-26PA-0120186",
            "insurer_name": "현대해상",
            "coverage_by_age_table": [],
            "coverage_description_table": [],
        },
    )

    analysis_service.process_analysis(
        AnalysisStartRequest.model_validate(BASE), repository=None
    )

    sent = captured["fail"].error_message
    assert "증권이 아닌" not in sent, "정상 증권을 잘못된 문서로 되돌린다"
    assert "보장 내용을 찾지 못했습니다" in sent


def test_empty_table_asks_for_the_original(monkeypatch, captured):
    monkeypatch.setattr(
        analysis_service, "analyze_certificate",
        lambda path: {"policy_number": "F-1", "coverage_by_age_table": []},
    )

    analysis_service.process_analysis(
        AnalysisStartRequest.model_validate(BASE), repository=None
    )

    sent = captured["fail"].error_message
    assert "보장 내용을 찾지 못했습니다" in sent
    # 내부 지시문이 사용자 화면에 뜨던 문구다
    assert "에이전트" not in sent


# presigned URL에는 서명이 붙어 있다. 실패 사유는 DB에 영구 저장된 뒤 화면에 뜬다.
def test_presigned_url_never_reaches_the_callback(monkeypatch, captured):
    def boom(url, doc_id):
        raise analysis_service.AnalysisFailure(
            f"PDF를 내려받지 못했습니다 ({url})",
            user_message="문서를 내려받지 못했습니다. 잠시 후 다시 시도해 주세요.",
        )

    monkeypatch.setattr(analysis_service, "_download_pdf", boom)

    analysis_service.process_analysis(
        AnalysisStartRequest.model_validate(BASE), repository=None
    )

    sent = captured["fail"].error_message
    assert "X-Amz-Signature" not in sent
    assert "x.test" not in sent


# ── 길이 컷 ──────────────────────────────────────────────────


# 백엔드에 길이 검증이 없어 한도를 넘기면 400이 아니라 500이 온다. 500은
# 재시도 대상이라 세 번 다 500을 받고 콜백을 포기하고, 분석은 성공했는데
# 상태가 PROCESSING에 남는다. 문서당 분석은 1회뿐이라 되돌릴 수도 없다.
#
# 증권 쪽이 특히 위험하다. 이 값들은 에이전트가 뽑은 문자열이라 길이를
# 우리가 통제하지 못한다. 실제로 이 경로에 길이 컷이 빠져 있었다.
def test_certificate_payload_respects_length_limits():
    payloads = to_payloads({
        "coverage_by_age_table": [
            {
                "coverage_category_level_1": "가" * 300,
                "coverage_category_level_2": "나" * 900,
                "coverage_item_name": "다" * 300,
                "coverage_amount_age_15_80": "5,000만원 " + "라" * 300,
            }
        ],
        "coverage_description_table": [],
    })

    item = payloads[0]
    assert len(item.title) == MAX_LENGTHS["title"]
    assert len(item.subtitle) == MAX_LENGTHS["subtitle"]
    assert len(item.category) == MAX_LENGTHS["category"]
    assert len(item.limit_label) == MAX_LENGTHS["limit_label"]


# 자르는 것은 넘칠 때만이어야 한다. 실제 증권 값은 그대로 나가야 화면 문구가 온전하다.
def test_realistic_certificate_values_are_untouched():
    payloads = to_payloads({
        "coverage_by_age_table": [
            {
                "coverage_category_level_1": "해외의료비 보장",
                "coverage_category_level_2": "",
                "coverage_item_name": "상해",
                "coverage_amount_age_15_80": "US 5만달러",
            }
        ],
        "coverage_description_table": [],
    })

    item = payloads[0]
    assert item.limit_label == "US 5만달러"
    assert item.title == "해외의료비 보장 상해"
    assert item.limit_currency == "USD"
