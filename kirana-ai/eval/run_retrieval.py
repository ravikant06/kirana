"""
Measure retrieval quality against the golden sets in eval/golden/.

Run:  python -m eval.run_retrieval                   # hybrid (production path)
      python -m eval.run_retrieval --compare         # dense vs hybrid, side by side
      python -m eval.run_retrieval --save            # write eval/results/<timestamp>.json

Retrieval only: did the right chunk reach the top k? Answer quality (does the
reply say the right thing, does it abstain) is added in Phase 3.

The retriever is a plain callable, so adding one later (a reranker) means
adding a function to RETRIEVERS, not editing the scoring code.
"""
import argparse
import json
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from eval.metrics import CaseResult, summarise
from kirana_ai import chunker, config, embeddings, filters, loader, sparse, vector_store

GOLDEN_DIR = Path(__file__).parent / "golden"
RESULTS_DIR = Path(__file__).parent / "results"

# A retriever takes a query and a depth, and returns chunk dicts in rank order.
Retriever = Callable[[str, int], list[dict]]


def dense_retriever(query: str, depth: int) -> list[dict]:
    """Dense only: embed the query, search the meaning vectors."""
    client = vector_store.get_client()
    return vector_store.search(client, embeddings.embed_text(query), depth,
                               query_filter=filters.build_filter())


def hybrid_retriever(query: str, depth: int) -> list[dict]:
    """Dense + BM25, fused by RRF inside Qdrant."""
    client = vector_store.get_client()
    return vector_store.search(client, embeddings.embed_text(query), depth,
                               query_filter=filters.build_filter(),
                               sparse_vector=sparse.encode_query(query))


RETRIEVERS: dict[str, Retriever] = {"dense": dense_retriever, "hybrid": hybrid_retriever}


def corpus_size() -> int:
    docs = loader.load_documents(config.KB_DIR)
    return len(chunker.chunk_documents(docs, config.CHUNK_SIZE, config.CHUNK_OVERLAP))


def evaluate(cases: list[dict], retrieve: Retriever, depth: int) -> list[CaseResult]:
    """Rank of the first chunk whose text contains the case's gold substring."""
    results = []
    for case in cases:
        gold = case["gold_substring"].lower()
        hits = retrieve(case["query"], depth)
        rank = next(
            (i for i, hit in enumerate(hits, 1) if gold in hit.get("text", "").lower()),
            None,
        )
        results.append(CaseResult(case_id=case["id"], query=case["query"], rank=rank))
    return results


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True,
            cwd=Path(__file__).parent.parent, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def report(name: str, stats: dict) -> None:
    print(f"── {name}  (n={stats['n']})")
    for k in (1, 4, 10):
        lo, hi = stats[f"recall@{k}_ci95"]
        print(f"   recall@{k:<2} {stats[f'recall@{k}']:.0%}   95% CI [{lo:.0%}, {hi:.0%}]")
    print(f"   MRR      {stats['mrr']:.3f}")
    if stats["misses"]:
        print(f"   misses at k=4 ({len(stats['misses'])}):")
        for miss in stats["misses"]:
            print(f"     rank {str(miss['rank']):>4}  {miss['query'][:66]}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrieval evaluation.")
    parser.add_argument("--depth", type=int, default=10,
                        help="how deep to score; must exceed the largest k (default: 10)")
    parser.add_argument("--retriever", choices=sorted(RETRIEVERS), default="hybrid")
    parser.add_argument("--compare", action="store_true",
                        help="score every retriever on the same cases")
    parser.add_argument("--save", action="store_true", help="write the run to eval/results/")
    args = parser.parse_args()

    names = sorted(RETRIEVERS) if args.compare else [args.retriever]
    n_chunks = corpus_size()
    print(f"corpus: {n_chunks} chunks   collection: {config.COLLECTION_NAME}   "
          f"production top_k={config.TOP_K}\n")

    run = {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "config": {
            "embedding_model": config.EMBEDDING_MODEL,
            "embedding_dim": config.EMBEDDING_DIM,
            "chunk_size": config.CHUNK_SIZE,
            "chunk_overlap": config.CHUNK_OVERLAP,
            "corpus_chunks": n_chunks,
        },
        "results": {},
    }
    for path in sorted(GOLDEN_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        for name in names:
            stats = summarise(evaluate(data["cases"], RETRIEVERS[name], args.depth))
            run["results"][f"{data['family']}/{name}"] = stats
            report(f"{data['family']} / {name}", stats)

    if args.save:
        RESULTS_DIR.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        out = RESULTS_DIR / f"{stamp}-{'-'.join(names)}.json"
        out.write_text(json.dumps(run, indent=2, ensure_ascii=False))
        print(f"saved -> eval/results/{out.name}")


if __name__ == "__main__":
    main()
