#!/usr/bin/env python3
"""
Stage 7a: what happens when a client retries after losing the response.

    python3 infra/perf/duplicate_demo.py                 # against the backend on :8080, with keys
    python3 infra/perf/duplicate_demo.py --no-keys       # as a client without keys (7a; 400 since 7b)

The client talks to the API through Toxiproxy (localhost:28080 -> backend). For each action a
"latency" toxic delays the RESPONSE by 3 s while the client waits only 1 s: the request reaches
the backend and is fully processed, but the client times out, as on a flaky mobile network.
The toxic is then removed and the client retries the identical request, like any HTTP client
or impatient user would. Each scenario prints what the client saw and what the server did.

Before Stage 7 (no keys), P2 (add to cart) and P1/P3 (place order, cancel) showed the problems
Stage 7 fixes; since 7b a request without a key is refused with 400.
"""
import argparse
import json
import time
import urllib.error
import urllib.request
import uuid

TOXIPROXY = "http://localhost:8474"
PROXY = "kirana-api"


def call(base, method, path, body=None, user=None, key=None, timeout=10):
    headers = {"Content-Type": "application/json"}
    if user:
        headers["X-User-Id"] = str(user)
    if key:
        headers["Idempotency-Key"] = key
    req = urllib.request.Request(base + path, method=method, headers=headers,
                                 data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "null")
    except Exception as e:  # the client gave up waiting
        return None, type(e).__name__


def toxic(on):
    if on:
        body = {"name": "lose_response", "type": "latency", "stream": "downstream", "attributes": {"latency": 3000}}
        req = urllib.request.Request(f"{TOXIPROXY}/proxies/{PROXY}/toxics", data=json.dumps(body).encode(),
                                     method="POST", headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(f"{TOXIPROXY}/proxies/{PROXY}/toxics/lose_response", method="DELETE")
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except urllib.error.HTTPError:
        pass  # already on / already off


def lost_then_retry(args, method, path, body, user):
    """First attempt: response lost (client times out after 1 s). Retry: same request, network fine."""
    key = str(uuid.uuid4()) if args.keys else None  # one key per user action, reused on its retry
    toxic(True)
    first = call(args.proxy, method, path, body, user, key, timeout=1)
    time.sleep(2.5)  # the server has long finished; the delayed response is still on its way
    toxic(False)
    retry = call(args.proxy, method, path, body, user, key)
    return first, retry


def short(resp):
    status, body = resp
    if status is None:
        return f"no response ({body})"
    if isinstance(body, dict) and "title" in body:
        return f"{status} {body['title']}: {body.get('detail', '')}"
    return f"{status}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8080", help="the backend, directly (for setup and checks)")
    ap.add_argument("--proxy", default="http://localhost:28080", help="the same backend through Toxiproxy")
    ap.add_argument("--no-keys", dest="keys", action="store_false",
                    help="send no Idempotency-Key (what every client did before Stage 7)")
    args = ap.parse_args()
    api = args.api
    toxic(False)

    _, u = call(api, "POST", "/users", {"name": "Retry", "email": f"retry-{time.time_ns()}@test.com"})
    user = u["id"]
    _, p = call(api, "POST", "/products", {"name": f"Retry tea {time.time_ns()}", "price": "50"})
    call(api, "PUT", f"/products/{p['id']}/inventory", {"quantity": 10})
    print(f"user {user}, product {p['id']} (stock 10); keys: {'yes' if args.keys else 'no'}\n")

    print("1. Add 1 tea to the cart; the response is lost; the client retries")
    first, retry = lost_then_retry(args, "POST", "/cart/items", {"productId": p["id"], "quantity": 1}, user)
    _, cart = call(api, "GET", "/cart", user=user)
    qty = sum(i["quantity"] for i in cart["items"])
    print(f"   client saw: {short(first)}, then {short(retry)}")
    print(f"   server: cart has {qty} tea  {'<-- wanted 1' if qty != 1 else 'ok'}\n")
    if qty != 1:
        call(api, "PUT", f"/cart/items/{p['id']}", {"quantity": 1}, user=user)
    if qty == 0:
        print("   (no key, so nothing was added: the rest needs a cart; stopping)")
        return

    print("2. Place the order; the response is lost; the client retries")
    first, retry = lost_then_retry(args, "POST", "/orders", {"paymentProvider": "mock"}, user)
    _, mine = call(api, "GET", "/orders", user=user)
    print(f"   client saw: {short(first)}, then {short(retry)}")
    got = retry[1]["order"]["id"] if retry[0] == 201 else None
    print(f"   server: {len(mine)} order(s) exist: {[o['id'] for o in mine]}; the client "
          f"{'knows its order: #' + str(got) if got else 'does NOT know its order id'}\n")
    order = mine[0]["id"]

    print("3. Pay now on that order; the response is lost; the client retries")
    first, retry = lost_then_retry(args, "POST", f"/orders/{order}/payment", {"paymentProvider": "mock"}, user)
    print(f"   client saw: {short(first)}, then {short(retry)}"
          + (f" (gateway order {retry[1]['gatewayOrderId']})" if retry[0] == 200 else ""))
    print("   (already safe without a key: D55 reuses the order's one gateway order)\n")

    print("4. Cancel it; the response is lost; the client retries")
    first, retry = lost_then_retry(args, "POST", f"/orders/{order}/cancel", None, user)
    _, o = call(api, "GET", f"/orders/{order}", user=user)
    print(f"   client saw: {short(first)}, then {short(retry)}")
    print(f"   server: order {order} is {o['status']}  {'<-- the cancel worked, but the client was told it failed' if retry[0] != 200 else 'ok'}")


if __name__ == "__main__":
    main()
