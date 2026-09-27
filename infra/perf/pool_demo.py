#!/usr/bin/env python3
"""
Stage 2f: a slow dependency exhausts the DB connection pool (P5, P6).

    python3 infra/perf/pool_demo.py --api http://localhost:8080

1. Creates a demo product and asks for N upload tickets (MinIO must be up for this).
2. Freezes MinIO with `docker pause`, so every call to it hangs.
3. Sends N image confirms at once. Each one calls MinIO's statObject and hangs.
4. While they hang, times GET /products, which never touches MinIO.
5. Unpauses MinIO and waits for the confirms to finish.

If confirm holds a DB connection while it waits on MinIO, N confirms (N > pool size)
take every connection and GET /products fails too. If it doesn't, the product list is fine.
"""
import argparse
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import Counter

MINIO = "kirana-minio-1"


def call(api, method, path, body=None, timeout=60):
    req = urllib.request.Request(api + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            data = res.read()
            return res.status, (time.perf_counter() - start) * 1000, json.loads(data or "null")
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            body = json.loads(body)
        except ValueError:
            pass
        return e.code, (time.perf_counter() - start) * 1000, body
    except Exception as e:  # timeouts
        return 0, (time.perf_counter() - start) * 1000, str(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8080")
    ap.add_argument("--confirms", type=int, default=15)
    args = ap.parse_args()

    _, _, product = call(args.api, "POST", "/products", {"name": "Pool demo", "price": "10"})
    pid = product["id"]
    tickets = [call(args.api, "POST", f"/products/{pid}/images/upload-url",
                    {"fileName": f"p{i}.png", "contentType": "image/png", "sizeBytes": 100})[2]
               for i in range(args.confirms)]
    print(f"Demo product {pid}, {len(tickets)} pending images. Pausing MinIO ...")

    subprocess.run(["docker", "pause", MINIO], check=True, capture_output=True)
    confirm_results = []
    try:
        def confirm(t):
            confirm_results.append(call(args.api, "POST", f"/products/{pid}/images/{t['imageId']}/confirm"))

        threads = [threading.Thread(target=confirm, args=(t,)) for t in tickets]
        for t in threads:
            t.start()
        time.sleep(1.5)  # let every confirm reach MinIO and hang

        print(f"{args.confirms} confirms are now waiting on MinIO. Meanwhile, GET /products (no MinIO involved):")
        for i in range(3):
            status, ms, body = call(args.api, "GET", "/products?page=0&size=12", timeout=15)
            detail = body.get("title") if isinstance(body, dict) and status >= 400 else ""
            print(f"   attempt {i + 1}: HTTP {status} in {ms:,.0f} ms  {detail}")
    finally:
        subprocess.run(["docker", "unpause", MINIO], check=True, capture_output=True)
        print("MinIO unpaused.")

    for t in threads:
        t.join()
    print(f"Confirms finished: {dict(Counter(r[0] for r in confirm_results))} "
          f"(409 = nothing was uploaded, expected once MinIO answers)")
    call(args.api, "DELETE", f"/products/{pid}")


if __name__ == "__main__":
    main()
