"""약관 표지에서 보험사·상품명·시행일 추출.

요소는 실제 9건의 파싱 결과에서 본 모양을 그대로 옮겼다. 회사마다 표지가 달라
한 형태만 맞춰두면 다른 회사에서 조용히 빈 값이 나온다.

가장 중요한 것은 "틀린 값을 내지 않는 것"이다. 보험사를 잘못 넣으면 증권이 엉뚱한
약관에 연결되고, 그건 등록을 빠뜨리는 것보다 나쁘다.
"""

from scripts.terms_cover import extract_cover_info


def el(page: int, category: str, text: str) -> dict:
    return {"page": page, "category": category, "text": text}


class TestProductName:
    def test_제목이_heading이면_그대로(self):
        info = extract_cover_info([
            el(1, "heading1", "캐롯 해외여행보험 약관"),
            el(1, "footer", "캐롯 해외여행보험 1"),
        ])
        assert info.product == "캐롯 해외여행보험"

    def test_괄호에_든_상품명을_꺼낸다(self):
        # DB손보 표지가 이 모양이다: "보험약관 (프로미 해외여행보험Ⅰ)"
        info = extract_cover_info([el(1, "heading1", "보험약관 (프로미 해외여행보험Ⅰ)")])
        assert info.product == "프로미 해외여행보험Ⅰ"

    def test_보통약관_접미사를_뗀다(self):
        info = extract_cover_info([el(1, "paragraph", "해외여행보험 보통약관")])
        assert info.product == "해외여행보험"

    def test_목차_안내문구는_상품명이_아니다(self):
        # 삼성화재 표지는 목차부터 시작한다. "보험용어"의 '보험' 때문에 통과하던 줄이다.
        info = extract_cover_info([
            el(1, "paragraph", "해외여행보험 목차"),
            el(1, "paragraph", "가입자 유의사항 주요내용 요약서 보험용어 해설"),
            el(1, "paragraph", "□ 보험약관"),
            el(1, "paragraph", "해외여행보험 보통약관"),
        ])
        assert info.product == "해외여행보험"

    def test_보험사명이_붙어_있으면_뗀다(self):
        info = extract_cover_info([
            el(1, "heading1", "현대해상 다이렉트 해외여행보험 약관"),
        ])
        assert info.insurer == "현대해상"
        assert info.product == "다이렉트 해외여행보험"


class TestRevision:
    def test_약관코드에서_시행일을_읽는다(self):
        info = extract_cover_info([
            el(1, "heading1", "다이렉트 해외여행보험"),
            el(1, "paragraph", "현대해상 8403-0000-20250630"),
        ])
        assert info.terms_code == "8403-0000-20250630"
        assert info.revision == "2025-06-30"
        assert info.confidence["revision"] == "high"

    def test_시행일_라벨이_있으면_읽는다(self):
        info = extract_cover_info([
            el(1, "heading1", "해외여행보험"),
            el(2, "paragraph", "시행일자 : 2024. 1. 1"),
        ])
        assert info.revision == "2024-01-01"
        assert info.confidence["revision"] == "high"

    def test_연월만_있으면_1일로_채우고_확신도를_낮춘다(self):
        # KB 표지가 "약관 [ 2018. 4 ]" 형태다. 일자를 1일로 채우는 것은 추측이라
        # 사람이 보게 해야 한다.
        info = extract_cover_info([
            el(1, "paragraph", "KB해외여행보험"),
            el(1, "paragraph", "약관 [ 2018. 4 ]"),
        ])
        assert info.revision == "2018-04-01"
        assert info.confidence["revision"] == "low"
        assert "revision" in info.needs_review

    def test_없으면_None이고_검토_대상이다(self):
        info = extract_cover_info([el(1, "heading1", "해외여행보험 약관")])
        assert info.revision is None
        assert "revision" in info.needs_review

    def test_말이_안_되는_날짜는_버린다(self):
        info = extract_cover_info([
            el(1, "heading1", "해외여행보험"),
            el(1, "paragraph", "9999-0000-20259931"),
        ])
        assert info.revision is None


class TestInsurer:
    def test_표지에_있으면_확신도가_높다(self):
        info = extract_cover_info([
            el(1, "paragraph", "해외여행 실손의료보험"),
            el(1, "paragraph", "메리츠화재해상보험주식회사"),
        ])
        assert info.insurer == "메리츠화재"
        assert info.confidence["insurer"] == "high"

    def test_표지에_없으면_앞부분까지_넓혀_찾되_확신도를_낮춘다(self):
        info = extract_cover_info([
            el(1, "heading1", "해외여행보험 약관"),
            el(7, "paragraph", "저희 삼성화재를 이용해 주셔서 감사합니다."),
        ])
        assert info.insurer == "삼성화재"
        assert info.confidence["insurer"] == "low"
        assert "insurer" in info.needs_review

    def test_본문_깊은_곳은_보지_않는다(self):
        # 실측에서 DB손보 약관 본문의 타사 언급이 보험사로 잡혔다.
        # 틀린 값보다 빈 값이 낫다.
        info = extract_cover_info([
            el(1, "heading1", "보험약관 (프로미 해외여행보험Ⅰ)"),
            el(80, "paragraph", "농협손해보험 등 다른 회사"),
        ])
        assert info.insurer is None
        assert info.product == "프로미 해외여행보험Ⅰ"

    def test_두_회사가_함께_적히면_사람에게_넘긴다(self):
        info = extract_cover_info([
            el(1, "heading1", "캐롯 해외여행보험 약관"),
            el(1, "paragraph", "캐롯손해보험 / 인수사 한화손해보험"),
        ])
        assert info.confidence["insurer"] == "medium"
        assert "insurer" in info.needs_review


class TestEmpty:
    def test_요소가_없으면_전부_빈값(self):
        info = extract_cover_info([])
        assert info.insurer is None
        assert info.product is None
        assert info.revision is None
        assert set(info.needs_review) == {"insurer", "product", "revision"}
