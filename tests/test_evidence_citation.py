"""답변 본문의 [근거 N] 표기를 근거 번호로 되읽는다.

프론트가 답변의 마커를 누를 수 있는 칩으로 만들려면, 어느 마커가 어느 조항을
가리키는지 서버와 프론트가 똑같이 읽어야 한다. 양쪽이 다르게 읽으면 사용자가
근거를 눌렀을 때 다른 조항이 뜬다 - 보험 답변에서 가장 나쁜 실패다.

그래서 여기 실린 형식은 지어낸 것이 아니라 data/eval에 쌓인 실제 모델 출력에서
뽑았다. 프롬프트는 [근거 N]만 지시하지만 모델은 네 가지 형태로 쓴다.
"""

import pytest

from app.services import rag_service
from app.services.rag_service import parse_cited_indexes
from tests.conftest import make_hit


# 실제 출력에서 확인된 네 가지 형태.
@pytest.mark.parametrize(
    "answer,expected",
    [
        # 가장 흔한 형태
        ("보상됩니다 [근거 1]", {1}),
        # 숫자 나열
        ("보상됩니다 [근거 1, 2, 4, 8]", {1, 2, 4, 8}),
        # "근거"를 매번 반복
        ("보상됩니다 [근거 1, 근거 2, 근거 3]", {1, 2, 3}),
        # 마커가 여러 번 등장
        ("보상됩니다 [근거 1]. 다만 면책이 있습니다 [근거 6]", {1, 6}),
        # 공백이 없는 형태
        ("보상됩니다 [근거1]", {1}),
        # 두 자리 번호. 면책 조항이 붙으면 근거가 12건까지 늘어난다
        ("보상됩니다 [근거 9, 10, 11, 12]", {9, 10, 11, 12}),
    ],
)
def test_parses_real_marker_forms(answer, expected):
    assert parse_cited_indexes(answer, total=12) == expected


# 조항 번호가 마커 안에 딸려 오는 형태.
#
# 이게 이 파서의 존재 이유다. "괄호 안 숫자를 전부 뽑는" 방식이면 "제2조 4호"의
# 2와 4까지 근거 번호로 읽혀, 인용하지도 않은 조항 두 개가 사용자 화면에 근거로 뜬다.
def test_clause_numbers_inside_marker_are_not_mistaken_for_indexes():
    answer = "보상하지 않습니다 [근거 1 제2조 4호, 근거 7 제2조 4호]"

    assert parse_cited_indexes(answer, total=12) == {1, 7}


def test_clause_numbers_with_circled_digits():
    answer = "보상됩니다 [근거 1 제6조①1호, 근거 2 제5조①1호, 근거 8 제1조②]"

    assert parse_cited_indexes(answer, total=12) == {1, 2, 8}


# 모델이 없는 번호를 쓰면 버린다. 흘려보내면 프론트가 매칭되는 조항을 못 찾아
# 빈 팝업을 띄우거나 에러를 낸다.
def test_out_of_range_index_is_dropped():
    assert parse_cited_indexes("보상됩니다 [근거 15]", total=8) == set()
    assert parse_cited_indexes("보상됩니다 [근거 0]", total=8) == set()
    assert parse_cited_indexes("보상됩니다 [근거 3, 99]", total=8) == {3}


# 마커가 아예 없는 답변도 있다. [근거 N] 표기는 프롬프트 지시일 뿐 강제가 아니다.
# 이때 500이 나면 안 되고, 빈 집합이 나가 프론트가 폴백(근거 목록 접어 보여주기)을
# 돌릴 수 있어야 한다.
def test_answer_without_marker_yields_nothing():
    assert parse_cited_indexes("제공된 약관에서 확인할 수 없습니다.", total=8) == set()
    assert parse_cited_indexes("보상됩니다 [근거]", total=8) == set()
    assert parse_cited_indexes("", total=0) == set()


# 인용된 근거만 cited=true가 되어야 한다.
#
# 검색 결과에 짝지어진 면책 조항이 따라붙어 근거가 늘어나는데 모델은 일부만 쓴다.
# 구분이 없으면 프론트가 인용되지 않은 조항까지 같은 비중으로 늘어놓는다.
def test_only_cited_sources_are_marked():
    hits = [make_hit("c1"), make_hit("c2"), make_hit("c3")]

    sources = rag_service.build_sources(hits, parse_cited_indexes("답변 [근거 1, 3]", 3))

    assert [(s.index, s.cited) for s in sources] == [(1, True), (2, False), (3, True)]


# 인용 여부를 모르는 경로(테스트·평가 스크립트가 직접 부르는 경우)에서는
# 인자 없이 호출할 수 있어야 한다. 그때는 표시가 없는 것과 같다.
def test_cited_defaults_to_false_when_not_given():
    assert rag_service.build_sources([make_hit("c1")])[0].cited is False


# 라우터까지 붙여서 한 번 본다. 연락처 태그를 떼어낸 뒤의 답변으로 세는지,
# camelCase로 나가는지를 단위 테스트로는 잡지 못한다.
def test_cited_flows_through_api(client, fake_repo, monkeypatch):
    fake_repo.hits = [make_hit("c1"), make_hit("c2")]
    monkeypatch.setattr(rag_service, "embed_query", lambda q, client=None: [0.1] * 8)
    monkeypatch.setattr(
        rag_service,
        "_call_llm",
        lambda msg, client=None: "도난은 보상됩니다 [근거 2]\n\n[[CONTACT: POLICE]]",
    )

    body = client.post(
        "/internal/rag/query",
        json={
            "userId": "user-1",
            "tripId": "trip-1",
            "documentId": "doc-1",
            "question": "지갑을 도난당했는데 보상되나요?",
        },
    ).json()

    assert [(s["index"], s["cited"]) for s in body["sources"]] == [(1, False), (2, True)]
    assert body["suggestedContacts"] == ["POLICE"]
    # 태그는 지워지고 근거 표기는 남아야 한다. 프론트가 그걸로 칩을 만든다.
    assert "CONTACT" not in body["answer"]
    assert "[근거 2]" in body["answer"]
