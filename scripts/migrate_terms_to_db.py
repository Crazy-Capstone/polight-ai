"""이미 색인된 공용 약관을 policy_terms 계열 테이블로 옮긴다.

data/chunks + data/embeddings(파일 저장소)에 있는 약관을 실서버 DB의
policy_terms / policy_terms_chunks 로 적재한다. 파싱·임베딩은 이미 파일에 있으므로
재사용한다(과금 없음).

이 스크립트는 청크만 옮긴다(챗봇 검색용). 보장 규칙(policy_terms_coverages,
보장 상세용)은 LLM 추출이 필요해 migrate_terms_coverages.py 로 따로 돌린다.

실행 위치: 실서버 DB는 RDS(VPC 안)라 로컬에서 못 붙는다. AI 컨테이너 안에서
DATABASE_URL(rag_service DSN)로 실행한다.

    docker exec polight-ai python scripts/migrate_terms_to_db.py            # 전체
    docker exec polight-ai python scripts/migrate_terms_to_db.py --terms hyundai_travel_2025
    docker exec polight-ai python scripts/migrate_terms_to_db.py --dry-run  # 넣지 않고 확인만

재실행은 안전하다. 같은 약관은 (보험사·상품·개정판)으로 기존 id를 찾아 DELETE 후
INSERT 한다. UPDATE 권한 없이 재적재하는 방식이다(PR #36 5절).

예시 약관을 실제 수집분으로 갈아엎을 때는 --reset을 쓴다. 재적재는 목록에 있는
약관만 대체하므로, 목록에서 빠진 약관은 DB에 남아 검색에 계속 걸린다.

    docker exec -it polight-ai python scripts/migrate_terms_to_db.py --reset
    docker exec polight-ai python scripts/migrate_terms_to_db.py --reset --dry-run

개정판은 revision이 달라 별개 행이 된다. 구판을 지우지 않는다 - 보험은 가입 시점의
약관이 적용되므로, 구판으로 가입한 사용자에게는 그 판으로 답해야 한다.
"""

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.repositories import terms_mapper  # noqa: E402
from app.repositories.terms_repository import TermsRepository  # noqa: E402

CHUNKS_DIR = PROJECT_ROOT / "data" / "chunks"
EMBEDDINGS_DIR = PROJECT_ROOT / "data" / "embeddings"
RAW_PDF_DIR = PROJECT_ROOT / "data" / "raw_pdfs"
REGISTRY_PATH = PROJECT_ROOT / "config" / "terms_registry.json"


def _registry() -> list[dict]:
    with REGISTRY_PATH.open(encoding="utf-8") as f:
        return json.load(f)["terms"]


def _parse_effective_date(revision: str | None) -> date | None:
    """revision이 YYYY-MM-DD 형태면 effective_date로. 아니면 None.

    개정 시점이 곧 시행일이다. db_travel 2건은 revision이 없어 None으로 둔다 -
    상품명이 달라 매칭이 끊기지 않는다(BACKEND_REPLY_4 2-4).
    """
    if not revision:
        return None
    try:
        return date.fromisoformat(revision)
    except ValueError:
        return None


# policy_terms.file_hash에 넣을 PDF 지문.
#
# 이 값이 있으면 개정판 확인이 "다시 받은 PDF가 같은 파일인가"로 끝난다. 없으면
# 매번 다시 색인해봐야 알 수 있고, 약관 1건 재색인은 4~5분 + 파싱 과금이다.
#
# 이 스크립트는 AI 컨테이너 안에서 도는데 그 서버에는 약관 PDF가 없다(증권이 섞여
# 들어갈 위험 때문에 chunks/embeddings만 옮긴다). 그래서 색인 때 남겨둔 meta 파일을
# 먼저 보고, 로컬처럼 PDF가 있으면 그때 직접 계산한다.
def _file_hash(stem: str) -> str | None:
    meta_path = CHUNKS_DIR / f"{stem}_meta.json"
    if meta_path.exists():
        with meta_path.open(encoding="utf-8") as f:
            digest = json.load(f).get("file_hash")
        if digest:
            return digest

    pdf_path = RAW_PDF_DIR / f"{stem}.pdf"
    if not pdf_path.exists():
        return None
    sha = hashlib.sha256()
    with pdf_path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


# 레지스트리에 있는 약관을 DB에서 지운다.
#
# 예시 약관으로 개발하다가 실제 수집분으로 통째로 갈아엎을 때 쓴다. 약관마다
# migrate를 다시 돌리면 그 약관은 DELETE 후 INSERT로 대체되지만, 목록에서 빠진
# 약관은 DB에 남아 검색에 계속 걸린다. 그것부터 지워야 깨끗한 상태가 된다.
#
# 자식 테이블 삭제 순서는 repo.delete_terms가 이미 FK에 맞춰 처리한다.
def reset(repo: TermsRepository, entries: list[dict], dry_run: bool) -> None:
    print("기존 약관을 지웁니다.")
    for entry in entries:
        terms_id = repo.find_verified_terms_id(
            entry["insurer"], entry["product"], entry.get("revision")
        )
        if terms_id is None:
            print(f"  {entry['id']:22} DB에 없음")
            continue
        if dry_run:
            print(f"  {entry['id']:22} {terms_id} (dry-run)")
            continue
        repo.delete_terms(terms_id)
        print(f"  {entry['id']:22} {terms_id} 삭제")
    print()


def migrate_one(repo: TermsRepository, entry: dict, dry_run: bool) -> dict:
    stem = entry["id"]
    insurer = entry["insurer"]
    product = entry["product"]
    revision = entry.get("revision")

    chunks_path = CHUNKS_DIR / f"{stem}_chunks.json"
    emb_path = EMBEDDINGS_DIR / f"{stem}_embeddings.json"
    if not chunks_path.exists() or not emb_path.exists():
        return {"terms": stem, "skipped": "청크/임베딩 없음"}

    chunks = json.load(chunks_path.open(encoding="utf-8"))
    embeddings = json.load(emb_path.open(encoding="utf-8"))

    # 재적재면 기존 id 재사용, 아니면 새로. 부분 유니크 위반 방지.
    terms_id = repo.find_verified_terms_id(insurer, product, revision) or uuid4()

    terms_row = terms_mapper.terms_row(
        terms_id, insurer, product, revision, _parse_effective_date(revision),
        file_hash=_file_hash(stem),
    )
    chunk_rows = terms_mapper.chunk_rows(terms_id, chunks, embeddings)
    embedded = sum(1 for r in chunk_rows if r[terms_mapper.CHUNK_COLUMNS.index("embedding")] is not None)

    hashed = terms_row["file_hash"] is not None

    if dry_run:
        return {
            "terms": stem, "terms_id": str(terms_id), "hashed": hashed,
            "chunks": len(chunk_rows), "embedded": embedded, "dry_run": True,
        }

    repo.save_terms(terms_row, chunk_rows, coverage_tree=None)
    return {
        "terms": stem, "terms_id": str(terms_id), "hashed": hashed,
        "chunks": len(chunk_rows), "embedded": embedded,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="공용 약관 -> policy_terms 계열 적재")
    parser.add_argument("--terms", default=None, help="특정 약관 id만 (기본: 전체)")
    parser.add_argument("--dry-run", action="store_true", help="넣지 않고 확인만")
    parser.add_argument(
        "--reset", action="store_true",
        help="적재 전에 대상 약관을 DB에서 지운다 (예시 약관 -> 실제 수집분 교체용)",
    )
    parser.add_argument("--yes", action="store_true", help="--reset 확인 프롬프트를 건너뛴다")
    args = parser.parse_args()

    dsn = get_settings().database_url
    if not dsn:
        raise SystemExit(
            "DATABASE_URL이 없습니다. 실서버 DB는 AI 컨테이너 안에서만 접속됩니다.\n"
            "  docker exec polight-ai python scripts/migrate_terms_to_db.py"
        )

    entries = _registry()
    if args.terms:
        entries = [e for e in entries if e["id"] == args.terms]
        if not entries:
            raise SystemExit(f"레지스트리에 없는 약관: {args.terms}")

    repo = TermsRepository(dsn)

    if args.reset:
        # 지우는 건 되돌릴 수 없다. 재적재는 안전하지만 reset은 다르므로 한 번 묻는다.
        if not args.dry_run and not args.yes:
            target = args.terms or f"레지스트리 전체({len(entries)}건)"
            if input(f"{target}을 DB에서 지웁니다. 계속하려면 yes: ").strip() != "yes":
                raise SystemExit("취소했습니다.")
        reset(repo, entries, args.dry_run)

    print(f"{'약관':22} {'청크':>6} {'임베딩':>6} {'지문':>4}  terms_id")
    print("-" * 84)
    no_hash = []
    for entry in entries:
        result = migrate_one(repo, entry, args.dry_run)
        if result.get("skipped"):
            print(f"{result['terms']:22} 건너뜀 - {result['skipped']}")
            continue
        tag = " (dry-run)" if result.get("dry_run") else ""
        mark = "O" if result["hashed"] else "-"
        if not result["hashed"]:
            no_hash.append(result["terms"])
        print(
            f"{result['terms']:22} {result['chunks']:>6} {result['embedded']:>6} "
            f"{mark:>4}  {result['terms_id']}{tag}"
        )

    if no_hash:
        # 지문이 없어도 검색은 정상이다. 다만 개정판 확인 때 PDF를 받아 비교할
        # 근거가 없어, 그 약관만 다시 색인해봐야 알 수 있게 된다.
        print(
            f"\n지문(file_hash)이 빈 약관 {len(no_hash)}건: {', '.join(no_hash)}\n"
            "  data/chunks/{id}_meta.json 이 없습니다. 로컬에서 ingest_terms.py를\n"
            "  다시 돌리면 meta가 생기고, 그 파일을 함께 옮기면 채워집니다."
        )

    print("\n완료." + (" (실제로는 넣지 않았습니다 - dry-run)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
