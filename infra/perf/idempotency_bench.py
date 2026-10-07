#!/usr/bin/env python3
"""
Stage 7d: what an Idempotency-Key costs, Postgres store vs Redis store.

    python3 infra/perf/idempotency_bench.py --api http://localhost:8080 --label postgres
    (restart the backend with IDEMPOTENCY_STORE=redis)
    python3 infra/perf/idempotency_bench.py --api http://localhost:8080 --label redis

POST /cart/items, sequentially:
  new      300 adds, each with a fresh key (claim + work + complete)
  replay   300 repeats of already-completed keys (claim finds COMPLETED; no work)
Spread over 150 shoppers to stay under the cart rate limit (20 writes per 10 s each). Writes docs/perf/stage7-<label>.json.
"""
import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import kirana_auth  # AI Phase 5: bearer tokens instead of X-User-Id


def call(api, method, path, body=None, user=None, key=None):
    headers = {"Content-Type": "application/json", **kirana_auth.headers(api, user)}
    if key:
        headers["Idempotency-Key"] = key
    req = urllib.request.Request(api + path, method=method, headers=headers,
                                 data=None if body is None else json.dumps(body).encode())
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
            return r.status, (time.perf_counter() - start) * 1000, int(r.headers.get("X-Query-Count", -1))
    except urllib.error.HTTPError as e:
        return e.code, (time.perf_counter() - start) * 1000, int(e.headers.get("X-Query-Count", -1))


def summary(rows):
    ms = sorted(r[1] for r in rows)
    pick = lambda q: round(ms[min(len(ms) - 1, int(q * len(ms)))], 2)
    return {"requests": len(rows), "ok": sum(1 for r in rows if r[0] == 200),
            "p50_ms": pick(0.5), "p95_ms": pick(0.95), "mean_ms": round(statistics.mean(ms), 2),
            "sql_statements": statistics.median(r[2] for r in rows)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8080")
    ap.add_argument("--label", required=True)
    ap.add_argument("-n", type=int, default=300)
    args = ap.parse_args()
    api = args.api

    _, p, _ = None, None, None
    req = urllib.request.Request(api + "/products", method="POST", headers={"Content-Type": "application/json", **kirana_auth.headers(api)},
                                 data=json.dumps({"name": f"Bench tea {time.time_ns()}", "price": "10"}).encode())
    product = json.loads(urllib.request.urlopen(req).read())["id"]
    call(api, "PUT", f"/products/{product}/inventory", {"quantity": 100})
    shoppers = []
    for i in range(150):
        shoppers.append(kirana_auth.signup(api, f"B{i}", f"bench-{time.time_ns()}-{i}@t.com")["id"])

    for _ in range(10):  # warm up
        call(api, "POST", "/cart/items", {"productId": product, "quantity": 1}, shoppers[0], str(uuid.uuid4()))
        call(api, "DELETE", f"/cart/items/{product}", user=shoppers[0])

    new, replay, done = [], [], []
    for i in range(args.n):
        user, key = shoppers[i % len(shoppers)], str(uuid.uuid4())
        new.append(call(api, "POST", "/cart/items", {"productId": product, "quantity": 1}, user, key))
        call(api, "DELETE", f"/cart/items/{product}", user=user)  # stay under the per-product limit
        done.append((user, key))
    for user, key in done:
        replay.append(call(api, "POST", "/cart/items", {"productId": product, "quantity": 1}, user, key))

    out = {"label": args.label, "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "new_key": summary(new), "replay": summary(replay)}
    path = Path(__file__).resolve().parents[2] / "docs" / "perf" / f"stage7-{args.label}.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {path}")
    for name in ("new_key", "replay"):
        s = out[name]
        print(f"  {args.label:<9} {name:<8} p50 {s['p50_ms']:>6} ms  p95 {s['p95_ms']:>6} ms  mean {s['mean_ms']:>6} ms"
              f"  SQL {s['sql_statements']}  ok {s['ok']}/{s['requests']}")


if __name__ == "__main__":
    main()
