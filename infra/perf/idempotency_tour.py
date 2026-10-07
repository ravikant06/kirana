#!/usr/bin/env python3
"""
Stage 7: see every Idempotency-Key rule happen, with the row Postgres stores after each step.

    python3 infra/perf/idempotency_tour.py              # against the backend on :8080

  1. no key                          -> 400
  2. add to cart with key K1         -> 200, row COMPLETED with the stored response
  3. same request, same K1           -> 200 replayed (Idempotent-Replayed: true), cart still 1
  4. same K1, different body         -> 422
  5. place order K2, gateway slowed  -> while it runs, a second K2 -> 409 + Retry-After (row IN_PROGRESS)
                                        then K2 again -> the same order, replayed
  6. pay now K3, then K3 again       -> the same payment session, replayed
  7. one webhook delivered twice     -> the second is "duplicate ignored": one outbox row
  8. cancel K4, K4 again, new key    -> 200, 200 replayed, 409 (a new action on a cancelled order)

Changes shared dev state briefly (payment-mock's mode) and always restores it.
"""
import argparse
import hashlib
import hmac
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

import kirana_auth  # AI Phase 5: bearer tokens instead of X-User-Id

MOCK = "http://localhost:8090"
WEBHOOK_SECRET = "kirana-mock-webhook-secret"  # payment-mock's dev secret (infra/docker-compose.yml)


def call(api, method, path, body=None, user=None, key=None, headers=None):
    h = {"Content-Type": "application/json", **kirana_auth.headers(api, user), **(headers or {})}
    if key:
        h["Idempotency-Key"] = key
    data = body if isinstance(body, bytes) else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(api + path, method=method, headers=h, data=data)
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            text = r.read().decode()
            return r.status, dict(r.headers), json.loads(text) if text else None, (time.perf_counter() - start) * 1000
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        return e.code, dict(e.headers), json.loads(text) if text else None, (time.perf_counter() - start) * 1000


def sql(query):
    out = subprocess.run(["docker", "exec", "kirana-postgres-1", "psql", "-U", "kirana", "-d", "kirana", "-c", query],
                         capture_output=True, text=True)
    return out.stdout.rstrip()


def key_row(user, key):
    return sql(f"""SELECT idempotency_key AS key, endpoint, status, left(request_hash, 12) AS request_hash,
        recovery_point, resource_id, response_status, left(response_body, 60) AS response_body,
        to_char(locked_until, 'HH24:MI:SS') AS locked_until
        FROM idempotency_keys WHERE user_id = {user} AND idempotency_key = '{key}'""")


def show(label, resp):
    status, headers, body, ms = resp
    extra = []
    if headers.get("Idempotent-Replayed"):
        extra.append("Idempotent-Replayed: true")
    if headers.get("Retry-After"):
        extra.append(f"Retry-After: {headers['Retry-After']}")
    if headers.get("Location"):
        extra.append(f"Location: {headers['Location']}")
    title = body.get("title") if isinstance(body, dict) else None
    print(f"   {label}: {status} in {ms:.0f} ms" + (f"  [{', '.join(extra)}]" if extra else "")
          + (f"  {title}: {body.get('detail', '')[:90]}" if title else ""))


def mock_mode(mode, **extra):
    urllib.request.urlopen(urllib.request.Request(f"{MOCK}/admin/mode", data=json.dumps({"mode": mode, **extra}).encode(),
                                                  method="POST", headers={"Content-Type": "application/json"})).read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8080")
    api = ap.parse_args().api

    u = kirana_auth.signup(api, "Tour", f"tour-{time.time_ns()}@t.com")
    user = u["id"]
    _, _, p, _ = call(api, "POST", "/products", {"name": f"Tour tea {time.time_ns() % 100000}", "price": "50"})
    call(api, "PUT", f"/products/{p['id']}/inventory", {"quantity": 10})
    tea = {"productId": p["id"], "quantity": 1}
    print(f"shopper {user}, product {p['id']}\n")

    print("1. Add to cart WITHOUT a key")
    show("response", call(api, "POST", "/cart/items", tea, user))

    k1 = f"K1-{uuid.uuid4().hex[:8]}"
    print(f"\n2. Add to cart with Idempotency-Key {k1}")
    show("response", call(api, "POST", "/cart/items", tea, user, k1))
    print("   stored:\n" + key_row(user, k1))

    print(f"\n3. The SAME request again (a retry after a lost response), same key {k1}")
    show("response", call(api, "POST", "/cart/items", tea, user, k1))
    _, _, cart, _ = call(api, "GET", "/cart", user=user)
    print(f"   cart quantity: {sum(i['quantity'] for i in cart['items'])}  (the add ran once)")

    print(f"\n4. Same key {k1}, DIFFERENT body (quantity 3)")
    show("response", call(api, "POST", "/cart/items", {**tea, "quantity": 3}, user, k1))

    k2 = f"K2-{uuid.uuid4().hex[:8]}"
    print(f"\n5. Place order with {k2} while the gateway is slow (so the request takes seconds)")
    mock_mode("slow", delay_ms=2500)
    first = {}
    try:
        t = threading.Thread(target=lambda: first.setdefault("r", call(api, "POST", "/orders", {"paymentProvider": "mock"}, user, k2)))
        t.start()
        time.sleep(1.0)
        print("   while the first attempt is still running, the row is:\n" + key_row(user, k2))
        show("second attempt, same key", call(api, "POST", "/orders", {"paymentProvider": "mock"}, user, k2))
        t.join()
    finally:
        mock_mode("normal")
    show("first attempt finished", first["r"])
    order = first["r"][2]["order"]["id"]
    print("   stored:\n" + key_row(user, k2))
    show("third attempt, same key", call(api, "POST", "/orders", {"paymentProvider": "mock"}, user, k2))
    _, _, mine, _ = call(api, "GET", "/orders", user=user)
    print(f"   orders for this shopper: {[o['id'] for o in mine]}  (one order)")

    k3 = f"K3-{uuid.uuid4().hex[:8]}"
    print(f"\n6. Pay now on order {order} with {k3}, then the same again")
    r = call(api, "POST", f"/orders/{order}/payment", None, user, k3)
    show("first", r)
    gateway_order = r[2]["gatewayOrderId"]
    r2 = call(api, "POST", f"/orders/{order}/payment", None, user, k3)
    show("same key", r2)
    print(f"   gateway order: {gateway_order} both times: {r2[2]['gatewayOrderId'] == gateway_order}")

    print(f"\n7. One gateway webhook for order {order}, delivered twice")
    event_id = f"evt_tour_{uuid.uuid4().hex[:10]}"
    body = json.dumps({"event": "payment.captured", "payload": {"payment": {"entity": {
        "id": f"pay_tour_{uuid.uuid4().hex[:8]}", "order_id": gateway_order, "status": "captured"}}}},
        separators=(",", ":")).encode()
    signature = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    hdrs = {"X-Razorpay-Signature": signature, "X-Razorpay-Event-Id": event_id}
    show("delivery 1", call(api, "POST", "/webhooks/payment/mock", body, headers=hdrs))
    show("delivery 2 (the gateway retried)", call(api, "POST", "/webhooks/payment/mock", body, headers=hdrs))
    print(sql(f"SELECT event_type, count(*) AS rows FROM outbox WHERE message_key = '{order}' "
              f"AND event_type = 'PaymentCaptured' GROUP BY 1"))
    time.sleep(2)
    _, _, o, _ = call(api, "GET", f"/orders/{order}", user=user)
    print(f"   order {order} is now {o['status']} (the consumer applied the one event)")

    call(api, "POST", "/cart/items", tea, user, str(uuid.uuid4()))
    _, _, placed, _ = call(api, "POST", "/orders", {"paymentProvider": "mock"}, user, str(uuid.uuid4()))
    other = placed["order"]["id"]
    k4 = f"K4-{uuid.uuid4().hex[:8]}"
    print(f"\n8. Cancel order {other} with {k4}, the same again, then with a NEW key")
    show("first", call(api, "POST", f"/orders/{other}/cancel", None, user, k4))
    show("same key", call(api, "POST", f"/orders/{other}/cancel", None, user, k4))
    show("new key", call(api, "POST", f"/orders/{other}/cancel", None, user, str(uuid.uuid4())))
    print("   stored:\n" + key_row(user, k4))


if __name__ == "__main__":
    main()
