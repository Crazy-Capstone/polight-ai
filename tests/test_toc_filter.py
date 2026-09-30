"""목차 청크를 근거에서 제외한다.

목차는 조항 제목만 나열돼 있어 보상 여부·조건·금액이 하나도 없다. 그런데 특약명이
빠짐없이 적혀 있어 키워드 자석처럼 검색에 걸린다. 실측(약관 7종 2,548청크)에서
목차 38건 중 24건에 카테고리가 붙어 있었다(flight_delay, medical_expense 등).

근거로 올라가면 두 가지가 나빠진다. LLM에게는 쓸모없는 조각이 근거 자리를 차지하고,
사용자가 근거 본문을 열면 점선 덩어리를 보게 된다.

여기 실린 표본은 지어낸 것이 아니라 data/chunks에 있는 실제 약관에서 가져왔다.
"""

import json
from pathlib import Path

import pytest

from app.services import rag_service
from scripts.chunk_policy import drop_toc_chunks, is_toc_text
from tests.conftest import make_hit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHUNKS_DIR = PROJECT_ROOT / "data" / "chunks"


# 파서가 점선을 남긴 목차. 줄 끝 점선이 줄 길이를 131자까지 부풀려,
# 페이지 단위 판정(TOC_AVG_LINE_LEN=28)으로는 본문으로 잘못 읽힌다.
TOC_WITH_LEADERS = """제 29 조 ( 계약자의 임의해지 ) ····························································· 25
제 30 조 ( 중대사유로 인한 해지 ) ··························································· 26
제 31 조 ( 회사의 파산선고와 해지 ) ························································· 26
제 32 조 ( 보험료의 환급 ) ································································· 27
제 33 조 ( 분쟁의 조정 ) ··································································· 27
제 34 조 ( 관할법원 ) ····································································· 27"""

# 파서가 점선을 지운 목차. 점선 신호가 0이라 조항 제목 줄 비율로만 잡힌다.
TOC_WITHOUT_LEADERS = """제 4관 보험계약의 성립과 유지
제17조(보험계약의 성립)
제18조(청약의 철회)
제19조(약관교부 및 설명의무 등)
제20조(계약의 무효)
제21조(계약내용의 변경 등)
제22조(보험나이 등)
제23조(계약의 소멸)"""

# 실제 보장 조항. 근거의 본체라 절대 걸러지면 안 된다.
REAL_CLAUSE = """제3조(보험금의 지급사유)
회사는 피보험자가 보험기간 중 해외여행 도중에 급격하고도 우연한 외래의 사고로
신체에 상해를 입었을 때에는 그 상해로 인하여 발생한 손해를 이 약관에 따라
보상하여 드립니다. 다만, 아래의 경우에는 보상하지 않습니다."""

# 본문이 섞인 안내 블록. 용어 해설 표는 실제 근거가 될 수 있어 남겨야 한다.
# 실측에서 목차 판정 경계에 가장 가까웠던 표본이다(점선 줄 비율 0.33).
MIXED_WITH_BODY = """11. 대위권
회사가 보험금을 지급한 때에는 회사는 지급한 보험금 한도 내에서 보험계약자 또는
피보험자가 제3자에 대하여 가지는 손해배상청구권을 취득합니다. 다만, 회사가 보상한
금액이 피보험자가 입은 손해의 일부인 경우에는 피보험자의 권리를 침해하지 않는
범위 내에서 그 권리를 취득합니다.
주요 보험용어 해설
| 보험용어 | 용어 해설 |
| 보험약관 | 보험계약에 관하여 보험계약자와 보험회사 상호간에 이행하여야 할 권리와 의무를 규정한 것 |"""


def test_detects_toc_with_dot_leaders():
    assert is_toc_text(TOC_WITH_LEADERS)


def test_detects_toc_without_dot_leaders():
    assert is_toc_text(TOC_WITHOUT_LEADERS)


def test_keeps_real_clause():
    assert not is_toc_text(REAL_CLAUSE)


def test_keeps_block_that_has_body_text():
    assert not is_toc_text(MIXED_WITH_BODY)


# 제목만 있는 한 줄짜리 청크는 목차가 아니다. 조항 제목 줄 비율로만 보면 1.0이라
# 걸리는데, 실제로는 본문이 비어 있을 뿐인 정상 조각이다.
def test_keeps_single_title_line_chunk():
    assert not is_toc_text("제 5 조 (준용규정)")


def test_empty_text_is_not_toc():
    assert not is_toc_text("")
    assert not is_toc_text("\n\n   \n")


# 청킹 단계에서 빠져야 한다. 여기서 걸러지면 애초에 색인되지 않는다.
def test_chunking_drops_toc_chunks():
    chunks = [
        {"chunk_id": "c1", "text": TOC_WITH_LEADERS},
        {"chunk_id": "c2", "text": REAL_CLAUSE},
        {"chunk_id": "c3", "text": TOC_WITHOUT_LEADERS},
    ]

    kept = drop_toc_chunks(chunks)

    assert [c["chunk_id"] for c in kept] == ["c2"]


# 검색 단계에서도 막아야 한다. 청킹을 고치기 전에 색인된 약관은 재적재 전까지
# DB에 그대로 남아 있어, 그 약관을 쓰는 사용자에게는 청킹 수정이 닿지 않는다.
def test_search_drops_toc_hits():
    hits = [make_hit("toc", text=TOC_WITH_LEADERS), make_hit("real", text=REAL_CLAUSE)]

    assert [h.chunk_id for h in rag_service.drop_toc_hits(hits)] == ["real"]


# 목차만 검색된 질의는 근거 없음으로 답해야 한다. 걸러낸 뒤 후보가 비었는데
# 그대로 진행하면 근거 0건으로 LLM을 부르게 되고, 그게 곧 환각이다.
def test_query_with_only_toc_evidence_answers_no_evidence(client, fake_repo, monkeypatch):
    fake_repo.hits = [make_hit("toc", text=TOC_WITH_LEADERS)]
    monkeypatch.setattr(rag_service, "embed_query", lambda q, client=None: [0.1] * 8)
    monkeypatch.setattr(
        rag_service, "_call_llm", lambda msg, client=None: pytest.fail("목차만 있는데 LLM을 불렀다")
    )

    body = client.post(
        "/internal/rag/query",
        json={"userId": "u", "tripId": "t", "documentId": "doc-1", "question": "항공기 지연 보상되나요?"},
    ).json()

    assert body["sources"] == []
    assert body["answer"] == rag_service.NO_EVIDENCE_ANSWER


# 실제 약관 전체를 기준으로 오검출을 확인한다.
#
# 임계값 하나 잘못 잡으면 실제 조항이 조용히 사라진다. 근거가 사라지는 실패는
# 화면에 아무 표시도 남기지 않아서, 회귀로 잡지 않으면 눈치채지 못한다.
@pytest.mark.parametrize("path", sorted(CHUNKS_DIR.glob("*_chunks.json")))
def test_no_real_clause_is_dropped_from_indexed_terms(path):
    chunks = json.loads(path.read_text(encoding="utf-8"))
    dropped = [c for c in chunks if is_toc_text(c["text"])]

    # 걸러진 것은 전부 목차여야 한다. 목차는 조항 제목만 늘어서 있어
    # 문장을 끝맺는 종결어미("합니다", "습니다")가 나오지 않는다.
    for chunk in dropped:
        assert "합니다" not in chunk["text"], f"{chunk['chunk_id']}: 본문이 걸러졌다"

    # 그리고 약관 하나에서 걸러지는 양이 갑자기 늘면 임계값이 무너진 것이다.
    # 실측 최대는 8건(캐롯·현대2025·메리츠)이다.
    assert len(dropped) <= 12, f"{path.name}: {len(dropped)}건이 걸러졌다"
