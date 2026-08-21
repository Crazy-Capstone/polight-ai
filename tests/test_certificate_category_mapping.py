"""증권 담보명 -> 표준 카테고리 환산 (보장상세 연결용).

증권 담보명과 약관 규칙 title은 표기가 달라 이름으로 못 잇는다. 양쪽을 같은
표준 카테고리(medical_expense 등)로 환산해 백엔드가 그 키로 연결한다.
"""

from app.services.certificate_adapter import _standard_category


def test_증권_담보명이_표준_카테고리로_환산된다():
    # 실서버 coverage_items에 저장된 실제 메리츠 플랫폼 담보명 기준
    cases = {
        "(5세대)해외여행 상해_해외의료실비보장": "medical_expense",
        "(5세대)해외여행 질병_해외의료실비보장": "medical_expense",
        "휴대품손해(분실제외)": "baggage",
        "일괄배상 (해외여행중)": "liability",
        "항공기 및 수하물 지연비용": "flight_delay",
        "상해사망·후유장해 (해외여행중)": "death_disability",
        "해외여행중 중단사고 발생 추가비용": "trip_cancellation",
    }
    for title, expected in cases.items():
        assert _standard_category(title, None) == expected, title


def test_매핑_실패시_에이전트_원값으로_폴백():
    # 사전에 없는 담보는 에이전트가 준 값을 그대로 쓴다(없으면 None)
    assert _standard_category("여권분실후 재발급비용", "agent_val") == "agent_val"
    assert _standard_category("정체불명 담보 xyz", None) is None
