"""증권 분석 결과에서 개인정보를 지운다.

증권에는 피보험자 이름·생년월일·증권번호·연락처가 들어 있다. 이건 어댑터를 짜는 데도,
평가 정답을 만드는 데도 필요 없다. 필요한 것은 담보명·한도·가입여부뿐이다.

지우는 것과 남기는 것을 키 이름으로 가른다. 값을 보고 판단하면(예: 숫자면 지운다)
한도 금액까지 날아가고, 반대로 전부 남기면 이름이 그대로 따라온다.

사용법
    python scripts/redact_certificate.py 증권결과.json
    python scripts/redact_certificate.py 증권결과.json --out data/eval/cert_001.json

    # 어떤 키가 지워지는지 먼저 확인
    python scripts/redact_certificate.py 증권결과.json --dry-run

지운 뒤에는 반드시 눈으로 한 번 확인하십시오. 스튜디오가 어떤 키 이름을 쓰는지
모르는 상태라, 아래 목록에 없는 이름으로 개인정보가 들어 있을 수 있습니다.
--dry-run으로 남는 키를 훑어보는 것이 안전합니다.
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.certificate_privacy import MASK, is_sensitive, redact, scrub_text


def collect_keys(node, into: set, path: str = "") -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            into.add(here)
            collect_keys(value, into, here)
    elif isinstance(node, list) and node:
        collect_keys(node[0], into, f"{path}[]")


def main() -> None:
    parser = argparse.ArgumentParser(description="증권 분석 결과에서 개인정보 제거")
    parser.add_argument("source", type=str)
    parser.add_argument("--out", type=str, default=None)
    parser.add_argument("--dry-run", action="store_true", help="지울 키와 남는 키만 보여준다")
    args = parser.parse_args()

    source = Path(args.source)
    with source.open("r", encoding="utf-8") as f:
        data = json.load(f)

    removed: list[str] = []
    cleaned = redact(data, removed)

    print(f"지운 필드 {len(removed)}개")
    for path in removed[:40]:
        print(f"  - {path}")
    if len(removed) > 40:
        print(f"  ... 외 {len(removed) - 40}개")

    if args.dry_run:
        keys: set = set()
        collect_keys(cleaned, keys)
        print(f"\n남는 키 {len(keys)}개 (개인정보가 섞여 있지 않은지 확인하십시오)")
        for key in sorted(keys):
            print(f"  {key}")
        return

    out = Path(args.out) if args.out else source.with_name(f"{source.stem}_redacted.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)

    print(f"\n저장: {out}")
    print("공유하기 전에 파일을 직접 열어 확인하십시오. 키 이름을 기준으로 지우므로")
    print("예상 못 한 이름의 필드는 그대로 남아 있을 수 있습니다.")


if __name__ == "__main__":
    main()
