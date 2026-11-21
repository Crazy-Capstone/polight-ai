import json
from types import SimpleNamespace

import pytest

from app.schemas.rag import RagQueryRequest
from app.services import claim_incident_classifier as classifier, rag_service
from app.services.claim_document_catalog import ClaimInsurerSelection


def selection(insurer='samsung'):
    return ClaimInsurerSelection('MATCHED', insurer, {'id': insurer})


def context(**kwargs):
    return classifier.ClaimIncidentContext(is_claim_document_question=True, **kwargs)


@pytest.mark.parametrize('incidents,fields', [
    (['theft'], {'is_phone': True}),
    (['damage'], {'is_phone': False, 'repair_status': 'unrepairable'}),
    (['medical'], {'care_type': 'inpatient', 'treatment_location': 'overseas'}),
    (['medical'], {'care_type': 'outpatient', 'is_proxy_claim': True, 'is_minor': True, 'has_pharmacy_expense': True}),
    (['flight_delay', 'baggage_delay'], {'benefit_type': 'expense'}),
])
def test_validated_model_context_preserves_conditions(monkeypatch, incidents, fields):
    response = {'is_claim_document_question': True, 'incidents': incidents, **fields}
    monkeypatch.setattr(classifier, 'generate', lambda *args: (json.dumps(response), {}))
    result = classifier.classify_claim_incident('필요서류 알려주세요', [])
    assert result.incidents == incidents
    for key, value in fields.items():
        assert getattr(result, key) == value


def test_followup_passes_recent_history_and_latest_answer(monkeypatch):
    def generate(system, payload):
        data = json.loads(payload)
        assert data['question'] == '수리 못 한대요'
        assert len(data['history']) == 6
        assert data['history'][-1]['role'] == 'assistant'
        assert '사용자 발언만 사실' in system
        return json.dumps({'is_claim_document_question': True, 'incidents': ['damage'],
                           'repair_status': 'unrepairable', 'is_phone': True}), {}
    monkeypatch.setattr(classifier, 'generate', generate)
    turns = [{'role': 'user', 'content': '예전 대화'}] * 7 + [
        {'role': 'assistant', 'content': '휴대폰 청구서류 안내를 위해 수리 가능한지 알려주세요.'}]
    result = classifier.classify_claim_incident('수리 못 한대요', turns)
    assert result.repair_status == 'unrepairable'


@pytest.mark.parametrize('raw', [
    'not json', '{"is_claim_document_question":"true"}',
    '{"is_claim_document_question":true,"incidents":["invented"]}',
    '{"is_claim_document_question":true,"is_phone":"false"}',
    '{"is_claim_document_question":true,"claim_amount_krw":-1}',
    '{"is_claim_document_question":true,"insurer":"다른보험사"}',
])
def test_invalid_model_data_never_guesses(monkeypatch, raw):
    monkeypatch.setattr(classifier, 'generate', lambda *a: (raw, {}))
    assert classifier.classify_claim_incident('서류?', []) is None
    assert '사고' in classifier.claim_clarification(None, selection())


def test_provider_failure_does_not_leak_content(monkeypatch, caplog):
    def broken(*args):
        raise RuntimeError('secret medical content')
    monkeypatch.setattr(classifier, 'generate', broken)
    assert classifier.classify_claim_incident('서류?', []) is None
    assert 'secret medical content' not in caplog.text


def test_damage_questions_and_explicit_unknown():
    missing = context(incidents=['damage'])
    answer = classifier.claim_clarification(missing, selection())
    assert '휴대폰' in answer and '수리' in answer
    known = context(incidents=['damage'], is_phone=True, repair_status='repairable')
    assert classifier.claim_clarification(known, selection()) is None
    unknown = context(incidents=['damage'], is_phone=True, unavailable_conditions=['repair_status'])
    assert classifier.claim_clarification(unknown, selection()) is None


def test_medical_asks_only_missing_details():
    q = classifier.claim_clarification(context(incidents=['medical']), selection())
    assert '입원' in q and '국내' in q
    ready = context(incidents=['medical'], care_type='inpatient', treatment_location='overseas')
    assert classifier.claim_clarification(ready, selection()) is None
    kb = context(incidents=['medical'], care_type='outpatient', treatment_location='domestic')
    assert '청구금액' in classifier.claim_clarification(kb, selection('kb'))
    assert classifier.claim_clarification(kb, selection('samsung')) is None


def test_optional_proxy_minor_conditions_are_not_assumed_false():
    c = context(incidents=['theft'], is_phone=False)
    assert c.is_proxy_claim is None and c.is_minor is None
    assert classifier.claim_clarification(c, selection()) is None


def test_airline_and_liability_branch_questions():
    flight = context(incidents=['flight_delay'])
    assert '지수형' in classifier.claim_clarification(flight, selection())
    assert classifier.claim_clarification(flight, selection('carrot')) is None
    assert '신체' in classifier.claim_clarification(context(incidents=['liability']), selection())


def test_failed_insurer_never_substitutes_another_catalog():
    c = context(incidents=['baggage_delay'])
    assert '보험사 정보' in classifier.claim_clarification(c, ClaimInsurerSelection('MISSING_TERMS'))


def test_document_followups_and_unrelated_coverage_questions():
    turns = [{'role': 'assistant', 'content': '청구서류를 위해 입원인지 통원인지 알려주세요.'}]
    assert classifier.is_claim_document_turn('통원이고 일본이에요', turns)
    assert classifier.is_claim_document_turn('서류가 뭐예요?', [])
    assert not classifier.is_claim_document_turn('항공지연 한도는요?', turns)
    assert not classifier.is_claim_document_turn('안녕하세요', [])


def test_rag_clarifies_before_embedding_even_without_link(monkeypatch):
    monkeypatch.setattr(rag_service, 'get_settings', lambda: SimpleNamespace(database_url='configured'))
    monkeypatch.setattr(rag_service, 'classify_claim_incident', lambda *a: context(incidents=[]))
    def no_embedding(*a, **k):
        pytest.fail('clarification must precede search')
    monkeypatch.setattr(rag_service, 'embed_query', no_embedding)
    request = RagQueryRequest(user_id='u', trip_id='t', question='청구서류 알려주세요')
    result = rag_service.answer_question(request, object())
    assert '보험사 정보' in result.answer
    assert result.sources == [] and result.claim_documents == []


def test_rag_uses_history_for_followup_clarification(monkeypatch):
    monkeypatch.setattr(rag_service, 'get_settings', lambda: SimpleNamespace(database_url='configured'))
    monkeypatch.setattr(rag_service, 'resolve_claim_insurer', lambda *a: selection())
    def classify(question, turns):
        assert question == '병원 갔어요'
        assert turns[-1]['content'] == '어떤 사고의 청구서류인가요?'
        return context(incidents=['medical'])
    monkeypatch.setattr(rag_service, 'classify_claim_incident', classify)
    req = RagQueryRequest(user_id='u', trip_id='t', terms_id='linked', question='병원 갔어요',
        history=[{'sender':'ASSISTANT', 'content':'어떤 사고의 청구서류인가요?'}])
    result = rag_service.answer_question(req, object())
    assert '입원' in result.answer and '국내' in result.answer
