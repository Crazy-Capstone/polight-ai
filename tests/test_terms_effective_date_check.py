"""적재 전 시행일 점검.

백엔드 매칭은 상품이 맞는 약관이 여럿이면 보험 시작일과 시행일을 비교해 판을
고른다. 시행일이 비면 후보를 가리지 못해 연결을 포기한다(NONE).

그러면 약관은 제대로 들어갔는데 챗봇은 근거가 없다고 답한다. 원인이 값 하나가
비었다는 것이라, 넣고 나서 로그만 보고 찾기 어렵다. 그래서 넣기 전에 막는다.
"""

from datetime import date

import pytest

from scripts.migrate_terms_to_db import effective_date_conflicts


def entry(product: str, revision: str | None, insurer: str = "현대해상") -> dict:
    return {"id": product, "insurer": insurer, "product": product, "revision": revision}


def held(product: str, revision: str | None, effective: date | None,
         insurer: str = "현대해상") -> dict:
    return {
        "insurer_name": insurer,
        "product_name": product,
        "revision": revision,
        "effective_date": effective,
    }


class TestBlocks:
    def test_판이_둘인데_하나가_비면_막는다(self):
        entries = [entry("해외여행보험", "2022-07-18"), entry("해외여행보험", None)]

        assert effective_date_conflicts(entries, [])

    # 이번에 한 판만 넣어도 DB에 다른 판이 있으면 그 순간부터 후보가 둘이 된다.
    def test_DB에_있는_판까지_센다(self):
        entries = [entry("해외여행보험", None)]
        rows = [held("해외여행보험", "2022-07-18", date(2022, 7, 18))]

        assert effective_date_conflicts(entries, rows)

    # 공백·괄호가 다른 것은 같은 상품이다. 백엔드도 그렇게 묶으므로, 우리 눈에만
    # 달라 보여서 "판이 하나뿐"으로 넘어가면 안 된다.
    def test_공백_표기가_달라도_같은_상품으로_묶는다(self):
        entries = [entry("해외 여행보험", None)]
        rows = [held("해외여행보험", "2022-07-18", date(2022, 7, 18))]

        assert effective_date_conflicts(entries, rows)


class TestAllows:
    # 판이 하나면 고를 후보가 없어 비교할 일이 없다.
    # db_travel 2건이 실제로 이 경우다 - revision 없이 들어가 있고 상품명이 다르다.
    def test_판이_하나면_비어도_된다(self):
        assert not effective_date_conflicts([entry("프로미 해외여행보험Ⅰ", None)], [])

    def test_상품이_다르면_각각_하나다(self):
        entries = [entry("프로미 해외여행보험Ⅰ", None), entry("프로미 해외여행보험Ⅳ", None)]

        assert not effective_date_conflicts(entries, [])

    def test_판이_둘이어도_시행일이_다_있으면_통과(self):
        entries = [entry("해외여행보험", "2022-07-18"), entry("해외여행보험", "2025-06-30")]

        assert not effective_date_conflicts(entries, [])

    # 이번에 넣지 않는 상품은 볼 필요가 없다.
    def test_적재_대상이_아닌_DB_행은_보지_않는다(self):
        entries = [entry("해외여행보험", "2022-07-18")]
        rows = [held("운전자보험", None, None)]

        assert not effective_date_conflicts(entries, rows)


# 막을 때는 어느 상품의 어느 판이 비었는지 알려줘야 한다.
def test_어느_판이_비었는지_알려준다():
    entries = [entry("해외여행보험", "2022-07-18"), entry("해외여행보험", None)]

    conflicts = effective_date_conflicts(entries, [])
    (revisions,) = conflicts.values()

    assert revisions["2022-07-18"] == date(2022, 7, 18)
    assert revisions[""] is None
