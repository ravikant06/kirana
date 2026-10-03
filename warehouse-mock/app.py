"""
warehouse-mock: a stand-in for a fulfilment / logistics partner (Stage 6e).

Kirana asks it to ship a paid order. Like most real logistics APIs, creating a shipment is
idempotent by an Idempotency-Key header: the same key always returns the same shipment, so a
caller may safely send the request again after a timeout.

    POST /v1/shipments          header Idempotency-Key, X-Api-Key; body {order_id, items: [...]}
                                201 new shipment, or 200 + Idempotent-Replayed: true for a repeat
    GET  /v1/shipments          ?order_id=  (to check that an order shipped exactly once)
    GET|POST /admin/mode        {"mode": "normal|slow|down", "delay_ms"}
    GET  /health

State is in memory: a restart forgets every shipment.
"""
import asyncio
import hmac
import logging
import os
import secrets
import threading
import time

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

API_KEY = os.environ.get("WAREHOUSE_API_KEY", "kirana-warehouse-key")

app = FastAPI(title="warehouse-mock")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
log = logging.getLogger("uvicorn.error")

_lock = threading.Lock()
_shipments: dict[str, dict] = {}      # shipment id -> shipment
_by_key: dict[str, str] = {}          # Idempotency-Key -> shipment id
_mode = {"mode": "normal", "delay_ms": 5000}


@app.middleware("http")
async def faults(request: Request, call_next):
    """Make the API slow or down, to see what Kirana does when its warehouse is in trouble."""
    if request.url.path.startswith("/v1/"):
        if _mode["mode"] == "slow":
            await asyncio.sleep(_mode["delay_ms"] / 1000)
        elif _mode["mode"] == "down":
            return JSONResponse({"error": "warehouse-mock is down"}, 503)
    return await call_next(request)


class Item(BaseModel):
    sku: str
    name: str
    quantity: int


class CreateShipment(BaseModel):
    order_id: str
    items: list[Item]


@app.post("/v1/shipments")
def create_shipment(body: CreateShipment, response: Response,
                    idempotency_key: str | None = Header(default=None),
                    x_api_key: str | None = Header(default=None)):
    if not x_api_key or not hmac.compare_digest(x_api_key, API_KEY):
        raise HTTPException(401, "bad api key")
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key header is required")
    if not body.items:
        raise HTTPException(422, "a shipment needs at least one item")
    with _lock:
        if idempotency_key in _by_key:
            shipment = _shipments[_by_key[idempotency_key]]
            response.headers["Idempotent-Replayed"] = "true"
            log.info("shipment %s for order %s: repeat request (key %s), same shipment returned",
                     shipment["id"], body.order_id, idempotency_key)
            return shipment
        shipment = {
            "id": "shp_" + secrets.token_hex(5), "order_id": body.order_id, "status": "accepted",
            "items": [i.model_dump() for i in body.items], "created_at": int(time.time()),
        }
        _shipments[shipment["id"]] = shipment
        _by_key[idempotency_key] = shipment["id"]
    log.info("shipment %s for order %s: accepted (%d item(s))", shipment["id"], body.order_id, len(body.items))
    response.status_code = 201
    return shipment


@app.get("/v1/shipments")
def list_shipments(order_id: str | None = None):
    items = [s for s in _shipments.values() if order_id is None or s["order_id"] == order_id]
    return {"count": len(items), "items": items}


class Mode(BaseModel):
    mode: str
    delay_ms: int | None = None


@app.get("/admin/mode")
def get_mode():
    return _mode


@app.post("/admin/mode")
def set_mode(m: Mode):
    if m.mode not in ("normal", "slow", "down"):
        raise HTTPException(400, "mode must be normal, slow or down")
    _mode["mode"] = m.mode
    if m.delay_ms is not None:
        _mode["delay_ms"] = max(0, m.delay_ms)
    log.info("mode: %s", _mode)
    return _mode


@app.get("/health")
def health():
    return {"status": "UP", "shipments": len(_shipments), "mode": _mode["mode"]}
