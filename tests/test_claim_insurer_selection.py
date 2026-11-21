from contextlib import contextmanager

import pytest

from app.repositories.file_repository import FileVectorRepository
from app.repositories.pg_repository import PgVectorRepository
from app.services.claim_document_catalog import resolve_claim_insurer


class Repository:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def get_terms_insurer(self, terms_id):
        self.calls.append(terms_id)
        return self.name


@pytest.mark.parametrize('name,expected', [
    ('한화손해보험', 'hanwha'), ('캐롯손해보험', 'carrot'),
    ('한화손해보험(주)', 'hanwha'), ('주식회사 캐롯손해보험', 'carrot'),
    ('삼성화재해상보험', 'samsung'), ('KB손해보험', 'kb'),
    ('메리츠화재', 'meritz'), ('현대해상', 'hyundai'), ('DB손해보험', 'db'),
])
def test_linked_insurer_selects_own_catalog(name, expected):
    repo = Repository(name)
    selected = resolve_claim_insurer('linked-terms', repo)
    assert selected.status == 'MATCHED'
    assert selected.insurer['id'] == expected
    assert repo.calls == ['linked-terms']


def test_missing_link_does_not_lookup_or_guess():
    repo = Repository('삼성화재')
    assert resolve_claim_insurer(None, repo).status == 'MISSING_TERMS'
    assert not repo.calls


@pytest.mark.parametrize('name,status', [(None, 'INSURER_NOT_FOUND'), ('', 'INSURER_NOT_FOUND'),
    ('알수없는보험', 'UNSUPPORTED_INSURER'), ('한화손해보험 캐롯', 'UNSUPPORTED_INSURER')])
def test_unresolved_names_do_not_select_another_insurer(name, status):
    result = resolve_claim_insurer('terms', Repository(name))
    assert result.status == status
    assert result.insurer is None


def test_db_failure_does_not_disclose_details(caplog):
    class Broken:
        def get_terms_insurer(self, terms_id):
            raise RuntimeError('password=secret')
    assert resolve_claim_insurer('terms', Broken()).status == 'LOOKUP_FAILED'
    assert 'secret' not in caplog.text


def test_catalog_is_not_shared_mutable_request_state():
    a = resolve_claim_insurer('terms', Repository('한화손해보험'))
    a.insurer['profiles'].clear()
    b = resolve_claim_insurer('terms', Repository('한화손해보험'))
    assert len(b.insurer['profiles']) == 1


def test_pg_lookup_is_scoped_to_exact_terms_id(monkeypatch):
    calls = []
    class Cursor:
        def execute(self, sql, params):
            calls.append((sql, params))
        def fetchone(self):
            return ('캐롯손해보험',)
    @contextmanager
    def cursor():
        yield Cursor()
    repo = PgVectorRepository('unused')
    monkeypatch.setattr(repo, '_cursor', cursor)
    assert repo.get_terms_insurer('terms-id') == '캐롯손해보험'
    assert 'WHERE id = %s::uuid' in calls[0][0]
    assert calls[0][1] == ('terms-id',)


def test_local_link_keeps_carrot_separate_from_underwriter():
    result = resolve_claim_insurer('carrot_travel_overseas_20260715', FileVectorRepository())
    assert result.insurer['id'] == 'carrot'
    assert resolve_claim_insurer('unknown-id', FileVectorRepository()).status == 'INSURER_NOT_FOUND'


def test_rag_selects_insurer_even_when_no_chunks_are_found(monkeypatch, caplog):
    from types import SimpleNamespace
    from app.schemas.rag import RagQueryRequest
    from app.services import rag_service
    from app.services.claim_incident_classifier import ClaimIncidentContext

    repo = Repository('캐롯손해보험')
    monkeypatch.setattr(rag_service, 'get_settings', lambda: SimpleNamespace(
        database_url='configured', top_k=8, mmr_candidate_multiplier=3))
    monkeypatch.setattr(rag_service, 'rewrite', lambda question, turns: question)
    monkeypatch.setattr(rag_service, 'embed_query', lambda *a, **k: [])
    monkeypatch.setattr(rag_service, 'hybrid_search', lambda *a, **k: [])
    monkeypatch.setattr(rag_service, 'classify_claim_incident', lambda *a: ClaimIncidentContext(
        is_claim_document_question=True, incidents=['theft'], is_phone=False))
    from app.schemas.rag import RagQueryResponse
    monkeypatch.setattr(rag_service, 'build_claim_document_response',
        lambda selected, ctx: RagQueryResponse(answer=selected.insurer['id'], sources=[]))
    request = RagQueryRequest(user_id='u', trip_id='t', terms_id='linked', question='청구 서류 뭐가 필요해요?')
    with caplog.at_level('INFO'):
        result = rag_service.answer_question(request, repo)
    assert repo.calls == ['linked']
    assert 'catalogId=carrot' in caplog.text
    assert result.sources == []
