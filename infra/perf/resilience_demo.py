#!/usr/bin/env python3
"""
Stage 5 before/after: what shoppers experience when a dependency misbehaves.

    python3 infra/perf/resilience_demo.py --api http://localhost:8081 --label after

Scenarios (each resets the gateway, Redis and the breakers first):
  payment-slow   payment-mock answers after 8 s      -> 20 checkouts, 10 at a time
  payment-hang   payment-mock never answers           -> 20 checkouts, 10 at a time
  redis-frozen   Redis container paused (docker pause) -> 100 product-page views, 10 at a time

Writes docs/perf/stage5-<label>.json. Changes shared dev state briefly (the mock's mode, the
Redis container) and always restores it. Orders created are cancelled at the end.
"""
import argparse
import json
import statistics
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import kirana_auth  # AI Phase 5: bearer tokens instead of X-User-Id

MOCK_ADMIN = "http://localhost:8090/admin/mode"
REDIS_CONTAINER = "kirana-redis-1"


def call(api, method, path, body=None, user=None, timeout=60, key=None):
    headers = {"Content-Type": "application/json", **kirana_auth.headers(api, user)}
    if key:
        headers["Idempotency-Key"] = key  # Stage 7: required on cart adds, checkout, pay, cancel
    req = urllib.request.Request(api + path, method=method, headers=headers,
                                 data=None if body is None else json.dumps(body).encode())
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read() or "null")
            return r.status, (time.perf_counter() - start) * 1000, data
    except urllib.error.HTTPError as e:
        return e.code, (time.perf_counter() - start) * 1000, json.loads(e.read() or "null")
    except Exception as e:
        return 0, (time.perf_counter() - start) * 1000, str(e)


def mock_mode(mode, **extra):
    body = json.dumps({"mode": mode, **extra}).encode()
    urllib.request.urlopen(urllib.request.Request(MOCK_ADMIN, data=body, method="POST",
                                                  headers={"Content-Type": "application/json"}), timeout=5).read()


def reset_breakers(api):
    for name in ["payment-mock", "redis", "minio"]:
        call(api, "POST", f"/system/breakers/{name}/reset")


def summary(latencies):
    s = sorted(latencies)
    pick = lambda q: round(s[min(len(s) - 1, int(q * len(s)))])
    return {"min_ms": round(s[0]), "p50_ms": pick(0.50), "p95_ms": pick(0.95), "max_ms": round(s[-1]),
            "mean_ms": round(statistics.mean(s)), "under_100ms": sum(1 for v in s if v < 100)}


def checkout_scenario(api, name, product, n=20, concurrency=10):
    buyers = []
    for i in range(n):
        u = kirana_auth.signup(api, f"R{i}", f"res-{name}-{time.time_ns()}-{i}@test.com")
        call(api, "POST", "/cart/items", {"productId": product, "quantity": 1}, user=u["id"], key=str(uuid.uuid4()))
        buyers.append(u["id"])
    with ThreadPoolExecutor(concurrency) as pool:
        results = list(pool.map(lambda uid: (uid, *call(api, "POST", "/orders", {"paymentProvider": "mock"}, user=uid,
                                                         key=str(uuid.uuid4()))), buyers))
    outcomes = {"payment_started": 0, "order_held_pay_later": 0, "checkout_busy_503": 0, "error": 0}
    for uid, status, ms, data in results:
        if status == 201 and data.get("payment"):
            outcomes["payment_started"] += 1
        elif status == 201:
            outcomes["order_held_pay_later"] += 1
        elif status == 503:
            outcomes["checkout_busy_503"] += 1
        else:
            outcomes["error"] += 1
        if status == 201:
            call(api, "POST", f"/orders/{data['order']['id']}/cancel", user=uid, key=str(uuid.uuid4()))  # give the stock back
    return {"scenario": name, "requests": n, "concurrency": concurrency, **summary([r[2] for r in results]), "outcomes": outcomes}


def redis_scenario(api, product, n=100, concurrency=10):
    call(api, "GET", f"/products/{product}")  # make sure it is cached before Redis freezes
    subprocess.run(["docker", "pause", REDIS_CONTAINER], check=True, capture_output=True)
    try:
        with ThreadPoolExecutor(concurrency) as pool:
            results = list(pool.map(lambda _: call(api, "GET", f"/products/{product}"), range(n)))
    finally:
        subprocess.run(["docker", "unpause", REDIS_CONTAINER], check=True, capture_output=True)
    ok = sum(1 for r in results if r[0] == 200)
    return {"scenario": "redis-frozen", "requests": n, "concurrency": concurrency, **summary([r[1] for r in results]),
            "outcomes": {"ok_200": ok, "error": n - ok}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8081")
    ap.add_argument("--label", required=True)
    args = ap.parse_args()
    api = args.api

    _, _, p = call(api, "POST", "/products", {"name": f"Resilience tea {args.label}", "price": "50"})
    product = p["id"]
    call(api, "PUT", f"/products/{product}/inventory", {"quantity": 1000})
    _, _, status = call(api, "GET", "/system/status")

    results = []
    try:
        for name, mode, extra in [("payment-slow", "slow", {"delay_ms": 8000}), ("payment-hang", "hang", {})]:
            mock_mode("normal")
            reset_breakers(api)
            mock_mode(mode, **extra)
            print(f"{args.label}: {name} ...", flush=True)
            results.append(checkout_scenario(api, name, product))
        mock_mode("normal")
        reset_breakers(api)
        print(f"{args.label}: redis-frozen ...", flush=True)
        results.append(redis_scenario(api, product))
    finally:
        mock_mode("normal")
        subprocess.run(["docker", "unpause", REDIS_CONTAINER], capture_output=True)
        reset_breakers(api)
        call(api, "DELETE", f"/products/{product}")

    out = {"label": args.label, "resilience_enabled": status.get("resilienceEnabled"),
           "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "results": results}
    path = Path(__file__).resolve().parents[2] / "docs" / "perf" / f"stage5-{args.label}.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {path}")
    for r in results:
        print(f"  {r['scenario']:<14} min {r['min_ms']:>6}  p50 {r['p50_ms']:>6}  p95 {r['p95_ms']:>6}  max {r['max_ms']:>6} ms"
              f"  mean {r['mean_ms']:>6} ms  under 100 ms: {r['under_100ms']}/{r['requests']}  {r['outcomes']}")


if __name__ == "__main__":
    main()
