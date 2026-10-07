"""
Knowledge-base documents API (Phase 2 M2), against a real Postgres.

Signing a POST policy is local (the region is configured, so no network call),
so the ticket is real. Only MinIO's delete is stubbed.
"""
import base64
import json
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from conftest import admin_bearer, bearer
from kirana_ai import api, config, kb, storage

UPLOAD = {"title": "Returns — ₹ refunds", "doc_type": "policy", "file_name": "Returns Policy (v2).pdf",
          "content_type": "application/pdf", "size_bytes": 1234}


@pytest.fixture
def client(ai_db):
    with ai_db.begin() as conn:
        conn.execute(text("TRUNCATE documents"))
    # Manage is admin-only (Phase 5): every call here carries an admin token unless it says otherwise.
    return TestClient(api.app, raise_server_exceptions=False, headers=admin_bearer(1))


def _signed_conditions(ticket: dict) -> list:
    policy = json.loads(base64.b64decode(ticket["form_fields"]["policy"]))
    return policy["conditions"]


def test_ticket_pins_key_type_size_and_metadata(client):
    r = client.post("/v1/kb/documents/upload-url", json=UPLOAD)

    assert r.status_code == 200
    t = r.json()
    doc_id = t["document_id"]
    assert t["object_key"] == f"{doc_id}/Returns-Policy-v2-.pdf"
    assert t["upload_url"] == f"{config.MINIO_PUBLIC_URL}/{config.KB_BUCKET}"

    fields = t["form_fields"]
    # Everything the policy names is in the form; MinIO's signing call adds only the signature.
    assert fields["key"] == t["object_key"]
    assert fields["Content-Type"] == "application/pdf"
    assert fields["x-amz-meta-document-id"] == doc_id
    assert fields["x-amz-meta-doc-type"] == "policy"
    assert unquote(fields["x-amz-meta-title"]) == "Returns — ₹ refunds"   # header-safe, round-trips
    assert {"policy", "x-amz-signature", "x-amz-credential"} <= set(fields)

    conditions = _signed_conditions(t)
    assert ["eq", "$key", t["object_key"]] in conditions
    assert ["eq", "$x-amz-meta-document-id", doc_id] in conditions
    assert ["eq", "$x-amz-meta-doc-type", "policy"] in conditions
    assert ["content-length-range", 1, config.KB_MAX_UPLOAD_BYTES] in conditions


def test_ticket_creates_a_pending_document(client):
    doc_id = client.post("/v1/kb/documents/upload-url", json=UPLOAD,
                         headers=admin_bearer(7)).json()["document_id"]

    (doc,) = client.get("/v1/kb/documents").json()
    assert doc["id"] == doc_id
    assert doc["status"] == "pending"
    assert doc["title"] == "Returns — ₹ refunds"
    assert doc["uploaded_by"] == "user:7"


@pytest.mark.parametrize("change, field", [
    ({"content_type": "application/zip"}, "content_type"),
    ({"doc_type": "secret"}, "doc_type"),
    ({"title": "  "}, "title"),
    ({"size_bytes": 0}, "size_bytes"),
])
def test_invalid_upload_requests(client, change, field):
    r = client.post("/v1/kb/documents/upload-url", json={**UPLOAD, **change})

    assert r.status_code == 400
    assert [e["field"] for e in r.json()["errors"]] == [field]


def test_too_large_is_refused_before_the_upload(client):
    r = client.post("/v1/kb/documents/upload-url",
                    json={**UPLOAD, "size_bytes": config.KB_MAX_UPLOAD_BYTES + 1})

    assert r.status_code == 400
    assert r.json()["errors"][0]["field"] == "size_bytes"


class FakeMinio:
    def __init__(self):
        self.removed = []

    def remove_object(self, bucket, key):
        self.removed.append((bucket, key))


def test_deleting_a_pending_document_still_removes_its_file(client, monkeypatch):
    """Pending = no event processed yet, not "no file": the browser may have just uploaded it."""
    doc_id = client.post("/v1/kb/documents/upload-url", json=UPLOAD).json()["document_id"]
    fake = FakeMinio()
    monkeypatch.setattr(storage, "client", lambda: fake)

    assert client.delete(f"/v1/kb/documents/{doc_id}").status_code == 204

    assert fake.removed == [(config.KB_BUCKET, f"{doc_id}/Returns-Policy-v2-.pdf")]
    assert client.get("/v1/kb/documents").json() == []          # nothing indexed: deleted at once


def test_deleting_an_uploaded_document_removes_the_file_and_waits_for_the_event(client, ai_db, monkeypatch):
    doc_id = client.post("/v1/kb/documents/upload-url", json=UPLOAD).json()["document_id"]
    with ai_db.begin() as conn:     # as the worker will after the upload event (M3)
        conn.execute(text("UPDATE documents SET status = 'ready' WHERE id = :id"), {"id": doc_id})
    fake = FakeMinio()
    monkeypatch.setattr(storage, "client", lambda: fake)

    assert client.delete(f"/v1/kb/documents/{doc_id}").status_code == 204

    assert fake.removed == [(config.KB_BUCKET, f"{doc_id}/Returns-Policy-v2-.pdf")]
    assert client.get("/v1/kb/documents").json()[0]["status"] == "deleting"


def test_unknown_document_is_404(client):
    r = client.delete("/v1/kb/documents/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404 and r.json()["title"] == "Document not found"


@pytest.mark.parametrize("name, safe", [
    ("Returns Policy (v2).pdf", "Returns-Policy-v2-.pdf"),
    ("../../etc/passwd", "etc-passwd"),
    ("दिवाली.md", "md"),
])
def test_file_names_are_made_safe_for_keys(name, safe):
    assert kb.safe_file_name(name) == safe


def test_kb_admin_needs_the_kb_write_permission(ai_db):
    anonymous = TestClient(api.app, raise_server_exceptions=False)
    assert anonymous.get("/v1/kb/documents").status_code == 401
    r = anonymous.post("/v1/kb/documents/upload-url", json=UPLOAD, headers=bearer(7))   # a shopper
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"
