"""증권 ↔ 보유 약관 대조 판정.

이 판정의 실패 모드는 "안 걸리는 것"이 아니라 "다 걸리는 것"이다. 시행일보다
가입일이 뒤인 것은 정상 상태(2025-06-30 시행판으로 2025-07-01에 가입)인데, 그것을
신호로 쓰면 모든 증권이 알림이 되어 목록을 아무도 보지 않게 된다.

그래서 정상 케이스가 OK로 떨어지는지를 함께 고정한다.
"""

from datetime import date, timedelta

from app.services import terms_watch as tw


def held(effective: date | None, product: str = "해외여행보험") -> dict:
    return {
        "product_name": product,
        "revision": effective.isoformat() if effective else None,
        "effective_date": effective,
    }


class TestNormalIsNotAnAlert:
    def test_시행_직후_가입은_정상(self):
        rows = [held(date(2025, 6, 30))]
        verdict, _ = tw._verdict(rows, rows, date(2025, 7, 1))
        assert verdict == tw.OK

    def test_임계값_직전까지는_정상(self):
        effective = date(2025, 1, 1)
        rows = [held(effective)]
        start = effective + timedelta(days=tw.STALE_AFTER_DAYS)
        verdict, _ = tw._verdict(rows, rows, start)
        assert verdict == tw.OK

    def test_가입일과_시행일이_같으면_정상(self):
        rows = [held(date(2025, 6, 30))]
        verdict, _ = tw._verdict(rows, rows, date(2025, 6, 30))
        assert verdict == tw.OK


class TestActionable:
    def test_임계값을_넘으면_확인_대상(self):
        effective = date(2025, 1, 1)
        rows = [held(effective)]
        start = effective + timedelta(days=tw.STALE_AFTER_DAYS + 1)
        verdict, reason = tw._verdict(rows, rows, start)
        assert verdict == tw.STALE
        assert "개정판" in reason

    def test_가입_시점_판이_없으면_확정(self):
        # 사용자는 2024년에 가입했는데 우리는 2025년판만 갖고 있다.
        # 챗봇이 실제로 다른 판으로 답하고 있는 상태다.
        rows = [held(date(2025, 6, 30))]
        verdict, reason = tw._verdict(rows, rows, date(2024, 1, 1))
        assert verdict == tw.MISSING_REVISION
        assert "2025-06-30" in reason

    def test_여러_판_중_가입일을_감싸면_정상(self):
        rows = [held(date(2022, 7, 18)), held(date(2025, 6, 30))]
        verdict, _ = tw._verdict(rows, rows, date(2025, 1, 1))
        assert verdict == tw.OK

    def test_상품이_없으면_상품_누락(self):
        insurer_rows = [held(date(2025, 6, 30), product="다른상품")]
        verdict, _ = tw._verdict([], insurer_rows, date(2026, 3, 1))
        assert verdict == tw.MISSING_PRODUCT

    def test_보험사_약관이_아예_없으면_보험사_누락(self):
        verdict, _ = tw._verdict([], [], date(2026, 3, 1))
        assert verdict == tw.MISSING_INSURER


class TestUnknown:
    def test_시행일이_비어_있으면_비교_불가(self):
        rows = [held(None)]
        verdict, _ = tw._verdict(rows, rows, date(2026, 3, 1))
        assert verdict == tw.UNKNOWN

    def test_가입일을_못_읽으면_비교_불가(self):
        rows = [held(date(2025, 6, 30))]
        verdict, _ = tw._verdict(rows, rows, None)
        assert verdict == tw.UNKNOWN


class TestPriority:
    def test_확정이_의심보다_먼저다(self):
        # show_terms_alerts.py가 이 순서로 정렬한다. 틀린 판으로 답하고 있는 1건이
        # 오래됐을지도 모르는 10건보다 급하다.
        assert tw.ACTIONABLE.index(tw.MISSING_REVISION) < tw.ACTIONABLE.index(tw.STALE)

    def test_조치_목록에_관찰_판정은_없다(self):
        assert tw.OK not in tw.ACTIONABLE
        assert tw.UNKNOWN not in tw.ACTIONABLE
        assert tw.NO_TERMS_ID not in tw.ACTIONABLE


class TestFailureIsolation:
    def test_DB가_없으면_조용히_넘어간다(self, monkeypatch):
        # 파일 저장소로 도는 로컬·평가 환경. 대조할 policy_terms가 없다.
        # 여기서 예외가 나면 증권 분석 전체가 실패한다.
        from app.core import config

        monkeypatch.setattr(
            config, "get_settings", lambda: type("S", (), {"database_url": ""})()
        )
        monkeypatch.setattr(tw, "get_settings", config.get_settings)
        assert tw.check_certificate("현대해상", "해외여행보험", "2026-03-01") == tw.UNKNOWN

    def test_조회가_터져도_예외를_내지_않는다(self, monkeypatch):
        monkeypatch.setattr(
            tw, "get_settings", lambda: type("S", (), {"database_url": "postgresql://nope"})()
        )
        monkeypatch.setattr(tw, "_append", lambda record: None)
        # DSN이 가짜라 접속 자체가 실패한다. 그래도 판정만 UNKNOWN으로 돌려준다.
        assert tw.check_certificate("현대해상", "해외여행보험", "2026-03-01") == tw.UNKNOWN
