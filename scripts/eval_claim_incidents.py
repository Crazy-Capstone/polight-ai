"""Six small, synthetic live-model checks. No real customer data is used."""
import json
import time

from app.services.answer_providers import resolve
from app.services.claim_incident_classifier import classify_claim_incident

CASES = [
    ('phone_theft', '휴대폰을 도난당했는데 청구서류 뭐가 필요해요?', [],
     {'incidents': ['theft'], 'is_phone': True}),
    ('damage_followup', '수리 못 한대요', [
        {'role': 'user', 'content': '휴대폰 깨졌는데 필요한 서류 알려주세요.'},
        {'role': 'assistant', 'content': '수리 가능한가요, 수리 불가 판정을 받았나요?'}],
     {'incidents': ['damage'], 'is_phone': True, 'repair_status': 'unrepairable'}),
    ('overseas_outpatient', '일본 병원에서 통원 치료하고 약국에서 약도 샀어요. 청구 서류 알려주세요.', [],
     {'incidents': ['medical'], 'care_type': 'outpatient', 'treatment_location': 'overseas', 'has_pharmacy_expense': True}),
    ('minor_proxy', '미성년자인 아들이 해외에서 입원했어요. 제가 아들 대신 대리청구하려는데 서류가 뭔가요?', [],
     {'incidents': ['medical'], 'care_type': 'inpatient', 'treatment_location': 'overseas', 'is_minor': True, 'is_proxy_claim': True}),
    ('multiple_delays', '항공편도 지연되고 맡긴 수하물도 늦게 왔어요. 각각 필요한 서류가 뭔가요?', [],
     {'incidents': ['flight_delay', 'baggage_delay'], 'is_airline_baggage': True, 'benefit_type': None}),
    ('correction_unknown', '정정할게요. 입원이 아니라 한국에서 통원했어요. 청구금액은 아직 몰라요.', [
        {'role': 'user', 'content': '일본에서 입원했는데 필요한 서류가 뭔가요?'},
        {'role': 'assistant', 'content': '입원서류를 안내하겠습니다.'}],
     {'incidents': ['medical'], 'care_type': 'outpatient', 'treatment_location': 'domestic', 'claim_amount_krw': None,
      'unavailable_conditions_contains': 'claim_amount_krw'}),
]


def run(case_names=None):
    rows = []
    for name, question, history, expected in CASES:
        if case_names is not None and name not in case_names:
            continue
        started = time.monotonic()
        context = classify_claim_incident(question, history)
        actual = context.model_dump() if context else None
        errors = []
        if actual is None:
            errors.append('no_valid_context')
        else:
            if not actual['is_claim_document_question']:
                errors.append('is_claim_document_question')
            for field, value in expected.items():
                if field.endswith('_contains'):
                    if value not in actual[field.removesuffix('_contains')]:
                        errors.append(field)
                elif field == 'incidents':
                    if set(actual[field]) != set(value):
                        errors.append(field)
                elif actual[field] != value:
                    errors.append(field)
        rows.append({'case': name, 'passed': not errors, 'errors': errors,
                     'seconds': round(time.monotonic() - started, 2), 'actual': actual})
    return {'provider': resolve(None).name, 'model': resolve(None).model,
            'calls': len(rows), 'passed': sum(r['passed'] for r in rows), 'results': rows}


if __name__ == '__main__':
    print(json.dumps(run(), ensure_ascii=True))
