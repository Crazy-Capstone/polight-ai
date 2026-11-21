"""Exercise the actual FastAPI route/serialization without real users or API charges."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.main import app
from app.repositories import get_vector_repository
from app.schemas.rag import RagQueryResponse
from app.services import rag_service, claim_document_selector
from app.services.claim_incident_classifier import ClaimIncidentContext


class Repository:
    def __init__(self, name): self.name=name
    def get_terms_insurer(self, terms_id): return self.name
    def search(self,*a,**k): pytest.fail('document response must bypass term search')


def request(body, key='test-key'):
    async def send():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            return await client.post('/internal/rag/query',json=body,headers={'X-Internal-Api-Key':key})
    return asyncio.run(send())


@pytest.fixture
def setup(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config,'get_settings',lambda:SimpleNamespace(internal_api_key='test-key'))
    monkeypatch.setattr(rag_service,'get_settings',lambda:SimpleNamespace(database_url='configured'))
    monkeypatch.setattr(app,'dependency_overrides',{get_vector_repository:lambda:Repository('삼성화재')})
    monkeypatch.setattr(rag_service,'classify_claim_incident',lambda *a:ClaimIncidentContext(
        is_claim_document_question=True,incidents=['theft'],is_phone=False))
    def generate(prompt,payload):
        sources=json.loads(payload)['sources']
        common=next(s for s in sources if s['text']=='보험금청구서')
        incident=next(s for s in sources if s['text'].startswith('도난사고증명서'))
        return json.dumps({'documents':[
            {'source_id':common['id'],'document_name':'보험금청구서'},
            {'source_id':incident['id'],'document_name':'도난사고증명서'}]}),0
    monkeypatch.setattr(claim_document_selector,'generate_json',generate)


BODY={'userId':'synthetic-user','tripId':'synthetic-trip','termsId':'linked-terms','question':'도난 청구서류 알려주세요'}


def test_http_success_fields_and_json_roundtrip(setup):
    response=request(BODY)
    assert response.status_code==200
    body=response.json()
    assert body['responseType']=='TEXT' and body['sources']==[]
    assert [d['documentName'] for d in body['claimDocuments']]==['보험금청구서','도난사고증명서']
    assert body['claimDocumentsNotice'] and body['claimDocumentsUrl'].startswith('https://www.samsungfire.com/')
    assert all(d['sourceChunkId'] is None for d in body['claimDocuments'])
    # This validates the AI JSON contract, not Spring's real database persistence.
    reloaded=RagQueryResponse.model_validate_json(json.dumps(body))
    assert reloaded.model_dump(by_alias=True)==body


def test_http_clarification_has_no_false_checklist(setup,monkeypatch):
    monkeypatch.setattr(rag_service,'classify_claim_incident',lambda *a:ClaimIncidentContext(
        is_claim_document_question=True,incidents=[]))
    response=request(BODY)
    assert response.status_code==200
    assert response.json()['claimDocuments']==[]
    assert response.json()['claimDocumentsUrl'] is None


def test_http_missing_link_does_not_use_another_insurer(setup):
    body={k:v for k,v in BODY.items() if k!='termsId'}
    response=request(body)
    assert response.status_code==200
    assert response.json()['claimDocuments']==[]
    assert '보험사 정보' in response.json()['answer']


def test_http_invalid_auth_stops_before_model(setup,monkeypatch):
    monkeypatch.setattr(rag_service,'classify_claim_incident',lambda *a:pytest.fail('unauthenticated model call'))
    assert request(BODY,'wrong-key').status_code==401


def test_http_invalid_request_is_422(setup):
    assert request({'question':'서류?'}).status_code==422


def test_http_unsupported_type_keeps_own_insurer_link(setup,monkeypatch):
    monkeypatch.setattr(app,'dependency_overrides',{get_vector_repository:lambda:Repository('DB손해보험')})
    monkeypatch.setattr(rag_service,'classify_claim_incident',lambda *a:ClaimIncidentContext(
        is_claim_document_question=True,incidents=['flight_delay']))
    response=request(BODY)
    assert response.status_code==200
    assert response.json()['claimDocuments']==[]
    assert 'idbins.com' in response.json()['claimDocumentsUrl']


def test_http_schema_has_new_fields(setup):
    schema=app.openapi()['components']['schemas']
    assert {'claimDocuments','claimDocumentsNotice','claimDocumentsUrl'} <= set(schema['RagQueryResponse']['properties'])
    assert 'sourceChunkId' not in schema['ClaimDocument']['required']
    assert 'sourceQuote' not in schema['ClaimDocument']['required']
