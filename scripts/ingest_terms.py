"""약관 PDF를 색인해 검색 가능한 상태로 만든다.

증권은 사용자가 올리지만 약관은 우리가 미리 넣는다. 같은 보험 상품의 약관은
사용자가 달라도 내용이 같으므로, 한 번 색인해 모두가 공유한다.

지금까지는 두 단계로 나뉘어 있었고(run_upstage_pipeline.py -> embed_chunks.py)
레지스트리 등록은 손으로 했다. 한 번에 하지 않으면 임베딩을 빠뜨린 채로 넘어가는데,
그러면 검색 결과가 0건이 되고 원인이 "약관이 없다"로만 보인다.

    python scripts/ingest_terms.py                        # raw_pdfs 전체
    python scripts/ingest_terms.py --pdf carrot_travel_2025.pdf
    python scripts/ingest_terms.py --pdf 새약관.pdf --insurer 캐롯손해보험 --product "캐롯 해외여행보험"

레지스트리에 없는 약관은 등록 초안을 찍어준다. 보험사·상품명·시행일은 약관 표지에서
읽어 채우므로(scripts/terms_cover.py) 손으로 적을 것이 source_url 하나로 줄어든다.
표지 형식이 회사마다 달라 100%는 아니어서, 확신이 낮은 필드는 따로 표시한다.

색인한 PDF의 지문(sha256)은 data/chunks/{id}_meta.json에 남는다. 적재 때
policy_terms.file_hash로 들어가 개정판 대조에 쓰인다.

저장 위치는 DATABASE_URL 유무로 갈린다.

    비어 있음   data/chunks + data/embeddings (파일 저장소)
    설정됨      policy_chunks -- 다만 이 테이블은 user_id/analysis_result_id가
                NOT NULL이라 주인 없는 공유 약관을 넣을 수 없다. 백엔드에 요청한
                policy_terms 테이블이 생기기 전까지는 파일 저장소를 쓴다.
                docs/BACKEND_INTERFACE.md 3-2 참고.
"""

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.services.embedding_providers import build_client, get_provider  # noqa: E402
from app.services.upstage_parser import cached_elements, parse_pdf  # noqa: E402
from scripts.chunk_policy import (  # noqa: E402
    DEFAULT_MAPPING_PATH,
    build_mapping_entries,
    create_chunks_from_elements,
    load_json,
    save_json,
)
from scripts.embed_chunks import embed_chunks_file  # noqa: E402
from scripts.terms_cover import CoverInfo, extract_cover_info  # noqa: E402

RAW_PDF_DIR = PROJECT_ROOT / "data" / "raw_pdfs"
CHUNKS_DIR = PROJECT_ROOT / "data" / "chunks"
EMBEDDINGS_DIR = PROJECT_ROOT / "data" / "embeddings"
REGISTRY_PATH = PROJECT_ROOT / "config" / "terms_registry.json"


def registered(terms_id: str) -> dict | None:
    with REGISTRY_PATH.open("r", encoding="utf-8") as f:
        for entry in json.load(f)["terms"]:
            if entry["id"] == terms_id:
                return entry
    return None


def meta_path(terms_id: str) -> Path:
    return CHUNKS_DIR / f"{terms_id}_meta.json"


def _file_hash(pdf_path: Path) -> str:
    digest = hashlib.sha256()
    with pdf_path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# 색인한 PDF의 지문을 청크 옆에 남긴다.
#
# policy_terms.file_hash를 채우는 재료다. 적재(migrate_terms_to_db.py)는 AI 컨테이너
# 안에서 도는데, 그 서버에는 약관 PDF가 없다 - 증권이 섞여 들어갈 위험 때문에
# data/chunks와 data/embeddings만 옮기기로 했다. 그래서 PDF에서 그때 계산할 수 없고,
# 색인 시점에 청크와 같은 폴더에 적어 함께 옮겨지게 한다.
def write_meta(terms_id: str, pdf_path: Path) -> dict:
    meta = {
        "terms_id": terms_id,
        "source_file": pdf_path.name,
        "file_hash": _file_hash(pdf_path),
        "indexed_at": date.today().isoformat(),
    }
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    with meta_path(terms_id).open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


# 표지 정보. 이미 파싱된 요소가 있으면 그것을 쓰고, 없으면 캐시만 본다.
#
# 등록 초안을 만들려고 유료 파싱을 다시 부르지 않는다. 캐시도 없으면 None이고,
# 그때는 초안의 값이 비어 사람이 채우게 된다.
def cover_for(pdf_path: Path, elements: list[dict] | None = None) -> CoverInfo | None:
    els = elements if elements is not None else cached_elements(pdf_path)
    return extract_cover_info(els) if els else None


_FIELD_LABELS = {"insurer": "보험사", "product": "상품명", "revision": "시행일"}


# 레지스트리에 붙여넣을 초안.
#
# --insurer/--product로 준 값이 표지에서 읽은 값보다 우선한다. 사람이 명시한 쪽이
# 정확하고, 표지 추출은 그것을 대신하려는 게 아니라 손으로 적는 일을 줄이려는 것이다.
#
# aliases에 약관코드를 넣어둔다. 증권에 상품명 대신 약관코드가 찍히는 경우가 있어
# (현대해상 8403-0000-20250630), 그때 매칭되는 유일한 단서다.
def registry_stub(terms_id: str, cover: CoverInfo | None, args: argparse.Namespace) -> dict:
    code = cover.terms_code if cover else None
    return {
        "id": terms_id,
        "insurer": args.insurer or (cover.insurer if cover else None) or "보험사명",
        "product": args.product or (cover.product if cover else None) or "상품명",
        "aliases": [code] if code else [],
        "revision": cover.revision if cover else None,
        # 공시실 목록과 대조할 때 쓴다. 끝 8자리가 시행일이라, PDF를 받지 않고
        # 개정 여부를 판단하는 근거가 된다.
        "terms_code": code,
        # 이 약관을 받은 공시실 주소. 개정 확인 때 여기부터 열면 된다.
        "source_url": "",
        "collected_at": date.today().isoformat(),
    }


def ingest(pdf_path: Path, client, force: bool) -> dict:
    terms_id = pdf_path.stem
    chunks_path = CHUNKS_DIR / f"{terms_id}_chunks.json"
    embeddings_path = EMBEDDINGS_DIR / f"{terms_id}_embeddings.json"

    # 이미 있으면 건너뛴다. 파싱은 페이지 단위 과금이고 임베딩도 토큰 과금이라,
    # 무심코 전체를 다시 돌리면 돈이 나간다. 파싱 캐시가 있어 재파싱은 막히지만
    # 임베딩은 그대로 다시 청구된다.
    if not force and chunks_path.exists() and embeddings_path.exists():
        with chunks_path.open("r", encoding="utf-8") as f:
            existing = json.load(f)
        print(f"  이미 색인됨 (청크 {len(existing)}개). 다시 하려면 --force")
        # 지문 파일은 나중에 추가된 것이라 예전에 색인한 약관에는 없다.
        # 건너뛰는 경우에도 PDF가 있으면 채워준다(무료, 파싱을 부르지 않는다).
        if not meta_path(terms_id).exists() and pdf_path.exists():
            write_meta(terms_id, pdf_path)
        return {
            "terms_id": terms_id,
            "chunks": len(existing),
            "skipped": True,
            "cover": cover_for(pdf_path),
        }

    elements = parse_pdf(pdf_path)
    mapping = build_mapping_entries(load_json(DEFAULT_MAPPING_PATH))
    chunks = create_chunks_from_elements(elements, pdf_path.name, mapping)
    save_json(chunks, chunks_path)
    write_meta(terms_id, pdf_path)

    # embed_chunks_file은 이미 배치 처리·중간 저장·이어하기를 한다.
    # 여기서 다시 짜면 임베딩 텍스트 구성이 어긋날 위험이 있다. 구성이 다르면
    # 색인과 질의가 다른 공간에 놓여 검색이 조용히 나빠진다.
    EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)
    vectors = embed_chunks_file(chunks_path, embeddings_path, client, None)

    labeled = sum(1 for c in chunks if c.get("matched_category"))
    with_path = sum(1 for c in chunks if c.get("clause_path"))
    return {
        "terms_id": terms_id,
        "elements": len(elements),
        "chunks": len(chunks),
        "labeled": labeled,
        "with_path": with_path,
        "embeddings": len(vectors),
        "skipped": False,
        "cover": cover_for(pdf_path, elements),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="약관 색인 (파싱 -> 청킹 -> 임베딩 -> 저장)")
    parser.add_argument("--pdf", type=str, default=None, help="특정 파일만. 생략하면 raw_pdfs 전체")
    parser.add_argument("--force", action="store_true", help="이미 색인된 것도 다시 한다 (과금 발생)")
    parser.add_argument("--insurer", type=str, default=None, help="레지스트리에 없을 때 쓸 보험사명")
    parser.add_argument("--product", type=str, default=None, help="레지스트리에 없을 때 쓸 상품명")
    args = parser.parse_args()

    settings = get_settings()
    if settings.database_url:
        print(
            "경고: DATABASE_URL이 설정돼 있지만 이 스크립트는 파일 저장소에 넣습니다.\n"
            "      policy_chunks는 user_id/analysis_result_id가 NOT NULL이라 주인 없는\n"
            "      공유 약관을 넣을 수 없습니다. docs/BACKEND_INTERFACE.md 3-2 참고.\n"
        )

    pdfs = [RAW_PDF_DIR / args.pdf] if args.pdf else sorted(RAW_PDF_DIR.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"처리할 PDF가 없습니다: {RAW_PDF_DIR}")

    provider = get_provider(settings.embedding_provider)
    client = build_client(provider)
    print(f"임베딩 벤더: {provider.name} (문서 모델 {provider.doc_model})\n")

    results = []
    missing_registry = []
    for pdf in pdfs:
        if not pdf.exists():
            raise SystemExit(f"PDF를 찾을 수 없습니다: {pdf}")
        print(f"=== {pdf.name}")
        results.append(ingest(pdf, client, args.force))

        # 레지스트리에 없으면 검색은 되지만 증권에서 이 약관을 찾을 수 없다.
        # 색인해놓고 연결이 빠지는 것이 가장 흔한 실수라 여기서 짚어준다.
        if registered(pdf.stem) is None:
            missing_registry.append((pdf.stem, results[-1].get("cover")))
        print()

    print(f'{"약관":26} {"청크":>6} {"라벨":>10} {"특약명":>10} {"임베딩":>7}')
    print("-" * 64)
    for r in results:
        if r["skipped"]:
            print(f'{r["terms_id"][:26]:26} {r["chunks"]:>6}   (건너뜀)')
            continue
        print(
            f'{r["terms_id"][:26]:26} {r["chunks"]:>6} '
            f'{r["labeled"]:>5}({r["labeled"] / r["chunks"]:>4.0%}) '
            f'{r["with_path"]:>5}({r["with_path"] / r["chunks"]:>4.0%}) {r["embeddings"]:>7}'
        )

    if missing_registry:
        print("\n레지스트리에 없는 약관이 있습니다. 이대로면 증권에서 이 약관을 찾지 못합니다.")
        print("config/terms_registry.json 의 terms 배열에 아래를 추가하십시오.")
        print("표지에서 읽은 값이라 검토가 필요한 곳은 따로 표시했습니다.\n")
        for terms_id, cover in missing_registry:
            print(json.dumps(registry_stub(terms_id, cover, args), ensure_ascii=False, indent=2))
            if cover is None:
                print("  # 표지를 읽지 못했습니다(파싱 캐시 없음). 값을 직접 채우십시오.\n")
                continue
            # 사람이 --insurer/--product로 직접 준 필드는 확인 대상이 아니다.
            # 방금 지정한 값을 다시 확인하라고 하면 표시 자체를 안 믿게 된다.
            given = {"insurer": args.insurer, "product": args.product}
            review = [f for f in cover.needs_review if not given.get(f)]
            if review:
                print(f"  # 확인 필요: {', '.join(_FIELD_LABELS[f] for f in review)}")
            print("  # source_url 은 이 약관을 받은 공시실 주소를 붙여넣으십시오.\n")


if __name__ == "__main__":
    main()
