"""Three source-bound checklist calls, reusing fixed incident contexts to limit cost."""
import json
from app.services.answer_providers import resolve
from app.services.claim_document_catalog import resolve_claim_insurer
from app.services.claim_incident_classifier import ClaimIncidentContext
from app.services.claim_document_selector import build_claim_document_response


def run(case_names=None):
    cases=[
        ('samsung_medical','삼성화재',dict(incidents=['medical'],care_type='outpatient',treatment_location='overseas',has_pharmacy_expense=False,is_minor=False,is_proxy_claim=False)),
        ('hanwha_theft','한화손해보험',dict(incidents=['theft'],is_phone=False,is_minor=False,is_proxy_claim=False)),
        ('db_outpatient','DB손해보험',dict(incidents=['medical'],care_type='outpatient',treatment_location='domestic',has_pharmacy_expense=False,claim_amount_krw=20000,is_minor=False,is_proxy_claim=False)),
    ]
    results=[]
    for cid,name,fields in cases:
        if case_names is not None and cid not in case_names:
            continue
        class Repo:
            def get_terms_insurer(self,terms_id): return name
        selection=resolve_claim_insurer('synthetic',Repo())
        response=build_claim_document_response(selection,ClaimIncidentContext(is_claim_document_question=True,**fields))
        docs=response.claim_documents
        names=[d.document_name for d in docs]
        errors=[]
        if not docs: errors.append('empty')
        if not response.claim_documents_url or not response.claim_documents_notice: errors.append('missing_notice_or_url')
        if len(names)!=len(set(''.join(n.split()) for n in names)): errors.append('duplicates')
        if not any('청구서' in n for n in names): errors.append('missing_common_claim_form')
        if cid=='samsung_medical':
            if any('약제비' in n or '처방전' in n for n in names): errors.append('pharmacy_false')
            if not any('진료비' in n for n in names): errors.append('missing_medical')
            if any(d.requirement_type!='CONDITIONAL' for d in docs if '진료비' in d.document_name or '초진' in d.document_name): errors.append('additional_marked_required')
        if cid=='hanwha_theft':
            if not any('도난' in n for n in names): errors.append('missing_theft')
            if any('가입' in n or '수리' in n or '친권자' in n or '위임장' in n for n in names): errors.append('wrong_conditional_documents')
        if cid=='db_outpatient':
            # A prescription can be an example of diagnosis proof even without
            # pharmacy costs; it is not a pharmacy-only requirement in that group.
            if any('약제' in n or n.strip()=='처방전' or '입퇴원' in n for n in names): errors.append('wrong_branch')
            if not any('진료비' in n for n in names): errors.append('missing_receipt')
        results.append({'case':cid,'passed':not errors,'errors':errors,'response':response.model_dump(by_alias=True)})
    return {'provider':resolve(None).name,'model':resolve(None).model,'calls':len(results),'passed':sum(r['passed'] for r in results),'results':results}


if __name__=='__main__':
    print(json.dumps(run(),ensure_ascii=True))
