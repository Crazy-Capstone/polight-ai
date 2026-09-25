"""증권 -> 약관 매칭 엔드포인트.

백엔드가 자기 로직으로 매칭하다가 표기 흔들림을 못 넘어 연결 30건 중 8건이 빠졌다.
실제 로그가 이랬다.

    약관을 연결하지 않음: 보험사=삼성화재해상보험주식회사,
    근거='삼성화재해상보험주식회사' 보험사로 등록된 약관이 없습니다.

규칙을 두 벌로 두면 한쪽만 고쳐진다. 그래서 우리 것을 열고 백엔드는 호출만 한다.
여기서 고정하는 것은 "실제 증권에서 나온 표기가 약관에 닿는가"다.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import terms_match_service

client = TestClient(app)

# 적재된 약관을 흉내낸다. id는 policy_terms.id(UUID) 자리다.
CANDIDATES = [
    {"id": "uuid-samsung-2024", "insurer": "삼성화재", "product": "해외여행보험",
     "revision": "2024-01-01", "effective_date": date(2024, 1, 1), "aliases": []},
    {"id": "uuid-samsung-2026", "insurer": "삼성화재", "product": "해외여행보험",
     "revision": "2026-06-06", "effective_date": date(2026, 6, 6), "aliases": []},
    {"id": "uuid-samsung-365", "insurer": "삼성화재", "product": "365연간해외여행보험",
     "revision": "2026-06-06", "effective_date": date(2026, 6, 6), "aliases": []},
    {"id": "uuid-carrot", "insurer": "캐롯손해보험", "product": "캐롯 해외여행보험",
     "revision": "2025-09-01", "effective_date": date(2025, 9, 1), "aliases": [],
     "underwriter_aliases": ["한화손해보험"]},
]


@pytest.fixture(autouse=True)
def _candidates(monkeypatch):
    monkeypatch.setattr(terms_match_service, "_candidates", lambda: CANDIDATES)


def post(**body):
    return client.post("/internal/terms/match", json=body).json()


# 이 건이 실제로 막혔던 케이스다. 증권에는 법인 정식명칭이, 약관에는 통용 명칭이 있다.
def test_corporate_name_reaches_the_terms():
    body = post(
        insurerName="삼성화재해상보험주식회사",
        productName="해외여행보험 (Travel Overseas)",
        insuranceStartDate="2026-07-30",
    )

    assert body["termsId"] == "uuid-samsung-2026"
    assert body["level"] == "EXACT"
    assert body["notice"] is None


# 가입 시점에 유효했던 판을 골라야 한다. 최신판을 주면 그 사이 바뀐 조항으로 답한다.
def test_picks_the_revision_in_force_at_purchase():
    body = post(insurerName="삼성화재", productName="해외여행보험",
                insuranceStartDate="2025-03-01")

    assert body["termsId"] == "uuid-samsung-2024", "가입일 이후에 나온 판이 뽑혔다"
    assert body["revision"] == "2024-01-01"


# 가입일을 모르면 최신판이 가장 나은 추정이다.
def test_without_start_date_uses_the_latest():
    body = post(insurerName="삼성화재", productName="해외여행보험")

    assert body["termsId"] == "uuid-samsung-2026"


# 보유한 판이 전부 가입일 뒤에 나왔다면 그 시점 판이 우리에게 없다는 뜻이다.
# 조용히 다른 판으로 답하면 사용자가 틀린 조건을 믿게 되므로 알려야 한다.
def test_flags_when_no_revision_covers_the_purchase():
    body = post(insurerName="삼성화재", productName="해외여행보험",
                insuranceStartDate="2020-01-01")

    assert body["level"] == "REVISION"
    assert "개정판" in body["notice"]


# 이름이 닮은 다른 상품으로 새면 안 된다.
def test_does_not_leak_into_a_similar_product():
    body = post(insurerName="삼성화재", productName="해외여행보험",
                insuranceStartDate="2026-07-30")

    assert body["productName"] == "해외여행보험"


# 제휴 판매는 증권에 인수사가 적힌다.
def test_matches_via_underwriter_alias():
    body = post(insurerName="한화손해보험(주)", productName="해외여행보험")

    assert body["termsId"] == "uuid-carrot"


# 없는 보험사는 실패가 아니라 NONE이다. 보장 카드는 증권에서 나오므로 화면은 떠야 한다.
def test_unknown_insurer_is_not_an_error():
    body = post(insurerName="롯데손해보험", productName="해외여행보험")

    assert body["level"] == "NONE"
    assert body["termsId"] is None
    assert "보유하고 있지 않아" in body["notice"]


# 증권에서 상품명을 못 읽는 경우가 있다. 그래도 500이 나면 안 된다.
def test_missing_product_name_does_not_crash():
    body = post(insurerName="삼성화재")

    assert body["level"] in ("INSURER", "NONE")
