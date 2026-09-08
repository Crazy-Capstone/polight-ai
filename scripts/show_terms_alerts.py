"""약관 대조 기록을 모아 "확인해야 할 약관" 목록으로 보여준다.

증권이 올라올 때마다 terms_watch가 한 줄씩 남긴 것을 집계한다. 같은 상품에 여러
사용자가 걸리므로 건수가 곧 우선순위다 - 열 명이 걸린 약관을 먼저 받는 게 맞다.

    docker exec polight-ai python scripts/show_terms_alerts.py
    docker exec polight-ai python scripts/show_terms_alerts.py --days 30
    docker exec polight-ai python scripts/show_terms_alerts.py --all   # OK/UNKNOWN까지

판정별로 할 일이 다르다.

    MISSING_REVISION 가입 시점 판이 없다. 확정이라 가장 먼저 본다 - 챗봇이 지금
                     다른 판으로 답하고 있다는 뜻이다
    STALE            보유 최신판이 가입일보다 1년 이상 오래됐다. source_url을 열어
                     공시실 목록의 시행일만 확인한다. 같으면 끝이고 다르면 그때 받는다
    MISSING_PRODUCT  그 회사 약관은 있으니 상품 하나만 받는다
    MISSING_INSURER  회사 단위 수집 대상이다. 급하지 않으면 사용자 업로드로 받는다
    NO_TERMS_ID      약관을 못 찾아 근거 없이 답한 질의 수. 어느 약관인지는 알 수 없고
                     빈도만 의미가 있다. 이 값이 크면 수집이 뒤처졌다는 뜻이다
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.terms_watch import ACTIONABLE, ALERTS_PATH, NO_TERMS_ID  # noqa: E402


def _load(days: int | None) -> list[dict]:
    if not ALERTS_PATH.exists():
        return []

    cutoff = None
    if days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    records = []
    with ALERTS_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                # 쓰다가 죽어 잘린 줄이 있을 수 있다. 한 줄 때문에 전체를 못 보면 안 된다.
                continue
            if cutoff:
                try:
                    if datetime.fromisoformat(record["at"]) < cutoff:
                        continue
                except (KeyError, ValueError):
                    pass
            records.append(record)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="약관 대조 기록 집계")
    parser.add_argument("--days", type=int, default=None, help="최근 N일만 (기본: 전체)")
    parser.add_argument("--all", action="store_true", help="OK/UNKNOWN 판정까지 보여준다")
    args = parser.parse_args()

    records = _load(args.days)
    if not records:
        print(f"기록이 없습니다: {ALERTS_PATH}")
        print("증권이 분석되면 쌓입니다. DATABASE_URL이 없으면 대조를 건너뜁니다.")
        return

    period = f"최근 {args.days}일" if args.days else "전체 기간"
    print(f"약관 대조 기록 — {period}, {len(records)}건\n")

    no_terms = sum(1 for r in records if r.get("verdict") == NO_TERMS_ID)
    if no_terms:
        print(f"근거 없이 답한 챗봇 질의: {no_terms}건")
        print("  약관을 못 찾아 '확인할 수 없습니다'로 답한 횟수다.")
        print("  어느 약관이 필요한지는 요청에 없어 알 수 없다.\n")

    wanted = None if args.all else ACTIONABLE
    counts: Counter[tuple[str, str, str]] = Counter()
    reasons: dict[tuple[str, str, str], str] = {}
    held: dict[tuple[str, str, str], list] = {}

    for record in records:
        verdict = record.get("verdict")
        if verdict == NO_TERMS_ID:
            continue
        if wanted and verdict not in wanted:
            continue
        key = (verdict, record.get("insurer") or "?", record.get("product") or "?")
        counts[key] += 1
        reasons.setdefault(key, record.get("reason") or "")
        held.setdefault(key, record.get("held_revisions") or [])

    if not counts:
        print("확인이 필요한 약관은 없습니다.")
        return

    print(f'{"건수":>4} {"판정":16} {"보험사":14} 상품')
    print("-" * 78)

    # 심각도(ACTIONABLE 순서)가 먼저고, 같으면 건수가 많은 것부터.
    # 여러 사용자가 걸린 약관을 먼저 받는 게 맞지만, 틀린 판으로 답하고 있는
    # 1건이 근거 없이 답하는 10건보다 급하다.
    def order(item: tuple) -> tuple:
        (verdict, _, _), count = item
        severity = ACTIONABLE.index(verdict) if verdict in ACTIONABLE else len(ACTIONABLE)
        return (severity, -count)

    for (verdict, insurer, product), count in sorted(counts.items(), key=order):
        print(f"{count:>4} {verdict:16} {insurer:14} {product}")
        if reasons[(verdict, insurer, product)]:
            print(f"     └ {reasons[(verdict, insurer, product)]}")
        for row in held[(verdict, insurer, product)]:
            print(f"       보유: {row.get('product')} / 시행 {row.get('effective_date')}")

    print(
        "\n다음 할 일: config/terms_registry.json 의 source_url 을 열어 공시실 목록의\n"
        "시행일(약관코드 끝 8자리)만 확인하십시오. 우리가 가진 판과 같으면 받지 않아도 됩니다."
    )


if __name__ == "__main__":
    main()
