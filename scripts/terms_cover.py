"""약관 PDF 표지에서 보험사·상품명·시행일을 뽑는다.

레지스트리 등록을 손으로 하면 약관 1건마다 필드를 대여섯 개 적어야 한다. 보험사를
정해 약관을 통째로 모으면 30건이 넘는데, 그러면 등록만으로 반나절이 간다.

표지에는 그 값들이 다 인쇄돼 있고 우리는 이미 전체를 파싱하므로, 여기서 뽑는 데는
추가 비용이 들지 않는다.

필드마다 확신도를 함께 돌려준다. 표지 형식이 회사마다 달라 100% 맞힐 수 없고,
틀린 값이 조용히 등록되면 증권 매칭이 엉뚱한 약관을 가리킨다. 그쪽이 등록을
빠뜨리는 것보다 나쁘므로, 사람이 검토할 자리를 남겨둔다.

    from app.services.upstage_parser import parse_pdf
    from scripts.terms_cover import extract_cover_info

    info = extract_cover_info(parse_pdf(pdf_path))
    info.insurer      # "현대해상"
    info.product      # "다이렉트 해외여행보험"
    info.revision     # "2025-06-30"
    info.terms_code   # "8403-0000-20250630"
    info.needs_review # ["product"]  <- 이 필드는 사람이 봐야 한다
"""

import re
from dataclasses import dataclass, field
from datetime import date

# 표지에서 찾을 범위.
#
# 1페이지만 보면 시행일을 놓친다. 표지에 상품명만 크게 넣고 시행일·약관코드는
# 다음 장의 판권 표기에 두는 회사가 있다. 반대로 전체를 훑으면 본문의 날짜
# (예: "2020년 1월 1일 이후 발생한 사고")를 시행일로 오인한다.
COVER_PAGES = 3

# 표지에 회사명이 없을 때 더 넓게 볼 범위.
#
# 전체를 훑으면 오검출이 난다. 실측에서 DB손보 약관 본문의 타사 언급이 보험사로
# 잡혔다. 회사명은 표지·인사말·판권 표기에 나오므로 앞부분이면 충분하고,
# 본문 깊은 곳의 다른 회사 이름은 보지 않는 편이 안전하다.
FALLBACK_PAGES = 10

# 국내 손해보험사. 여행자보험을 파는 회사 위주로 적었다.
#
# 이름 목록으로 찾는 이유: "(주)"나 "화재해상보험" 같은 표기가 회사마다 달라
# 정규식으로 잡으면 "해상보험"만 걸리거나 상품명 일부를 회사명으로 오인한다.
# 새 보험사를 수집하면 여기에 추가한다 - 없으면 insurer가 None으로 나오고
# 등록 초안에서 사람이 채우게 된다.
INSURERS = (
    "메리츠화재",
    "한화손해보험",
    "롯데손해보험",
    "MG손해보험",
    "흥국화재",
    "삼성화재",
    "현대해상",
    "KB손해보험",
    "DB손해보험",
    "캐롯손해보험",
    "하나손해보험",
    "AXA손해보험",
    "AIG손해보험",
    "NH농협손해보험",
    "농협손해보험",
    "카카오페이손해보험",
    "신한EZ손해보험",
)

# 표지 제목 끝에 붙는 말. 상품명에서 떼어낸다.
#
# "보험약관"은 넣지 않는다. "해외여행보험약관"에서 그것을 떼면 "해외여행"이 남아
# 상품명이 아니게 된다. "약관"만 떼면 "해외여행보험"이 남아 정확하다.
TITLE_SUFFIXES = ("보통약관", "약관")

# 상품명 후보에서 제외할 줄. 표지·목차에 함께 인쇄되는 안내 문구들이다.
#
# 실측에서 삼성화재 약관의 "가입자 유의사항 주요내용 요약서 보험용어 해설"이 상품명으로
# 잡혔다. "보험용어"의 '보험' 때문에 통과한 것이라, 문구 자체를 걸러야 한다.
TITLE_NOISE = (
    "목차", "고객센터", "보험회사", "금융감독원", "상품요약서", "사업방법서",
    "유의사항", "요약서", "용어 해설", "용어해설", "안내사항",
)

# 표지 제목 앞에 붙는 장식. 떼어내지 않으면 "□ 보험약관"이 상품명으로 잡힌다.
TITLE_PREFIX = "□■●▶※◇◆·-[]「」 \t"

# "보험약관 (프로미 해외여행보험Ⅰ)" 처럼 제목이 약관이고 상품명이 괄호에 든 형태.
# DB손보 약관이 이 모양이다.
TITLE_IN_PARENS = re.compile(r"^(?:보험)?약관\s*[(（]\s*(.+?)\s*[)）]$")

# 상품명 최소 길이. "보험"(2자)이나 "보험약관"에서 접미사를 뗀 잔여물을 걸러낸다.
TITLE_MIN_CHARS = 6

# 약관코드. 끝 8자리가 시행일이다.
#
#   8403-0000-20250630  ->  2025-06-30
#
# 회사마다 자리수가 조금 다르지만 "숫자-숫자-YYYYMMDD" 형태는 공통이다.
# 이 코드는 공시실 목록에도 그대로 찍혀 있어, 나중에 개정판을 대조할 때
# PDF를 받지 않고 날짜만 비교하는 근거가 된다.
TERMS_CODE = re.compile(r"(?<![\d-])(\d{3,4}-\d{3,4}-(\d{8}))(?![\d-])")

# "시행일자: 2025.06.30" 처럼 라벨이 붙은 날짜. 가장 믿을 수 있다.
LABELED_DATE = re.compile(
    r"(?:시행일자?|개정일자?|적용일자?)\s*[:：]?\s*"
    r"(\d{4})\s*[.\-년]\s*(\d{1,2})\s*[.\-월]\s*(\d{1,2})"
)

# "2025년 6월 30일부터 시행" 처럼 날짜 뒤에 말이 붙은 경우.
DATE_THEN_WORD = re.compile(
    r"(\d{4})\s*[.\-년]\s*(\d{1,2})\s*[.\-월]\s*(\d{1,2})\s*일?\s*"
    r"(?:부터\s*)?(?:시행|개정|적용)"
)

# "약관 [ 2018. 4 ]" 처럼 일자 없이 연·월만 적힌 경우. KB 약관이 이 모양이다.
#
# 일자를 1일로 채운다. 추측이지만 개정판을 가르는 단위는 월이라 실용상 충분하고,
# 확신도를 low로 내려 사람이 보게 한다. "약관" + 괄호를 함께 요구해서 본문의
# 아무 연·월이나 집히지 않게 한다.
TERMS_YEAR_MONTH = re.compile(
    r"약관\s*[\[(（]\s*(\d{4})\s*[.\-년]\s*(\d{1,2})\s*[\])）]"
)


@dataclass
class CoverInfo:
    insurer: str | None = None
    product: str | None = None
    revision: str | None = None
    terms_code: str | None = None
    # 필드별 확신도: high / medium / low / none.
    # high가 아니면 등록 전에 사람이 봐야 한다.
    confidence: dict[str, str] = field(default_factory=dict)

    @property
    def needs_review(self) -> list[str]:
        """사람이 확인해야 하는 필드 이름들."""
        return [
            name
            for name in ("insurer", "product", "revision")
            if getattr(self, name) is None or self.confidence.get(name) != "high"
        ]


def _match_insurers(text: str) -> list[str]:
    hits = [name for name in INSURERS if name in text]
    # 서로 포함 관계인 이름은 긴 쪽만 남긴다(농협손해보험 ⊂ NH농협손해보험).
    # 이걸 하지 않으면 같은 회사가 둘로 세어져 확신도가 부당하게 내려간다.
    return [n for n in hits if not any(n != m and n in m for m in hits)]


def _find_insurer(cover_text: str, early_text: str) -> tuple[str | None, str]:
    """표지에서 먼저 찾고, 없으면 앞 FALLBACK_PAGES 페이지까지 넓혀 찾는다.

    실측 9건 중 3건(삼성·캐롯·DB)은 앞 3페이지에 회사명이 텍스트로 없었다. 로고가
    이미지이거나 목차부터 시작하는 약관이다. 인사말·판권 표기에는 적혀 있어
    조금 넓히면 찾히지만, 그만큼 타사 언급을 집을 위험이 커서 확신도를 낮춘다.
    """
    for text, base in ((cover_text, "high"), (early_text, "low")):
        distinct = _match_insurers(text)
        if not distinct:
            continue
        longest = max(distinct, key=len)
        # 둘 이상 남으면 판매사·인수사가 함께 적힌 경우다(캐롯 약관의 한화손보 같은).
        # 어느 쪽이 보험사인지 문서만으로는 못 정하므로 사람에게 넘긴다.
        if len(distinct) > 1:
            return longest, "medium" if base == "high" else "low"
        return longest, base
    return None, "none"


def _clean_title(text: str, insurer: str | None) -> str | None:
    line = " ".join(text.split())

    if any(noise in line for noise in TITLE_NOISE):
        return None

    line = line.strip(TITLE_PREFIX)

    parens = TITLE_IN_PARENS.match(line)
    if parens:
        line = parens.group(1)

    if insurer:
        line = line.replace(insurer, "").strip()

    for suffix in TITLE_SUFFIXES:
        if line.endswith(suffix):
            line = line[: -len(suffix)].strip()
            break

    # "보험"이 없으면 상품명이 아니다. 길이 제한은 표지의 안내 문장을 걸러낸다.
    if "보험" not in line or not (TITLE_MIN_CHARS <= len(line) <= 60):
        return None
    return line


def _find_product(cover: list[dict], insurer: str | None) -> tuple[str | None, str]:
    first_page = [e for e in cover if (e.get("page") or 0) <= 1]

    # 표지 제목은 글자가 커서 heading으로 잡힌다. 그쪽이 정확하다.
    headings = [
        e.get("text", "")
        for e in first_page
        if str(e.get("category", "")).startswith("heading")
    ]
    others = [e.get("text", "") for e in first_page]

    for pool, confidence in ((headings, "high"), (others, "low")):
        for text in pool:
            name = _clean_title(text, insurer)
            if name:
                return name, confidence
    return None, "none"


def _iso(year: str, month: str, day: str) -> str | None:
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def _find_revision(joined: str) -> tuple[str | None, str | None, str]:
    """(약관코드, 시행일, 확신도)."""
    code_match = TERMS_CODE.search(joined)
    if code_match:
        digits = code_match.group(2)
        iso = _iso(digits[:4], digits[4:6], digits[6:8])
        if iso:
            # 약관코드에 박힌 날짜는 회사가 부여한 식별자라 오인할 여지가 없다.
            return code_match.group(1), iso, "high"

    labeled = LABELED_DATE.search(joined)
    if labeled:
        iso = _iso(*labeled.groups())
        if iso:
            return None, iso, "high"

    trailing = DATE_THEN_WORD.search(joined)
    if trailing:
        iso = _iso(*trailing.groups()[:3])
        if iso:
            # 라벨이 없어 본문의 "2020년 1월 1일 이후 시행" 같은 문구일 수 있다.
            return None, iso, "medium"

    year_month = TERMS_YEAR_MONTH.search(joined)
    if year_month:
        iso = _iso(year_month.group(1), year_month.group(2), "1")
        if iso:
            return None, iso, "low"

    return None, None, "none"


def extract_cover_info(elements: list[dict]) -> CoverInfo:
    """파싱된 요소에서 표지 정보를 뽑는다. 요소가 비면 빈 CoverInfo."""
    cover = [e for e in elements if (e.get("page") or 0) <= COVER_PAGES]
    joined = "\n".join(e.get("text", "") for e in cover)
    early = "\n".join(
        e.get("text", "") for e in elements if (e.get("page") or 0) <= FALLBACK_PAGES
    )

    info = CoverInfo()
    info.insurer, insurer_conf = _find_insurer(joined, early)
    info.product, product_conf = _find_product(cover, info.insurer)
    info.terms_code, info.revision, revision_conf = _find_revision(joined)
    info.confidence = {
        "insurer": insurer_conf,
        "product": product_conf,
        "revision": revision_conf,
    }
    return info
