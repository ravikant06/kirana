"""
Answer-quality eval (Phase 3 M3): does the assistant say the right thing, and only what it can support?

    python -m eval.run_answers                    # run every case, compare with the baseline
    python -m eval.run_answers --limit 5          # a quick subset
    python -m eval.run_answers --save-baseline    # make this run the new baseline
    python -m eval.run_answers --grade 20         # you grade the last run; agreement with the judge

Each case runs the real agent (retrieval + Gemini), then the LLM judge (eval/judge.py).
That is ~3 LLM calls per case: a full run costs a few tens of cents, so it is a deliberate
command, not a unit test.

Metrics:
  answerable    correct        states the expected facts
                false refusal  said "couldn't find it" although the documents answer it
  unanswerable  abstained      rightly said it couldn't find it
                hallucinated   answered anyway (the failure that matters most)
  all           faithful       every claim supported by the retrieved passages

One run of an LLM is one sample: run twice and numbers move. Treat a few points of
difference as noise; Phase 9 adds repeated runs and variance.
"""
import argparse
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from eval.judge import judge
from kirana_ai import agent, config
from kirana_ai.llm import get_adapter
from kirana_ai.usage import cost_of, load_prices

HERE = Path(__file__).parent
CASES = HERE / "answers"
RESULTS = HERE / "results"
BASELINE = HERE / "baselines" / "answers.json"


def load_cases() -> list[dict]:
    cases = []
    for name in ("answerable", "unanswerable"):
        for c in json.loads((CASES / f"{name}.json").read_text())["cases"]:
            cases.append({**c, "family": name})
    return cases


def run_case(case: dict) -> dict:
    llm = get_adapter()
    calls = []
    llm.add_listener(calls.append)
    started = time.perf_counter()
    chunks, answer, steps = agent.answer(case["query"], llm=llm)
    elapsed = time.perf_counter() - started
    prices = load_prices()
    costs = [cost_of(r, prices) for r in calls]
    verdict = judge(case["query"], chunks, answer, case.get("expected"))
    return {
        "id": case["id"], "family": case["family"], "query": case["query"],
        "expected": case.get("expected"), "table": case.get("table", False),
        "answer": answer, "sources": sorted({c["source"] for c in chunks}),
        "steps": steps, "verdict": verdict,
        "llm_calls": len(calls), "seconds": round(elapsed, 2),
        "cost_usd": None if None in costs else float(sum(costs, Decimal(0))),
    }


def metrics(results: list[dict]) -> dict:
    ans = [r for r in results if r["family"] == "answerable"]
    una = [r for r in results if r["family"] == "unanswerable"]
    rate = lambda rows, pred: round(sum(1 for r in rows if pred(r["verdict"])) / len(rows), 3) if rows else None
    costs = [r["cost_usd"] for r in results if r["cost_usd"] is not None]
    return {
        "answerable_correct": rate(ans, lambda v: v["correct"] is True),
        "answerable_false_refusal": rate(ans, lambda v: v["answered"] is False),
        "table_correct": rate([r for r in ans if r["table"]], lambda v: v["correct"] is True),
        "unanswerable_abstained": rate(una, lambda v: v["answered"] is False),
        "unanswerable_hallucinated": rate(una, lambda v: v["answered"] is True),
        "faithful": rate(results, lambda v: v["faithful"] is True),
        "judge_unparseable": sum(1 for r in results if r["verdict"]["answered"] is None),
        "avg_seconds": round(sum(r["seconds"] for r in results) / len(results), 2),
        "avg_cost_usd": round(sum(costs) / len(costs), 5) if costs else None,
        "n": len(results),
    }


LABELS = {
    "answerable_correct": ("answerable: correct", True),
    "answerable_false_refusal": ("answerable: false refusal", False),
    "table_correct": ("  of which table rows: correct", True),
    "unanswerable_abstained": ("unanswerable: abstained", True),
    "unanswerable_hallucinated": ("unanswerable: hallucinated", False),
    "faithful": ("all: faithful to passages", True),
    "avg_seconds": ("avg seconds per question", False),
    "avg_cost_usd": ("avg cost per question (USD)", False),
}


def report(m: dict, baseline: dict | None) -> None:
    print(f"\n{'metric':34} {'now':>8} {'baseline':>9} {'change':>8}")
    for key, (label, higher_is_better) in LABELS.items():
        now, then = m.get(key), (baseline or {}).get(key)
        fmt = (lambda v: "—" if v is None else f"{v:.0%}") if "seconds" not in key and "cost" not in key \
            else (lambda v: "—" if v is None else f"{v:g}")
        change = ""
        if now is not None and then is not None and now != then:
            better = (now > then) == higher_is_better
            change = ("▲ " if better else "▼ ") + (f"{now - then:+.0%}" if fmt is not None and "%" in fmt(now) else f"{now - then:+g}")
        print(f"{label:34} {fmt(now):>8} {fmt(then):>9} {change:>8}")
    if m["judge_unparseable"]:
        print(f"\n{m['judge_unparseable']} judge verdict(s) could not be parsed (see the results file)")


def grade(limit: int) -> None:
    """You grade the last run's answers without seeing the judge, then see how often you agreed."""
    latest = sorted(RESULTS.glob("*-answers.json"))
    if not latest:
        raise SystemExit("No run to grade yet: run `python -m eval.run_answers` first.")
    run = json.loads(latest[-1].read_text())
    rows = run["results"][:limit]
    agree = 0
    grades = []
    print(f"Grading {len(rows)} answers from {latest[-1].name}. y = good, n = bad.\n")
    for i, r in enumerate(rows, 1):
        print(f"[{i}/{len(rows)}] {r['query']}")
        if r["expected"]:
            print(f"  expected: {r['expected']}")
            question = "  Is the answer correct? [y/n] "
        else:
            print("  expected: (not in the documents: it should say it couldn't find it)")
            question = "  Did it handle it correctly (declined, no invented facts)? [y/n] "
        print(f"  answer:   {r['answer'][:500]}")
        human = input(question).strip().lower().startswith("y")
        v = r["verdict"]
        judge_ok = v["correct"] is True if r["expected"] else v["answered"] is False
        agree += human == judge_ok
        grades.append({"id": r["id"], "human": human, "judge": judge_ok, "reason": v["reason"]})
        print(f"  judge said {'good' if judge_ok else 'bad'}: {v['reason']}\n")
    out = latest[-1].with_name(latest[-1].name.replace("-answers.json", "-grades.json"))
    out.write_text(json.dumps(grades, indent=2))
    print(f"Agreement: {agree}/{len(rows)} = {agree / len(rows):.0%}   (saved to eval/results/{out.name})")
    print("Below ~85%, trust the judge's numbers only as a rough signal, and look at where you disagreed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, help="run only the first N cases of each family")
    parser.add_argument("--save-baseline", action="store_true", help="store this run as the baseline")
    parser.add_argument("--grade", type=int, metavar="N", help="hand-grade N answers of the last run")
    args = parser.parse_args()
    if args.grade:
        grade(args.grade)
        return

    cases = load_cases()
    if args.limit:
        cases = [c for fam in ("answerable", "unanswerable") for c in [x for x in cases if x["family"] == fam][:args.limit]]
    print(f"{len(cases)} cases · {config.GENERATION_MODEL} · thinking={config.GEMINI_THINKING_LEVEL or 'default'}"
          f" · floor={config.RELEVANCE_FLOOR} · top_k={config.TOP_K}\n")
    results = []
    for c in cases:
        r = run_case(c)
        v = r["verdict"]
        mark = ("✓" if v["correct"] else "✗") if c["family"] == "answerable" else ("✓" if v["answered"] is False else "✗")
        print(f"  {mark} {c['id']} {c['query'][:58]:58} faithful={v['faithful']} {r['seconds']}s")
        results.append(r)

    m = metrics(results)
    baseline = json.loads(BASELINE.read_text())["metrics"] if BASELINE.exists() else None
    report(m, baseline)

    run = {"recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "config": {"model": config.GENERATION_MODEL, "thinking": config.GEMINI_THINKING_LEVEL,
                      "relevance_floor": config.RELEVANCE_FLOOR, "top_k": config.TOP_K,
                      "hybrid": config.HYBRID_SEARCH, "limit": args.limit},
           "metrics": m, "results": results}
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-answers.json"
    out.write_text(json.dumps(run, indent=2, ensure_ascii=False))
    print(f"\nsaved -> eval/results/{out.name}")
    if args.save_baseline:
        BASELINE.parent.mkdir(exist_ok=True)
        BASELINE.write_text(json.dumps({k: run[k] for k in ("recorded_at", "config", "metrics")}, indent=2))
        print("saved as the baseline -> eval/baselines/answers.json")


if __name__ == "__main__":
    main()
