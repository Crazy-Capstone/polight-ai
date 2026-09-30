"""사용자 테스트 시나리오로 챗봇 정확도를 측정한다.

data/eval/scenario_gold.json의 16문항을 챗봇에 던져 답을 받고, 유형별로 채점한다.
성능 개선 전/후를 같은 평가셋으로 비교하기 위한 도구다.

채점 방식:
  --judge <모델> 지정 시: 전 문항을 LLM이 정답(gold) 기준으로 채점한다. 자연어 답변의
      표현 차이를 흡수한다("공항 도착 후 바로" == "나서기 전"). 정답이 고정돼 재량이 제한된다.
      편향이 걱정되면 답변과 다른 모델을 judge로 지정한다.
  미지정 시: 규칙 기반 키워드 매칭(빠르지만 표현차에 약함, 명백한 객관식용).

증권 가입정보(coverages)를 넣는 조건과 빼는 조건을 나눠 측정할 수 있다.
  --no-coverages : 증권 정보 없이(지금 백엔드 미전송 상태 재현)
  기본           : coverages 주입(백엔드가 보낸다고 가정한 AI 잠재력)

실행(AI 컨테이너 안, DATABASE_URL 필요):
    docker exec polight-ai python scripts/eval_scenarios.py --out data/eval/before.json --note "개선 전"
    docker exec polight-ai python scripts/eval_scenarios.py --judge openai-41 --out data/eval/after.json --note "개선 후"
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.repositories import get_vector_repository  # noqa: E402
from app.schemas.rag import RagQueryRequest  # noqa: E402
from app.services.answer_providers import generate  # noqa: E402
from app.services.rag_service import answer_question  # noqa: E402

GOLD_PATH = PROJECT_ROOT / "data" / "eval" / "scenario_gold.json"

# 시연 증권(메리츠 플랫폼)의 terms_id. 백엔드 termsId 전송을 재현한다.
TERMS_ID = "684fc6ba-ad8f-46bc-9789-7c49d5f12fae"

# 시연 증권의 가입담보(coverages). 백엔드가 보낸다고 가정하고 주입한다.
# name/limit/conditions는 실서버 coverage_items에서 확인한 값 형태를 따른다.
DEMO_COVERAGES = [
    {"name": "휴대품손해(분실제외)", "subscribed": True, "limitAmount": 500000,
     "limitCurrency": "KRW", "conditions": "* 자기부담금 10,000 * 물품당 최대 20만원 한도"},
    {"name": "해외여행중 배상책임(일괄배상)", "subscribed": True, "limitAmount": 10000000,
     "limitCurrency": "KRW", "conditions": "* 자기부담금 10,000"},
    {"name": "해외발생의료실비_상해", "subscribed": True, "limitAmount": 30000000,
     "limitCurrency": "KRW", "conditions": "Deductible $0.00"},
    {"name": "해외발생의료실비_질병", "subscribed": True, "limitAmount": 30000000,
     "limitCurrency": "KRW", "conditions": "Deductible $0.00"},
    {"name": "항공기 및 수하물 지연비용", "subscribed": True, "limitAmount": 200000,
     "limitCurrency": "KRW", "conditions": None},
    {"name": "해외여행중상해_사망", "subscribed": True, "limitAmount": 100000000,
     "limitCurrency": "KRW", "conditions": None},
    {"name": "여권분실후 재발급비용", "subscribed": True, "limitAmount": 600000,
     "limitCurrency": "KRW", "conditions": None},
    {"name": "해외여행중식중독비용", "subscribed": True, "limitAmount": 200000,
     "limitCurrency": "KRW", "conditions": None},
]


def _norm(text: str) -> str:
    """채점용 정규화: 공백·기호 제거, 소문자."""
    return re.sub(r"[\s·,()\[\]/%\-]", "", (text or "")).lower()


def _has(answer: str, keyword: str) -> bool:
    """답변에 키워드가 포함되는가.

    키워드가 한 덩어리면 정규화 후 부분일치로 본다. 여러 단어로 된 키워드
    ("4시간 이상 지연")는 답변에서 단어가 흩어져 나오는 일이 흔하므로
    (예: "지연이 4시간 이상"), 구성 단어가 모두 있으면 포함으로 인정한다.
    표현 차이로 정답을 놓치는 것을 막되, 무관한 답을 정답으로 인정하지는 않는다.
    """
    na = _norm(answer)
    if _norm(keyword) in na:
        return True
    words = [w for w in keyword.split() if len(w) >= 2]
    return len(words) >= 2 and all(_norm(w) in na for w in words)


def score_single_select(answer: str, item: dict) -> dict:
    """단일선택: 정답 선택지의 핵심이 답에 있고, 오답 선택지가 두드러지지 않으면 정답."""
    gold = item["gold"]
    kws = item.get("scoring_keywords") or [gold]
    hit = any(_has(answer, k) for k in (kws if isinstance(kws, list) else [gold]))
    return {"score": 1.0 if hit else 0.0, "detail": f"정답='{gold}' {'포함' if hit else '누락'}"}


def score_boolean_map(answer: str, item: dict) -> dict:
    """항목별 O/X: 각 항목의 판정 키워드가 맞는 방향으로 언급됐는가."""
    gold = item["gold"]
    kw_map = item.get("scoring_keywords", {})
    per, correct = {}, 0
    for key, verdict in gold.items():
        kws = kw_map.get(key, [])
        hit = any(_has(answer, k) for k in kws)
        per[key] = "✓" if hit else "✗"
        if hit:
            correct += 1
    n = len(gold) or 1
    return {"score": correct / n, "detail": per}


def score_multi_select(answer: str, item: dict) -> dict:
    """다중선택: 필수 항목 재현율.

    scoring_keywords는 required 항목별 동의어 맵({항목: [동의어...]})이거나
    평면 리스트일 수 있다. 맵이면 항목별 동의어로, 리스트면 항목명 자체 + 리스트
    전체를 후보로 써서 표현 차이를 흡수한다.
    """
    gold = item["gold"]
    required = gold.get("required", [])
    kw = item.get("scoring_keywords", {})
    found = []
    for r in required:
        if isinstance(kw, dict):
            candidates = [r, *kw.get(r, [])]
        else:  # 평면 리스트: 항목명 + 리스트 전체를 후보로
            candidates = [r, *kw]
        if any(_has(answer, c) for c in candidates):
            found.append(r)
    recall = len(found) / (len(required) or 1)
    missing = [r for r in required if r not in found]
    return {"score": recall, "detail": {"found": found, "missing": missing}}


def score_numeric(answer: str, item: dict) -> dict:
    """수치: 답에서 숫자를 뽑아 허용범위 안에 정답이 있는가."""
    gold = item["gold"]
    lo, hi = gold["tolerance_min"], gold["tolerance_max"]
    # "39만원", "390,000", "390000" 등에서 원 단위 숫자 추출
    nums = []
    for m in re.finditer(r"([\d,]+)\s*만", answer):
        nums.append(int(m.group(1).replace(",", "")) * 10000)
    for m in re.finditer(r"([\d,]{4,})\s*원", answer):
        nums.append(int(m.group(1).replace(",", "")))
    hit = any(lo <= n <= hi for n in nums)
    return {"score": 1.0 if hit else 0.0,
            "detail": f"정답 {gold['gold_value']:,}(±범위 {lo:,}~{hi:,}) / 답에서 추출 {nums}"}


def score_free_text(answer: str, item: dict, judge_model: str | None) -> dict:
    """서술형: 핵심 요소 포함 규칙 채점 + (옵션) LLM judge 보조."""
    core = item["gold"].get("core_elements", [])
    found = [c for c in core if any(_has(answer, w) for w in c.split())]
    rule_score = len(found) / (len(core) or 1)
    result = {"score": rule_score, "detail": {"core_found": len(found), "core_total": len(core)}}

    if judge_model:
        prompt = (
            f"다음은 여행자보험 챗봇의 '{item['question']}'에 대한 답변이다.\n"
            f"정답이 담아야 할 핵심 요소: {core}\n\n답변:\n{answer}\n\n"
            "이 답변이 핵심 요소를 정확히 담았는지 0.0~1.0 사이 점수로만 평가하라. "
            "숫자만 출력하라(예: 0.8)."
        )
        try:
            raw, _ = generate("너는 채점자다. 숫자만 답한다.", prompt, provider_name=judge_model)
            m = re.search(r"[01](?:\.\d+)?", raw)
            if m:
                result["judge_score"] = float(m.group())
        except Exception as e:
            result["judge_error"] = str(e)[:80]
    return result


SCORERS = {
    "single_select": lambda a, it, j: score_single_select(a, it),
    "boolean_map": lambda a, it, j: score_boolean_map(a, it),
    "multi_select": lambda a, it, j: score_multi_select(a, it),
    "numeric": lambda a, it, j: score_numeric(a, it),
    "free_text": lambda a, it, j: score_free_text(a, it, j),
}


# ── LLM 채점 ─────────────────────────────────────────────
#
# 규칙 기반 키워드 매칭은 자연어 답변의 표현 차이("공항 도착 후 바로" vs "나서기 전")를
# 못 잡아, 키워드를 답변에 맞춰 늘리면 오버피팅이 된다. 대신 정답(gold)을 주고 LLM이
# "정답 개념을 맞게 답했는지"를 판정한다. 정답이 고정돼 있어 채점자 재량이 제한된다.
def _gold_brief(item: dict) -> str:
    """채점 프롬프트에 넣을 정답 설명."""
    g = item["gold"]
    t = item["type"]
    if t == "single_select":
        return f"정답 선택지: {g}"
    if t == "boolean_map":
        return "각 항목 보상여부(O=보상/X=미보상): " + ", ".join(f"{k}={v}" for k, v in g.items())
    if t == "multi_select":
        return f"반드시 포함할 항목: {g.get('required')} / 포함하면 가점: {g.get('optional')}"
    if t == "numeric":
        return f"정답 금액: {g['gold_value']:,}원 (허용 {g['tolerance_min']:,}~{g['tolerance_max']:,}). 계산: {g.get('formula','')}"
    if t == "free_text":
        return f"답이 담아야 할 핵심 요소: {g.get('core_elements')}"
    return str(g)


def score_llm(answer: str, item: dict, judge_model: str) -> dict:
    """LLM이 정답 기준으로 0.0~1.0 채점. 부분 정답은 부분 점수."""
    note = item.get("note", "")
    prompt = (
        f"여행자보험 챗봇 답변을 채점한다.\n\n"
        f"[질문] {item['question']}\n"
        f"[정답 기준] {_gold_brief(item)}\n"
        + (f"[채점 참고] {note}\n" if note else "")
        + f"\n[챗봇 답변]\n{answer}\n\n"
        "채점 규칙:\n"
        "- 정답 개념을 맞게 담았으면 표현이 달라도 정답으로 인정한다.\n"
        "- 여러 항목이면 맞은 비율로 부분 점수를 준다.\n"
        "- 정답과 반대로 답했거나 핵심을 빠뜨리면 감점한다.\n"
        "- 약관에 없는 절차·상식 안내는 그 자체로 감점하지 않는다(정답이면 인정).\n\n"
        'JSON만 출력: {"score": 0.0~1.0, "reason": "한 줄 이유"}'
    )
    try:
        raw, _ = generate("너는 엄격하고 공정한 채점자다. JSON만 출력한다.", prompt,
                          provider_name=judge_model)
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        obj = json.loads(m.group()) if m else {}
        score = float(obj.get("score", 0.0))
        return {"score": max(0.0, min(1.0, score)), "detail": obj.get("reason", "")}
    except Exception as e:
        return {"score": 0.0, "detail": f"채점 실패: {str(e)[:80]}"}


def main() -> None:
    parser = argparse.ArgumentParser(description="시나리오 챗봇 정확도 측정")
    parser.add_argument("--out", default=None, help="결과 저장 경로(JSON)")
    parser.add_argument("--note", default="", help="이 측정의 메모(개선 전/후 등)")
    parser.add_argument("--no-coverages", action="store_true",
                        help="증권 정보를 넣지 않는다(백엔드 미전송 상태 재현)")
    parser.add_argument("--judge", default=None, help="LLM 채점 모델(예: openai-41). 지정하면 전 문항을 LLM이 채점(표현차 흡수). 없으면 규칙 기반.")
    parser.add_argument("--repeat", type=int, default=1, help="문항당 반복 횟수(분산 확인)")
    parser.add_argument("--rescore", default=None,
                        help="저장된 결과 파일의 답변을 챗봇 재호출 없이 현재 채점으로 다시 채점")
    args = parser.parse_args()

    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))

    # 재채점 모드: 저장된 답변에 현재 채점 로직만 다시 적용한다(챗봇 재호출 없음).
    # 답변을 고정한 채 채점 보정의 효과만 순수하게 비교할 때 쓴다.
    if args.rescore:
        prev = json.loads(Path(args.rescore).read_text(encoding="utf-8"))
        gold_by_id = {it["id"]: it for it in gold["items"]}
        results = []
        for r in prev["items"]:
            item = gold_by_id[r["id"]]
            scored = (score_llm(r["answer"], item, args.judge) if args.judge
                      else SCORERS[item["type"]](r["answer"], item, None))
            results.append({**r, "score": scored["score"], "detail": scored.get("detail"),
                            "judge_score": scored.get("judge_score", r.get("judge_score"))})
            print(f"  {r['id']:3} [{item['type']:13}] {scored['score']:.2f}")
        overall = sum(x["score"] for x in results) / len(results)
        by_type: dict[str, list] = {}
        for x in results:
            by_type.setdefault(x["type"], []).append(x["score"])
        print("\n" + "=" * 50)
        print(f"전체 정확도(재채점): {overall:.3f}")
        for t, s in sorted(by_type.items()):
            print(f"  {t:15}: {sum(s)/len(s):.3f}  ({len(s)}문항)")
        if args.out:
            Path(args.out).write_text(json.dumps(
                {"note": args.note + " (재채점)", "overall": overall,
                 "by_type": {t: sum(s)/len(s) for t, s in by_type.items()}, "items": results},
                ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\n저장: {args.out}")
        return
    repo = get_vector_repository()
    coverages = [] if args.no_coverages else DEMO_COVERAGES

    results = []
    print(f"측정 시작 (coverages={'없음' if args.no_coverages else '주입'}, "
          f"judge={args.judge or '규칙만'}, repeat={args.repeat})\n")

    for item in gold["items"]:
        runs = []
        for _ in range(args.repeat):
            req = RagQueryRequest.model_validate({
                "userId": "eval", "tripId": "eval",
                "termsId": TERMS_ID,
                "question": item["chatbot_question"],
                "coverages": coverages,
                "coveragesComplete": bool(coverages),
            })
            t0 = time.monotonic()
            resp = answer_question(req, repository=repo)
            elapsed = time.monotonic() - t0
            scored = (score_llm(resp.answer, item, args.judge) if args.judge
                      else SCORERS[item["type"]](resp.answer, item, None))
            runs.append({"answer": resp.answer, "elapsed": elapsed, **scored})

        avg = sum(r["score"] for r in runs) / len(runs)
        best = max(runs, key=lambda r: r["score"])
        results.append({
            "id": item["id"], "type": item["type"], "question": item["question"],
            "score": avg, "elapsed": sum(r["elapsed"] for r in runs) / len(runs),
            "answer": best["answer"], "detail": best.get("detail"),
            "judge_score": best.get("judge_score"),
        })
        js = f" judge={best.get('judge_score')}" if best.get("judge_score") is not None else ""
        print(f"  {item['id']:3} [{item['type']:13}] {avg:.2f}{js}  ({best['elapsed']:.1f}s)")

    # 유형별·전체 요약
    overall = sum(r["score"] for r in results) / len(results)
    by_type: dict[str, list] = {}
    for r in results:
        by_type.setdefault(r["type"], []).append(r["score"])
    avg_time = sum(r["elapsed"] for r in results) / len(results)

    print("\n" + "=" * 50)
    print(f"전체 정확도: {overall:.3f}  (16문항 평균)")
    for t, scores in sorted(by_type.items()):
        print(f"  {t:15}: {sum(scores)/len(scores):.3f}  ({len(scores)}문항)")
    print(f"평균 응답시간: {avg_time:.1f}초")

    if args.out:
        out = {
            "note": args.note, "coverages": not args.no_coverages, "judge": args.judge,
            "repeat": args.repeat, "overall": overall,
            "by_type": {t: sum(s) / len(s) for t, s in by_type.items()},
            "avg_time": avg_time, "items": results,
        }
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n저장: {args.out}")


if __name__ == "__main__":
    main()
