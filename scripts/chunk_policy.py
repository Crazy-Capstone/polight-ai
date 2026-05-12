import argparse
import json
import re
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_MAPPING_PATH = PROJECT_ROOT / "config" / "category_mapping.json"
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "extracted_text"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "chunks"


SECTION_TITLE_PATTERNS = [
    # 예: 제1장, 제2절
    re.compile(r"^(제\s*\d+\s*[장절])\s+(.+)$"),

    # 예: 제1조(보험금의 지급사유), 제 1 조 보험금의 지급사유
    re.compile(r"^(제\s*\d+\s*조)\s*[\(\[]?(.{2,80}?)[\)\]]?$"),

    # 예: 1. 해외여행중 상해의료비 특별약관
    re.compile(r"^\d{1,3}\.\s+(.{2,100})$"),

    # 예: 가. 보상하는 손해
    re.compile(r"^[가-힣]\.\s+(.{2,100})$"),

    # 약관에서 자주 나오는 큰 제목
    re.compile(r"^(.{2,100}(보통약관|특별약관|실손의료비|배상책임|휴대품손해|수하물|항공기|지연|결항|구조송환|여행중단|여행취소).*)$"),

    # 보장/제외/청구 관련 소제목
    re.compile(r"^(보상하는 손해|보상하지 않는 손해|보험금의 지급사유|보험금을 지급하지 않는 사유|보험금 청구|보험금 지급|용어의 정의)$"),
]


NOISE_TITLE_KEYWORDS = [
    "목차",
    "개인정보",
    "상품요약서",
    "가입자 유의사항",
    "주요내용 요약서",
]


def load_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")

    if path.stat().st_size == 0:
        raise ValueError(f"JSON 파일이 비어 있습니다: {path}")

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"JSON 형식이 올바르지 않습니다: {path}\n"
            f"line={e.lineno}, column={e.colno}, message={e.msg}"
        ) from e


def save_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def is_probable_title(line: str) -> bool:
    line = line.strip()

    if not line:
        return False

    if len(line) < 2 or len(line) > 120:
        return False

    if any(keyword in line for keyword in NOISE_TITLE_KEYWORDS):
        return False

    # 금액/문장형 긴 설명이 제목으로 잡히는 것을 줄임
    if line.count(",") >= 3:
        return False

    for pattern in SECTION_TITLE_PATTERNS:
        if pattern.match(line):
            return True

    return False


def extract_title(line: str) -> str:
    """
    패턴에 걸린 라인에서 실제 제목 후보를 반환한다.
    """
    line = line.strip()

    for pattern in SECTION_TITLE_PATTERNS:
        match = pattern.match(line)
        if not match:
            continue

        groups = [group for group in match.groups() if group]

        if not groups:
            return line

        # '제1조' 같은 조항 번호만 있는 그룹보다 뒤쪽 제목 그룹을 우선
        candidate = groups[-1].strip()
        return candidate

    return line


def build_mapping_entries(mapping: dict) -> list[dict]:
    """
    category_mapping.json을 매칭하기 쉬운 리스트로 변환한다.

    지원 형태:
    1) "상해의료비": "medical_expense"
    2) "상해의료비": {"primary_category": "medical_expense", "secondary_category": "baggage"}
    """
    entries = []

    for keyword, value in mapping.items():
        if isinstance(value, str):
            primary_category = value
            secondary_category = None
        elif isinstance(value, dict):
            primary_category = value.get("primary_category")
            secondary_category = value.get("secondary_category")
        else:
            continue

        if not primary_category:
            continue

        entries.append(
            {
                "keyword": keyword,
                "normalized_keyword": normalize_for_match(keyword),
                "primary_category": primary_category,
                "secondary_category": secondary_category,
            }
        )

    # 긴 키워드를 먼저 매칭해야 "수하물"보다 "항공기 및 수하물 지연비용"이 우선된다.
    entries.sort(key=lambda item: len(item["normalized_keyword"]), reverse=True)
    return entries


def match_category(text: str, mapping_entries: list[dict]) -> dict:
    normalized_text = normalize_for_match(text)

    for entry in mapping_entries:
        if entry["normalized_keyword"] in normalized_text:
            return {
                "matched_category": entry["primary_category"],
                "secondary_category": entry["secondary_category"],
                "matched_keyword": entry["keyword"],
            }

    return {
        "matched_category": None,
        "secondary_category": None,
        "matched_keyword": None,
    }


def flatten_pages(pages: list[dict]) -> list[dict]:
    """
    페이지별 텍스트를 라인 단위로 펼친다.
    각 라인에 page 정보를 유지한다.
    """
    rows = []

    for page in pages:
        page_number = page["page"]
        text = page.get("text", "")

        for line in text.splitlines():
            cleaned = line.strip()
            if cleaned:
                rows.append(
                    {
                        "page": page_number,
                        "line": cleaned,
                    }
                )

    return rows


def create_chunks(
    pages: list[dict],
    source_file: str,
    mapping_entries: list[dict],
    min_chunk_chars: int = 200,
) -> list[dict]:
    rows = flatten_pages(pages)

    chunks = []
    current_title = "문서 시작"
    current_lines = []
    current_page_start = rows[0]["page"] if rows else 1
    chunk_index = 1

    def flush_chunk(page_end: int) -> None:
        nonlocal chunk_index, current_lines, current_title, current_page_start

        text = "\n".join(current_lines).strip()

        if not text:
            return

        # 너무 짧은 목차성 chunk는 최대한 제외
        if len(text) < min_chunk_chars and current_title == "문서 시작":
            current_lines = []
            return

        category_result = match_category(
            f"{current_title}\n{text[:1000]}",
            mapping_entries,
        )

        chunk = {
            "chunk_id": f"{Path(source_file).stem}_{chunk_index:04d}",
            "source_file": source_file,
            "page_start": current_page_start,
            "page_end": page_end,
            "section_title": current_title,
            "text": text,
            "char_count": len(text),
            "matched_category": category_result["matched_category"],
            "secondary_category": category_result["secondary_category"],
            "matched_keyword": category_result["matched_keyword"],
        }

        chunks.append(chunk)
        chunk_index += 1
        current_lines = []

    for row in rows:
        line = row["line"]
        page = row["page"]

        if is_probable_title(line):
            # 새 제목이 나오면 이전 chunk 저장
            if current_lines:
                flush_chunk(page_end=page)

            current_title = extract_title(line)
            current_page_start = page
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        last_page = rows[-1]["page"] if rows else 1
        flush_chunk(page_end=last_page)

    return chunks


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create section-based chunks from extracted travel insurance PDF pages."
    )
    parser.add_argument(
        "pages_json",
        type=str,
        help="Path to extracted pages JSON. Example: data/extracted_text/kakao_travel_2025_pages.json",
    )
    parser.add_argument(
        "--mapping",
        type=str,
        default=str(DEFAULT_MAPPING_PATH),
        help="Path to category_mapping.json.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory to save chunks JSON.",
    )
    parser.add_argument(
        "--min-chunk-chars",
        type=int,
        default=200,
        help="Minimum chunk character count for early noise filtering.",
    )

    args = parser.parse_args()

    pages_path = Path(args.pages_json)
    mapping_path = Path(args.mapping)
    output_dir = Path(args.output_dir)

    pages = load_json(pages_path)
    mapping = load_json(mapping_path)
    mapping_entries = build_mapping_entries(mapping)

    source_file = pages_path.name.replace("_pages.json", ".pdf")

    chunks = create_chunks(
        pages=pages,
        source_file=source_file,
        mapping_entries=mapping_entries,
        min_chunk_chars=args.min_chunk_chars,
    )

    output_path = output_dir / pages_path.name.replace("_pages.json", "_chunks.json")
    save_json(chunks, output_path)

    matched_count = sum(1 for chunk in chunks if chunk["matched_category"])
    categories = sorted(
        {
            chunk["matched_category"]
            for chunk in chunks
            if chunk["matched_category"]
        }
    )

    print(f"Input: {pages_path}")
    print(f"Chunks: {len(chunks)}")
    print(f"Matched chunks: {matched_count}")
    print(f"Matched categories: {categories}")
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    main()