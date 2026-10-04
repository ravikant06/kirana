"""
Pick the relevance floor from data (Phase 3 M2).

    python -m eval.run_floor

For each question, run the production retrieval and take the best chunk's dense cosine
similarity. Answerable questions (golden/policy_questions.json) should land above the
floor; unanswerable ones (answers/unanswerable.json) below it. The sweep prints, for each
candidate floor, how many of each are handled correctly, and suggests the floor with the
best balance, preferring to keep answerable questions when two floors tie.
"""
import json
from pathlib import Path

from kirana_ai import config, embeddings, filters, sparse, vector_store

GOLDEN = Path(__file__).parent / "golden"
ANSWERS = Path(__file__).parent / "answers"


def best_similarity(query: str) -> float:
    client = vector_store.get_client()
    hits = vector_store.search(client, embeddings.embed_text(query), config.TOP_K,
                               query_filter=filters.build_filter(),
                               sparse_vector=sparse.encode_query(query) if config.HYBRID_SEARCH else None)
    return max((h["similarity"] for h in hits), default=0.0)


def main() -> None:
    answerable = [c["query"] for c in json.loads((GOLDEN / "policy_questions.json").read_text())["cases"]]
    unanswerable = [c["query"] for c in json.loads((ANSWERS / "unanswerable.json").read_text())["cases"]]
    a = sorted((best_similarity(q), q) for q in answerable)
    u = sorted((best_similarity(q), q) for q in unanswerable)

    print(f"best-chunk similarity  answerable n={len(a)}: {a[0][0]:.3f} .. {a[-1][0]:.3f}"
          f"   unanswerable n={len(u)}: {u[0][0]:.3f} .. {u[-1][0]:.3f}\n")
    print("  floor   answerable kept   unanswerable rejected   balanced")
    best = None
    for step in range(40, 81):
        floor = step / 100
        kept = sum(s >= floor for s, _ in a) / len(a)
        rejected = sum(s < floor for s, _ in u) / len(u)
        balanced = (kept + rejected) / 2
        if best is None or balanced > best[1] or (balanced == best[1] and kept > best[2]):
            best = (floor, balanced, kept, rejected)
        if step % 2 == 0:
            print(f"  {floor:.2f}   {kept:15.0%}   {rejected:21.0%}   {balanced:8.0%}")
    floor, balanced, kept, rejected = best
    print(f"\nsuggested RELEVANCE_FLOOR={floor:.2f}: keeps {kept:.0%} of answerable, "
          f"rejects {rejected:.0%} of unanswerable")
    print("\nclosest calls (the questions a floor near there decides):")
    for s, q in [x for x in a if x[0] < floor + 0.05][:4]:
        print(f"  answerable   {s:.3f}  {q}")
    for s, q in [x for x in u if x[0] > floor - 0.05][-4:]:
        print(f"  unanswerable {s:.3f}  {q}")


if __name__ == "__main__":
    main()
