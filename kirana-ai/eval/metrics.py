"""
Retrieval metrics, with confidence intervals.

The golden sets here are small (tens of queries). At that size a two-query
difference looks like a win and is usually noise, so every rate is reported
with a bootstrap interval rather than as a bare percentage.
"""
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class CaseResult:
    """Outcome for one query: the rank of the first relevant chunk, or None."""
    case_id: str
    query: str
    rank: int | None

    def hit_at(self, k: int) -> bool:
        return self.rank is not None and self.rank <= k


def recall_at(results: list[CaseResult], k: int) -> float:
    """Fraction of queries whose first relevant chunk landed in the top k."""
    if not results:
        return 0.0
    return sum(r.hit_at(k) for r in results) / len(results)


def mrr(results: list[CaseResult]) -> float:
    """Mean reciprocal rank — rewards putting the right chunk near the top."""
    if not results:
        return 0.0
    return sum((1 / r.rank) if r.rank else 0.0 for r in results) / len(results)


def bootstrap_ci(
    results: list[CaseResult],
    k: int,
    iterations: int = 2000,
    confidence: float = 0.95,
    seed: int = 0,
) -> tuple[float, float]:
    """
    Percentile bootstrap interval for recall@k.

    Resamples the query set with replacement to estimate how much the score
    would move on a different sample of queries of the same size.
    """
    if not results:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(results)
    samples = []
    for _ in range(iterations):
        draw = [results[rng.randrange(n)] for _ in range(n)]
        samples.append(sum(r.hit_at(k) for r in draw) / n)
    samples.sort()
    lo = samples[int((1 - confidence) / 2 * iterations)]
    hi = samples[int((1 + confidence) / 2 * iterations) - 1]
    return (lo, hi)


def summarise(results: list[CaseResult], ks: tuple[int, ...] = (1, 4, 10)) -> dict:
    out = {"n": len(results), "mrr": round(mrr(results), 4)}
    for k in ks:
        lo, hi = bootstrap_ci(results, k)
        out[f"recall@{k}"] = round(recall_at(results, k), 4)
        out[f"recall@{k}_ci95"] = [round(lo, 4), round(hi, 4)]
    out["misses"] = [
        {"case_id": r.case_id, "query": r.query, "rank": r.rank}
        for r in results
        if not r.hit_at(4)
    ]
    return out
