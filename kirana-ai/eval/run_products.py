"""
Product search quality (Phase 4 M5): does a better ranker actually help?

    python -m eval.run_products                  # hybrid vs hybrid + reranker
    python -m eval.run_products --model BAAI/bge-reranker-base   # try another reranker (1 GB download)

Runs the production search (retrieve -> optional rerank -> live hydration and stock filter) for
each query in eval/products/golden.json and scores the top 5 against the products that count
as a good answer:
    hit@1      the first card is a good answer          (what the shopper sees first)
    recall@5   at least one good answer among the five
    prec@5     share of the five that are good          (how many cards are noise)
    MRR        how high the first good answer is
Embedding calls only (no LLM): cheap to run.
"""
import argparse
import json
from pathlib import Path

from kirana_ai import config, products

CASES = json.loads((Path(__file__).parent / "products" / "golden.json").read_text())["cases"]


def is_good(name: str, good: list[str]) -> bool:
    return any(name.lower().startswith(g.lower()) for g in good)


def score(use_reranker: bool) -> dict:
    hit1 = rec5 = prec = mrr = 0.0
    misses = []
    for c in CASES:
        names = [p["name"] for p in products.search(c["query"], use_reranker=use_reranker).products]
        marks = [is_good(n, c["good"]) for n in names]
        hit1 += bool(marks and marks[0])
        rec5 += any(marks)
        prec += sum(marks) / max(len(names), 1)
        mrr += next((1 / i for i, m in enumerate(marks, 1) if m), 0)
        if not (marks and marks[0]):
            misses.append((c["query"], names[0] if names else "(nothing)"))
    n = len(CASES)
    return {"hit@1": hit1 / n, "recall@5": rec5 / n, "prec@5": prec / n, "MRR": mrr / n, "misses": misses}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", help="reranker model to compare (default: config.RERANK_MODEL)")
    args = parser.parse_args()
    if args.model:
        config.RERANK_MODEL = args.model
        products._reranker.cache_clear()

    runs = {"hybrid": score(False), f"+ rerank ({config.RERANK_MODEL.split('/')[-1]})": score(True)}
    print(f"{len(CASES)} queries\n\n{'':36} {'hit@1':>6} {'recall@5':>9} {'prec@5':>7} {'MRR':>6}")
    for name, m in runs.items():
        print(f"{name:36} {m['hit@1']:6.0%} {m['recall@5']:9.0%} {m['prec@5']:7.0%} {m['MRR']:6.2f}")
    for name, m in runs.items():
        print(f"\nfirst card not a good answer ({name}):")
        for q, first in m["misses"]:
            print(f"   {q[:40]:40} -> {first}")


if __name__ == "__main__":
    main()
