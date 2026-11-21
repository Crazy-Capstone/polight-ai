import json
from types import SimpleNamespace

import pytest

from app.services import claim_document_selector as selector, rag_service
from app.services.claim_document_catalog import resolve_claim_insurer
from app.services.claim_incident_classifier import ClaimIncidentContext
from app.schemas.rag import RagQueryRequest, ClaimDocument


class Repo:
    def __init__(self, name): self.name=name
    def get_terms_insurer(self, terms_id): return self.name


def selected(name):
    return resolve_claim_insurer('linked',Repo(name))


def context(incidents, **fields):
    return ClaimIncidentContext(is_claim_document_question=True,incidents=incidents,**fields)


@pytest.mark.parametrize('name,ctx,expected',[
 ('삼성화재',context(['theft'],is_phone=True),[1,2]),
 ('삼성화재',context(['flight_delay'],benefit_type='index'),[1,7]),
 ('KB손해보험',context(['medical'],care_type='outpatient',treatment_location='domestic',claim_amount_krw=50000),[1,6,8]),
 ('KB손해보험',context(['medical'],care_type='inpatient',treatment_location='overseas'),[1,3]),
 ('현대해상',context(['liability'],liability_type='property',is_minor=False),[1,7,9]),
 ('DB손해보험',context(['medical'],care_type='outpatient',has_pharmacy_expense=False),[1,4]),
 ('한화손해보험',context(['theft'],is_minor=False,is_proxy_claim=False),[1,7]),
 ('캐롯손해보험',context(['flight_delay'],benefit_type='expense'),[2]),
 ('캐롯손해보험',context(['flight_delay'],benefit_type='index'),[3]),
])
def test_only_matching_groups_enter_model(name,ctx,expected):
    p,groups,unsupported=selector.select_groups(selected(name),ctx)
    assert [selector._number(g) for g in groups]==expected
    assert not unsupported


def test_samsung_medical_preserves_common_and_additional():
    _,groups,_=selector.select_groups(selected('삼성화재'),context(['medical']))
    assert [g['documentCategory'] for g in groups]==['COMMON','ADDITIONAL']


def test_other_insurer_cannot_fill_missing_type(monkeypatch):
    monkeypatch.setattr(selector,'generate_json',lambda *a:pytest.fail('unsupported must not call model'))
    r=selector.build_claim_document_response(selected('DB손해보험'),context(['flight_delay']))
    assert r.claim_documents==[]
    assert 'idbins.com' in r.claim_documents_url


def test_source_bound_documents_dedup_notice_and_url(monkeypatch):
    def generate(prompt,payload):
        data=json.loads(payload)
        assert all(s['id'].startswith('property_travel_') for s in data['sources'])
        base=next(s for s in data['sources'] if s['text']=='보험금청구서')
        theft=next(s for s in data['sources'] if s['text'].startswith('도난사고증명서'))
        docs=[{'source_id':base['id'],'document_name':'보험금청구서'},
              {'source_id':base['id'],'document_name':'보험금청구서'},
              {'source_id':theft['id'],'document_name':'도난사고증명서'}]
        return json.dumps({'documents':docs}),0
    monkeypatch.setattr(selector,'generate_json',generate)
    r=selector.build_claim_document_response(selected('삼성화재'),context(['theft'],is_phone=False))
    assert [d.document_name for d in r.claim_documents]==['보험금청구서','도난사고증명서']
    assert all(d.source_chunk_id is None for d in r.claim_documents)
    payload=r.model_dump(by_alias=True)
    assert payload['claimDocumentsNotice'] and 'samsungfire.com' in payload['claimDocumentsUrl']


@pytest.mark.parametrize('doc',[
 {'source_id':'property_travel_01:0','document_name':'존재하지 않는 서류'},
 {'source_id':'other_insurer:0','document_name':'보험금청구서'},
 {'source_id':'property_travel_01:0','document_name':'보험금청구서','condition_quote':'지어낸 조건'},
])
def test_invalid_source_or_invented_content_fails_closed(monkeypatch,doc):
    monkeypatch.setattr(selector,'generate_json',lambda *a:(json.dumps({'documents':[doc]}),0))
    r=selector.build_claim_document_response(selected('삼성화재'),context(['theft'],is_phone=False))
    assert r.claim_documents==[] and r.claim_documents_url


def test_additional_group_cannot_be_promoted_to_required(monkeypatch):
    def generate(prompt,payload):
        data=json.loads(payload)
        source=next(s for s in data['sources'] if s['text']=='초진기록지')
        return json.dumps({'documents':[{'source_id':source['id'],'document_name':'초진기록지','additional':False}]}),0
    monkeypatch.setattr(selector,'generate_json',generate)
    r=selector.build_claim_document_response(selected('삼성화재'),context(['medical']))
    assert r.claim_documents[0].requirement_type=='CONDITIONAL'
    assert '보험사 확인' in r.claim_documents[0].condition_text


def test_rag_returns_checklist_before_any_search(monkeypatch):
    monkeypatch.setattr(rag_service,'get_settings',lambda:SimpleNamespace(database_url='configured'))
    monkeypatch.setattr(rag_service,'classify_claim_incident',lambda *a:context(['theft'],is_phone=False))
    monkeypatch.setattr(rag_service,'embed_query',lambda *a,**k:pytest.fail('must not search terms'))
    monkeypatch.setattr(selector,'generate_json',lambda *a:(json.dumps({'documents':[
        {'source_id':'property_travel_01:0','document_name':'보험금청구서'}]}),0))
    req=RagQueryRequest(user_id='u',trip_id='t',terms_id='linked',question='도난 청구서류?')
    r=rag_service.answer_question(req,Repo('삼성화재'))
    assert r.claim_documents and r.sources==[] and r.claim_documents_url


def test_existing_terms_document_schema_remains_compatible():
    d=ClaimDocument(document_name='서류',requirement_type='REQUIRED',coverage_title='담보',
        source_chunk_id='chunk',source_quote='quote')
    assert d.source_quote=='quote'


def test_common_omission_condition_and_online_notice_are_preserved(monkeypatch):
    def generate(prompt,payload):
        data=json.loads(payload)
        s=next(s for s in data['sources'] if s['text'].startswith('해외여행 시 여권 사본'))
        return json.dumps({'documents':[{'source_id':s['id'],'document_name':s['text']}]}),0
    monkeypatch.setattr(selector,'generate_json',generate)
    r=selector.build_claim_document_response(selected('삼성화재'),context(['medical']))
    assert '따로 제출하실 필요가 없습니다' in r.claim_documents_notice


def test_common_conditional_phrase_never_becomes_unconditional_required(monkeypatch):
    def generate(prompt,payload):
        data=json.loads(payload)
        s=next(s for s in data['sources'] if '국내 여행자보험인 경우' in s['text'])
        return json.dumps({'documents':[{'source_id':s['id'],'document_name':'국내 여행자보험인 경우 신분증 사본'}]}),0
    monkeypatch.setattr(selector,'generate_json',generate)
    r=selector.build_claim_document_response(selected('DB손해보험'),context(['theft'],is_phone=False))
    assert r.claim_documents[0].requirement_type=='CONDITIONAL'
    assert '국내 여행자보험' in r.claim_documents[0].condition_text
