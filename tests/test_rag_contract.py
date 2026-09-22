import pytest

from app.services import rag_service
from app.services.prompt_builder import format_evidence
from tests.conftest import make_hit

QUERY_URL = "/internal/rag/query"

VALID_BODY = {
    "userId": "user-1",
    "tripId": "trip-1",
    "documentId": "doc-1",
    "question": "항공편이 5시간 지연되면 보상되나요?",
}


# 임베딩과 LLM 호출만 대체한다. 나머지(검색, 컨텍스트 조립, 출처 생성)는 실제 코드가 돈다.
@pytest.fixture(autouse=True)
def stub_openai(monkeypatch):
    monkeypatch.setattr(rag_service, "embed_query", lambda q, client=None: [0.1] * 8)
    monkeypatch.setattr(
        rag_service, "_call_llm", lambda msg, client=None: "테스트 답변 [근거 1]"
    )


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# Spring과의 JSON 계약: 요청은 camelCase로 받고 응답도 camelCase로 준다.
def test_response_uses_camel_case(client, fake_repo):
    fake_repo.hits = [make_hit("c1")]

    response = client.post(QUERY_URL, json=VALID_BODY)

    assert response.status_code == 200
    body = response.json()
    # responseType은 chat_messages.response_type(NOT NULL)에 그대로 들어간다.
    # 빠지면 Spring이 메시지를 저장할 수 없다.
    #
    # suggestedContacts는 사고 정황일 때 프론트가 현지 연락처를 함께 띄우도록
    # 실어 보내는 값이다. 백엔드가 이 값을 프론트까지 전달하기 전에는 무시되고,
    # 빈 배열이 기존 동작과 같다(test_contact_tag.py 참고).
    assert set(body) == {"answer", "responseType", "suggestedContacts", "sources"}
    assert body["responseType"] == "TEXT"
    assert body["suggestedContacts"] == []
    assert set(body["sources"][0]) == {
        "index",
        "chunkId",
        "documentId",
        "termsId",
        "sectionTitle",
        "page",
        "pageStart",
        "pageEnd",
        "clauseType",
        "text",
        "quote",
        "cited",
    }


def test_missing_required_field_returns_422(client):
    response = client.post(QUERY_URL, json={"question": "질문만 있음"})
    assert response.status_code == 422


# 근거가 없으면 LLM을 호출하지 않고 "확인할 수 없다"고 답해야 한다.
# 근거 없이 답을 만들면 그게 곧 환각이다.
def test_no_evidence_returns_explicit_message(client, fake_repo):
    fake_repo.hits = []

    body = client.post(QUERY_URL, json=VALID_BODY).json()

    assert body["sources"] == []
    assert body["answer"] == rag_service.NO_EVIDENCE_ANSWER


# document_id로 스코프가 걸려야 한다. 다른 약관이 섞이면 오답이 된다.
#
# policy_id를 쓰지 않는 이유: 백엔드에 policies 행을 만드는 코드가 없어 항상 null이고,
# SQL에서 "= NULL"은 아무 행과도 일치하지 않아 검색이 통째로 0건이 된다.
def test_search_is_scoped_by_document_id(client, fake_repo):
    fake_repo.hits = [
        make_hit("mine", document_id="doc-1"),
        make_hit("other", document_id="doc-2"),
    ]

    body = client.post(QUERY_URL, json=VALID_BODY).json()

    assert [s["chunkId"] for s in body["sources"]] == ["mine"]


# 이 프로젝트 RAG의 핵심: 보장 조항이 검색되면 짝지어진 면책 조항이 근거에 따라와야 한다.
# 따라오지 않으면 LLM이 예외를 모른 채 "보상됩니다"라고 답한다.
def test_related_exclusion_chunk_is_attached(client, fake_repo):
    fake_repo.hits = [
        make_hit("covered", coverage_type="included", related_chunk_id="excluded-1"),
        make_hit("excluded-1", coverage_type="excluded"),
    ]
    # 검색 자체는 보장 조항만 반환하도록 top_k를 1로 만드는 대신,
    # search가 두 건 다 주더라도 중복 추가가 없어야 한다는 것까지 확인한다.
    body = client.post(QUERY_URL, json=VALID_BODY).json()

    chunk_ids = [s["chunkId"] for s in body["sources"]]
    assert "excluded-1" in chunk_ids
    assert len(chunk_ids) == len(set(chunk_ids)), "면책 조항이 중복으로 붙었다"


def test_related_chunk_not_duplicated_when_already_present(fake_repo):
    hits = [
        make_hit("covered", coverage_type="included", related_chunk_id="exc"),
        make_hit("exc", coverage_type="excluded"),
    ]
    fake_repo.hits = hits

    result = rag_service.attach_related_chunks(hits, fake_repo)

    assert len(result) == 2


# 출처 인용은 LLM 출력이 아니라 검색된 원문에서 잘라내야 한다.
# LLM이 인용문을 만들면 원문에 없는 문장이 근거로 제시될 수 있다.
def test_quote_comes_from_source_text_not_llm():
    long_text = "가" * 500
    hit = make_hit("c1", text=long_text)

    sources = rag_service.build_sources([hit])

    assert sources[0].quote.startswith("가")
    assert sources[0].quote.endswith("...")
    assert sources[0].page == hit.page_start


# quote는 잘라도 text는 자르면 안 된다. 근거 팝업에 띄울 본문이라
# 200자에서 끊기면 사용자가 보상 조건이나 면책 사유의 뒷부분을 읽지 못한다.
def test_text_carries_full_clause_not_truncated_quote():
    long_text = "가" * 500
    hit = make_hit("c1", text=long_text)

    source = rag_service.build_sources([hit])[0]

    assert source.text == long_text
    assert len(source.quote) == rag_service.QUOTE_MAX_CHARS + len("...")


# 답변의 [근거 N]과 sources[].index가 같은 번호를 가리켜야 한다.
#
# 이 둘은 서로 다른 함수가 매긴다(prompt_builder.format_evidence / build_sources).
# 같은 리스트를 같은 순서로 받는다는 것이 유일한 보장이라, 한쪽 정렬이 바뀌면
# 프론트가 엉뚱한 조항을 근거라고 띄운다. 면책 조항이 뒤에 붙어 순서가 밀리는
# 실제 경로까지 함께 확인한다.
def test_evidence_index_matches_prompt_numbering():
    hits = [
        make_hit("covered", section_title="제1조(보상하는 손해)"),
        make_hit("excluded", coverage_type="excluded", section_title="제4조(보상하지 않는 손해)"),
    ]

    evidence = format_evidence(hits)
    sources = rag_service.build_sources(hits)

    assert [s.index for s in sources] == [1, 2]
    for source in sources:
        assert f"[근거 {source.index}] " in evidence
        # 그 번호가 붙은 블록이 실제로 이 조항인지까지 본다
        block = evidence.split(f"[근거 {source.index}] ")[1]
        assert source.section_title in block


# 면책 조항은 화면에서 보장 조항과 구분돼야 한다. 구분이 없으면 사용자가
# "보상하지 않는 손해"를 보상 근거로 읽는다.
#
# 값은 새로 만들지 않고 policy_terms_chunks.clause_type의 어휘를 그대로 쓴다.
def test_clause_type_uses_backend_enum_vocabulary():
    hits = [
        make_hit("c1", coverage_type="included"),
        make_hit("c2", coverage_type="excluded"),
        make_hit("c3", coverage_type="procedure"),
    ]

    sources = rag_service.build_sources(hits)

    assert [s.clause_type for s in sources] == ["COVERAGE", "EXCLUSION", "PROCEDURE"]


# 모르는 coverage_type이 와도 500이 나면 안 된다. 답변은 이미 만들어졌는데
# 근거를 그리다 실패하는 것이 가장 아깝다.
def test_unknown_clause_type_falls_back_instead_of_raising():
    source = rag_service.build_sources([make_hit("c1", coverage_type="알 수 없음")])[0]

    assert source.clause_type == "GENERAL"


# 공용 약관 경로에서 document_id 자리에는 terms_id가 실린다. 그 이름 그대로
# 내보내면 받는 쪽이 policy_documents를 조회하다 0건을 만나므로 제 이름으로도 보낸다.
def test_terms_id_is_sent_under_its_own_name():
    hit = make_hit("c1", document_id="terms-1")
    hit.terms_id = "terms-1"

    source = rag_service.build_sources([hit])[0]

    assert source.terms_id == "terms-1"
    assert source.document_id == "terms-1"


# 파일 저장소(평가·데모)는 약관이 아니라 문서 단위라 terms_id가 없다.
# 그때 null이 나가야 받는 쪽이 "약관 경로가 아니다"를 구분할 수 있다.
def test_terms_id_is_null_when_repository_has_no_terms():
    source = rag_service.build_sources([make_hit("c1")])[0]

    assert source.terms_id is None
