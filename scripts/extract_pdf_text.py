import argparse
import json
from pathlib import Path

import fitz  # PyMuPDF


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "extracted_text"


def normalize_text(text: str) -> str:
    """
    PDF에서 추출된 텍스트의 기본 공백을 정리한다.
    너무 공격적으로 줄바꿈을 제거하지 않는다.
    약관은 줄 단위 제목 탐지가 중요하기 때문이다.
    """
    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.strip().split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def extract_pdf_pages(pdf_path: Path) -> list[dict]:
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF 파일을 찾을 수 없습니다: {pdf_path}")

    pages = []

    with fitz.open(pdf_path) as doc:
        if doc.is_encrypted:
            raise ValueError(f"암호화된 PDF는 처리할 수 없습니다: {pdf_path.name}")

        for page_index, page in enumerate(doc, start=1):
            text = page.get_text("text")
            normalized = normalize_text(text)

            pages.append(
                {
                    "page": page_index,
                    "text": normalized,
                }
            )

    return pages


def save_pages_json(pages: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(pages, f, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract page-level text from a travel insurance PDF."
    )
    parser.add_argument(
        "pdf_path",
        type=str,
        help="Path to input PDF file. Example: data/raw_pdfs/kakao_travel_2025.pdf",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory to save extracted page JSON.",
    )

    args = parser.parse_args()

    pdf_path = Path(args.pdf_path)
    output_dir = Path(args.output_dir)

    pages = extract_pdf_pages(pdf_path)

    output_path = output_dir / f"{pdf_path.stem}_pages.json"
    save_pages_json(pages, output_path)

    total_chars = sum(len(page["text"]) for page in pages)

    print(f"PDF: {pdf_path.name}")
    print(f"Pages: {len(pages)}")
    print(f"Characters: {total_chars}")
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    main()