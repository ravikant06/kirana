"""
payment-mock: a stand-in for Razorpay's test mode that Kirana can break on purpose.

Same API shape as Razorpay (https://razorpay.com/docs/api/orders/), so Kirana's one
Razorpay-style client talks to both:
    POST /v1/orders                   create an order (idempotent on receipt)
    GET  /v1/orders/{id}              the order
    GET  /v1/orders/{id}/payments     its payments (status "captured" or "failed")
Plus a hosted checkout page the browser opens (Razorpay's is its checkout.js modal):
    GET  /checkout/{order_id}         Pay / Decline / "pay but lose the reply" / Cancel
And a switch for Stage 5 failure experiments, applied to the /v1 API only:
    GET|POST /admin/mode              {"mode": "normal|slow|down|flaky|hang", "delay_ms", "failure_rate"}
Stage 6: webhooks, like Razorpay's. After every payment attempt it POSTs a signed event
(payment.captured / payment.failed) to WEBHOOK_URL, retrying with growing waits until it gets a 2xx:
    GET|POST /admin/webhooks          {"enabled": bool, "duplicate": bool, "url": "..."}
    GET  /admin/webhooks/deliveries   the last deliveries and their attempts

State is in memory: restarting the mock forgets every order (the reconciler then sees UNKNOWN).
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import random
import secrets
import threading
import time
import urllib.error
import urllib.request
from collections import deque

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

KEY_ID = os.environ.get("MOCK_KEY_ID", "rzp_test_kiranamock")
KEY_SECRET = os.environ.get("MOCK_KEY_SECRET", "kirana-mock-secret")
# Webhooks are signed with a separate secret, as in Razorpay (set in its dashboard).
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "kirana-mock-webhook-secret")

app = FastAPI(title="payment-mock")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_lock = threading.Lock()
_orders: dict[str, dict] = {}        # order_id -> order
_by_receipt: dict[str, str] = {}      # receipt -> order_id (idempotency)
_payments: dict[str, list] = {}       # order_id -> [payment]
_mode = {"mode": "normal", "delay_ms": 3000, "failure_rate": 0.3}
_webhooks = {
    "url": os.environ.get("WEBHOOK_URL", "http://host.docker.internal:8080/webhooks/payment/mock"),
    "enabled": True,     # off: the shop only learns from the browser or the reconciler
    "duplicate": False,  # on: every event is delivered twice (consumers must not double-apply)
}
_deliveries: deque = deque(maxlen=30)
log = logging.getLogger("uvicorn.error")  # shows up in `docker logs` next to uvicorn's lines
RETRY_DELAYS = [1, 2, 4, 8, 16, 32]  # seconds between attempts, then give up (Razorpay retries for ~24 h)


def _new_id(prefix: str) -> str:
    return prefix + "_" + secrets.token_urlsafe(10).replace("-", "x").replace("_", "y")


def _sign(order_id: str, payment_id: str) -> str:
    return hmac.new(KEY_SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def _check_auth(request: Request) -> None:
    header = request.headers.get("authorization", "")
    expected = "Basic " + base64.b64encode(f"{KEY_ID}:{KEY_SECRET}".encode()).decode()
    if not hmac.compare_digest(header, expected):
        raise HTTPException(401, {"error": {"code": "BAD_REQUEST_ERROR", "description": "Authentication failed"}})


@app.middleware("http")
async def faults(request: Request, call_next):
    """Stage 5: make the gateway API slow, down, flaky or hang. The checkout page is unaffected."""
    if request.url.path.startswith("/v1/"):
        mode = _mode["mode"]
        if mode == "slow":
            await asyncio.sleep(_mode["delay_ms"] / 1000)
        elif mode == "hang":
            await asyncio.sleep(600)
        elif mode == "down" or (mode == "flaky" and random.random() < _mode["failure_rate"]):
            return JSONResponse({"error": {"code": "SERVER_ERROR", "description": f"payment-mock is {mode}"}}, 503)
    return await call_next(request)


# ---------------------------------------------------------------- Razorpay-shaped API

class CreateOrder(BaseModel):
    amount: int
    currency: str = "INR"
    receipt: str | None = None
    notes: dict | None = None


@app.post("/v1/orders")
def create_order(body: CreateOrder, request: Request):
    _check_auth(request)
    if body.amount < 100:
        raise HTTPException(400, {"error": {"code": "BAD_REQUEST_ERROR", "description": "amount must be at least 100 paise"}})
    with _lock:
        if body.receipt and body.receipt in _by_receipt:
            return _orders[_by_receipt[body.receipt]]
        order = {
            "id": _new_id("order"), "entity": "order", "amount": body.amount, "amount_paid": 0,
            "currency": body.currency, "receipt": body.receipt, "status": "created",
            "notes": body.notes or {}, "created_at": int(time.time()),
        }
        _orders[order["id"]] = order
        _payments[order["id"]] = []
        if body.receipt:
            _by_receipt[body.receipt] = order["id"]
        return order


@app.get("/v1/orders/{order_id}")
def get_order(order_id: str, request: Request):
    _check_auth(request)
    if order_id not in _orders:
        raise HTTPException(404, {"error": {"code": "BAD_REQUEST_ERROR", "description": "The id provided does not exist"}})
    return _orders[order_id]


@app.get("/v1/orders/{order_id}/payments")
def order_payments(order_id: str, request: Request):
    _check_auth(request)
    if order_id not in _orders:
        raise HTTPException(404, {"error": {"code": "BAD_REQUEST_ERROR", "description": "The id provided does not exist"}})
    items = _payments[order_id]
    return {"entity": "collection", "count": len(items), "items": items}


# ---------------------------------------------------------------- hosted checkout page

class Attempt(BaseModel):
    outcome: str  # "success" | "failed"


@app.post("/checkout/{order_id}/attempt")
def attempt(order_id: str, body: Attempt):
    with _lock:
        order = _orders.get(order_id)
        if order is None:
            raise HTTPException(404, "Unknown order")
        if order["status"] == "paid":
            raise HTTPException(409, "Order already paid")
        payment = {"id": _new_id("pay"), "entity": "payment", "amount": order["amount"],
                   "currency": order["currency"], "order_id": order_id, "created_at": int(time.time())}
        if body.outcome == "success":
            payment["status"] = "captured"
            order["status"], order["amount_paid"] = "paid", order["amount"]
        else:
            payment["status"] = "failed"
            payment["error_description"] = "Payment declined by the test bank"
            order["status"] = "attempted"
        _payments[order_id].append(payment)
    log.info("payment %s for %s: %s", payment["id"], order_id, payment["status"])
    _send_webhook("payment.captured" if payment["status"] == "captured" else "payment.failed", payment)
    if payment["status"] == "captured":
        # What Razorpay's checkout hands the browser: the server must verify this signature.
        return {"razorpay_order_id": order_id, "razorpay_payment_id": payment["id"],
                "razorpay_signature": _sign(order_id, payment["id"])}
    return {"error": payment["error_description"]}


@app.get("/checkout/{order_id}", response_class=HTMLResponse)
def checkout_page(order_id: str):
    order = _orders.get(order_id)
    if order is None:
        return HTMLResponse("<p style='font-family:sans-serif'>Unknown or expired payment. It may have been "
                            "created before payment-mock restarted.</p>", 404)
    rupees = f"{order['amount'] / 100:,.2f}"
    return HTMLResponse(PAGE.replace("{ORDER_ID}", order_id).replace("{AMOUNT}", rupees))


@app.get("/admin/mode")
def get_mode():
    return _mode


class Mode(BaseModel):
    mode: str
    delay_ms: int | None = None
    failure_rate: float | None = None


@app.post("/admin/mode")
def set_mode(m: Mode):
    if m.mode not in ("normal", "slow", "down", "flaky", "hang"):
        raise HTTPException(400, "mode must be normal, slow, down, flaky or hang")
    _mode["mode"] = m.mode
    if m.delay_ms is not None:
        _mode["delay_ms"] = max(0, m.delay_ms)
    if m.failure_rate is not None:
        _mode["failure_rate"] = min(1.0, max(0.0, m.failure_rate))
    return _mode


# ---------------------------------------------------------------- webhooks (Stage 6)

def _send_webhook(event: str, payment: dict) -> None:
    if not _webhooks["enabled"]:
        return
    copies = 2 if _webhooks["duplicate"] else 1
    event_id = "evt_" + secrets.token_hex(8)  # the same id on every retry and every duplicate
    body = json.dumps({
        "entity": "event", "account_id": "acc_kiranamock", "event": event, "contains": ["payment"],
        "payload": {"payment": {"entity": payment}}, "created_at": int(time.time()),
    }, separators=(",", ":")).encode()
    signature = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    for _ in range(copies):
        threading.Thread(target=_deliver, args=(event_id, event, body, signature, _webhooks["url"]), daemon=True).start()


def _deliver(event_id: str, event: str, body: bytes, signature: str, url: str) -> None:
    record = {"event_id": event_id, "event": event, "url": url, "attempts": [], "delivered": False}
    _deliveries.appendleft(record)
    for attempt, wait in enumerate([0] + RETRY_DELAYS, start=1):
        time.sleep(wait)
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": signature,
            "X-Razorpay-Event-Id": event_id,
        })
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                record["attempts"].append({"n": attempt, "status": res.status})
                log.info("webhook %s %s for %s: attempt %d -> %d", event_id, event, _order_of(body), attempt, res.status)
                if 200 <= res.status < 300:
                    record["delivered"] = True
                    return
        except urllib.error.HTTPError as e:
            record["attempts"].append({"n": attempt, "status": e.code})
            log.info("webhook %s %s for %s: attempt %d -> %d", event_id, event, _order_of(body), attempt, e.code)
        except Exception as e:  # connection refused, timeout: the shop is down
            record["attempts"].append({"n": attempt, "error": type(e).__name__})
            log.info("webhook %s %s for %s: attempt %d -> %s (shop unreachable)", event_id, event, _order_of(body),
                     attempt, type(e).__name__)
    log.warning("webhook %s %s: gave up after %d attempts", event_id, event, len(record["attempts"]))


def _order_of(body: bytes) -> str:
    return json.loads(body)["payload"]["payment"]["entity"]["order_id"]


class WebhookSettings(BaseModel):
    enabled: bool | None = None
    duplicate: bool | None = None
    url: str | None = None


@app.get("/admin/webhooks")
def get_webhooks():
    return _webhooks


@app.post("/admin/webhooks")
def set_webhooks(w: WebhookSettings):
    for k, v in w.model_dump(exclude_none=True).items():
        _webhooks[k] = v
    return _webhooks


@app.get("/admin/webhooks/deliveries")
def deliveries():
    return list(_deliveries)


@app.get("/health")
def health():
    return {"status": "UP", "orders": len(_orders), "mode": _mode["mode"]}


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kirana test gateway</title>
<style>
  body { margin: 0; font: 15px/1.5 system-ui, sans-serif; background: #f4f6f9; color: #1b2a3a; }
  .card { max-width: 380px; margin: 24px auto; background: #fff; border-radius: 14px; padding: 22px;
          box-shadow: 0 6px 24px rgba(27,42,58,.12); display: grid; gap: 14px; }
  .brand { font-weight: 800; letter-spacing: .02em; color: #b87708; font-size: 13px; text-transform: uppercase; }
  .amount { font-size: 30px; font-weight: 800; }
  .muted { color: #5b6b7b; font-size: 13px; }
  button { font: inherit; font-weight: 700; border-radius: 10px; padding: 11px 14px; border: 0; cursor: pointer; }
  .pay { background: #1b2a3a; color: #fff; }
  .decline { background: #fbe7e5; color: #b3261e; }
  .lost { background: #fdf1d8; color: #8a5a00; }
  .cancel { background: none; color: #5b6b7b; text-decoration: underline; }
  button:disabled { opacity: .5; cursor: wait; }
  #msg { min-height: 1.5em; font-weight: 700; }
</style></head>
<body><div class="card">
  <div class="brand">Kirana test gateway &middot; no real money</div>
  <div><div class="muted">Pay</div><div class="amount">&#8377;{AMOUNT}</div>
       <div class="muted">Gateway order {ORDER_ID}</div></div>
  <button class="pay" data-outcome="success" data-tell="1">Pay &#8377;{AMOUNT}</button>
  <button class="decline" data-outcome="failed" data-tell="1">Decline payment</button>
  <button class="lost" data-outcome="success" data-tell="0" title="The gateway takes the money, but the shop is never told. The reconciler must notice.">
    Pay, but the shop never hears back</button>
  <button class="cancel" data-cancel="1">Cancel</button>
  <div id="msg" role="status"></div>
</div>
<script>
  const orderId = "{ORDER_ID}";
  const tell = (m) => window.parent.postMessage({ source: "payment-mock", orderId, ...m }, "*");
  const msg = document.getElementById("msg");
  document.querySelectorAll("button").forEach((b) => b.addEventListener("click", async () => {
    if (b.dataset.cancel) { tell({ status: "dismissed" }); return; }
    document.querySelectorAll("button").forEach((x) => (x.disabled = true));
    msg.textContent = "Processing...";
    const r = await fetch(`/checkout/${orderId}/attempt`, { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ outcome: b.dataset.outcome }) });
    const data = await r.json();
    if (!r.ok) { msg.textContent = data.detail || "Payment failed"; tell({ status: "failed", reason: msg.textContent }); return; }
    if (data.error) { msg.textContent = data.error; tell({ status: "failed", reason: data.error }); return; }
    if (b.dataset.tell === "1") { msg.textContent = "Paid"; tell({ status: "paid", ...data }); }
    else { msg.textContent = "Paid. The shop was not told."; setTimeout(() => tell({ status: "dismissed" }), 900); }
  }));
</script></body></html>
"""
