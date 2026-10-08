# ai-contract.md: Kirana ⇄ Kirana AI (v0.9)

The one and only copy (`kirana/kirana-ai/docs/`). Bump the version on every change.
**(Pn)** = added in phase n of `AI-PLAN.md`. Build only what the current phase needs.

## 0. Conventions

- The browser reaches the AI service through the Vite proxy: `/ai/*` → `http://localhost:8000/*`
  (the prefix is stripped, as with `/api`). No CORS on either service.
- Errors are RFC 7807 `ProblemDetail`, the same shape as Kirana, with media type
  `application/problem+json`: `{ type, title, status, detail, instance, code?, errors? }`.
  Validation failures are **400** (not FastAPI's default 422) with `errors: [{field, message}]`. `code` is a stable machine string
  (`RATE_LIMITED`, `UPSTREAM_UNAVAILABLE`, `UNAUTHENTICATED`, `BUDGET_EXCEEDED`).
- Ids are strings in JSON. Timestamps are ISO-8601 UTC.
- Money from Kirana is a JSON number today (D3). The AI service **never computes money**;
  it passes Kirana's values through, or shows none.

## 1. Identity

| Phase | Browser → AI | AI → Kirana |
|---|---|---|
| P1–P4 | `X-User-Id` (used **only** to scope threads; forgeable, like Kirana today) | none: tools call only public endpoints |
| P6 ✅ | same as P5 | never the user's own token: a token exchanged for exactly the scopes a tool needs |
| P5+ ✅ | `Authorization: Bearer <Kirana JWT>` on every route (RS256; verified against Kirana's `/.well-known/jwks.json`: `iss` = kirana, `aud` ∋ kirana-ai, `exp`). Permissions from the `scope` claim: chat and threads need `chat`, `/v1/kb/**` needs `kb:write` (403 otherwise). `X-User-Id` is not read | the same header, forwarded unchanged by the order tools |

The request body and tool arguments **never** contain a user id.

## 2. Chat API (Browser → AI)

| Phase | Method + path | Request → Response |
|---|---|---|
| P1 | `POST /v1/chat` | `{thread_id?, message}` → `ChatReply` (JSON) |
| P3 | `POST /v1/chat` with `Accept: text/event-stream` | same request → SSE (section 3). JSON stays for evals and curl. A foreign thread is still a plain 404: ownership is checked before the stream starts |
| P1 | `GET /v1/threads` | → `[ThreadSummary]` newest first, max 50 |
| P1 | `GET /v1/threads/{id}` | → `Thread` (404 if not the caller's) |
| P1 | `DELETE /v1/threads/{id}` | → 204 |
| P6 ✅ | `GET /v1/approvals/{approval_id}` | → `Approval` (404 if not the caller's) |
| P6 ✅ | `POST /v1/approvals/{approval_id}` | `{decision: "confirm" \| "reject"}` → `Approval`. Runs the server's stored copy; a repeat returns the first outcome; 409 `APPROVAL_TAMPERED` if the stored row no longer matches its seal |
| P7 ✅ | `GET /v1/memories` | → `[Memory]`: the caller's long-term memories, pinned first |
| P7 ✅ | `PATCH /v1/memories/{id}` | `{pinned: bool}` → `Memory` (404 if not the caller's) |
| P7 ✅ | `DELETE /v1/memories/{id}` | → 204; from then on never used (404 if not the caller's) |
| P9 | `POST /v1/messages/{message_id}/feedback` | `{rating: "up" \| "down", comment?}` → 204 |

    ChatRequest   = { thread_id?, message }          // message 1–2000 chars after trimming; unknown fields → 400
    ChatReply     = { thread_id, message_id, reply, citations: [Citation], steps: [Step], usage: Usage,
                      product_ids: [int] }      // P7: the cards to show: the products the answer names, in order
    Usage         = { llm_calls, input_tokens, output_tokens, latency_ms, cached_input_tokens,   // P7: from the prompt cache
                      cost_usd }                      // decimal string "0.004170", or null = unknown price
    Citation      = { source, doc_id, title?, pages: [int] }   // pages: PDFs only (P2)
    Step          = { tool, query, where, count,     // one tool call: filters the model chose, hits returned
                      decision, reason, denied_by?,  // P6: the policy's allow|deny|approval, why, and who refused
                      approval? }                    // P6: the card, when an action awaits confirmation
    Approval      = { id, tool, summary, lines: [str], status, message?, expires_at }
    Memory        = { id, text, kind: "preference"|"fact", pinned, created_at }   // P7
    Step (P7)     += products?: [{id, name}]  (search_products: what was shown, in order)
                  += orders?: [{id, status}]  (order tools)  — the source of the thread's facts
                    // status: pending | executing | done | failed | rejected | expired
    ThreadSummary = { id, title, updated_at }
    Thread        = { id, title, created_at, updated_at,
                      messages: [{ id, role, content, citations, steps, product_ids, created_at }] }

Status codes: 200 · 204 (delete) · 400 (validation) · 401 `UNAUTHENTICATED` (no, bad or expired token) · 403 `FORBIDDEN` (missing permission) · 404 (no such thread
*for this shopper*: missing and someone else's look the same) · 503 `UPSTREAM_UNAVAILABLE`
(LLM, Qdrant, embeddings or Postgres down — title `Assistant unavailable` or `Database busy`; the
underlying message is logged, never returned) · 500.

Response headers on `POST /v1/chat` (P1): `X-AI-LLM-Calls`, `X-AI-Input-Tokens`,
`X-AI-Output-Tokens`, `X-AI-Cost-USD` (left out when the price is unknown), so the Requests
panel shows AI cost the same way it shows `X-Query-Count`. `GET /health` → `{status: "ok"}`.

## 3. SSE events (AI → Browser), from P3

Each event is `event: <type>` then `data: <json>`, then a blank line. The browser uses `fetch` +
a stream reader (the request is a POST); `streamChat()` in Kirana's `api.js` parses it.

| Event | Data | UI does |
|---|---|---|
| `start` | `{thread_id}` | remember the thread (a new one is created by this turn) |
| `status` | `{tool, query, where}` | "Searching store policies: “query”" |
| `step` | `Step` (with `below_floor`) | (Requests panel) |
| `token` | `{text}` | append to the live answer |
| `reset` | `{}` | discard streamed text: it was a preamble to tool calls, not the answer |
| `citation` | `Citation` | source chip; sent just before `done` |
| `done` | `{thread_id, message_id, reply, steps, usage, product_ids}` | finalise; `usage` adds `first_token_ms`, `unverified_sources` |
| `error` | `ProblemDetail` (`status`, `code`) | show it with Retry: the HTTP status was already 200 when it failed |
| `products` (P4) | `{product_ids: [..]}` | fetch cards from Kirana |
| `approval_required` (P6 ✅) | `{approval_id, tool, summary, lines, expires_at}` | Confirm / Reject card (built from the server's copy) |
| (P7) | `approval_required` also for `remember_preference`: "Save to your memory", or "Update your memory" (Replace X with Y) when it replaces a saved one | Confirm saves it (and removes the replaced one) |

Closing the stream abandons the turn: the LLM call in flight is recorded in `llm_calls` as
"stream abandoned by the client" (it may still be billed), and no answer is saved.

## 4. Knowledge base admin (Browser → AI), from P2

| Method + path | Request → Response |
|---|---|
| `POST /v1/kb/documents/upload-url` | `KbUploadRequest` → `KbUploadTicket` (200), or 400 with field errors |
| `GET /v1/kb/documents` | → `[KbDocument]`, most recently changed first, deleted ones left out |
| `DELETE /v1/kb/documents/{id}` | → 204; 404 if unknown or already deleted |

    KbUploadRequest = { title (1–120), doc_type: "policy"|"faq"|"guide", file_name (1–200),
                        content_type: "application/pdf"|"text/markdown"|"text/plain",
                        size_bytes (1 .. 10 MB) }
    KbUploadTicket  = { document_id, object_key, upload_url, form_fields, expires_at }
    KbDocument      = { id, title, doc_type, file_name, content_type, size_bytes, status,
                        page_count, chunk_count, error, uploaded_by, created_at, updated_at }
    status          = pending | uploaded | indexing | ready | failed | deleting   (deleted is never listed)

The browser posts every entry of `form_fields`, then the file **last**, to `upload_url`
(Kirana's `uploadToStorage` does exactly this). `form_fields` holds `key`
(`{document_id}/{safe file name}`), `Content-Type`, the four `x-amz-meta-*` fields and the
signature; the signed policy pins all of them, so changing any one makes MinIO answer 403.
The title travels URL-encoded in `x-amz-meta-title`. There is no confirm call: MinIO's event
is the confirmation. Every KB route needs a token with `kb:write` (admins); the uploader is recorded as `uploaded_by`. A delete always removes the file; a `pending` document is deleted at once,
anything else becomes `deleting` until the worker has removed its chunks.

## 5. Events into the AI worker (Kafka)

Broker: Kirana's (`localhost:9094` from the laptop, `kafka:9092` from containers). Consumer
group `kirana-ai-ingest`, auto-commit off, offsets committed after processing (at-least-once).
Dead letters go to `<topic>-dlt`, as in Kirana.

**P2 — `kb.documents.v1`: produced by MinIO** (bucket notification on `kb-docs`, events
`s3:ObjectCreated:*` and `s3:ObjectRemoved:*`, MinIO's own S3-style event JSON).
- Object key: `{document_id}/{file_name}`. A delete event has no metadata, so the document
  id must come from the key.
- Our metadata travels in `Records[].s3.object.userMetadata`, set at upload as form fields
  pinned by the signed policy: `x-amz-meta-document-id`, `x-amz-meta-title`,
  `x-amz-meta-doc-type` (`policy | faq | guide`), `x-amz-meta-uploaded-by`. Validated before use.
- Topics `kb.documents.v1` and `kb.documents.v1-dlt` are created by the AI service (Kafka
  auto-create is off): `python -m kirana_ai.cli kafka-setup`.
- **Checked against a real event (Phase 2 M1):**
  - Kafka message key = `kb-docs/{document_id}/{file_name}` (bucket + object path, not encoded),
    so an upload and its delete share a partition and arrive in order.
  - `s3.object.key` inside the event is **URL-encoded** (`{document_id}%2F{file}`): decode it,
    or use the message key.
  - `userMetadata` keys arrive **canonicalised**: `X-Amz-Meta-Document-Id`, `X-Amz-Meta-Title`,
    `X-Amz-Meta-Doc-Type`, `X-Amz-Meta-Uploaded-By` (plus `content-type`). Read them case-insensitively.
  - `eventName` is `s3:ObjectCreated:Put` (or `:Post` for a browser POST upload) and
    `s3:ObjectRemoved:Delete`. A delete has no `userMetadata` and no `size`.

**P4 — `catalog.v1`: produced by Kirana's outbox** (key = product id, 3 partitions, created by
Kirana with `catalog.v1-dlt`), Kirana's envelope, the id field named after the aggregate:
```json
{ "eventId": "uuid", "type": "ProductUpserted", "occurredAt": "2026-10-04T10:15:00Z",
  "productId": 123,
  "data": { "name": "...", "description": "...", "category": "..." } }
```
Sent on every create and update (a price-only edit too: the consumer skips unchanged text by
hash) and written in the same transaction as the change. Consumer group `kirana-ai-catalog`.
`ProductDeleted` has the same envelope without `data` (also sent for a soft delete).
**Never price or stock.**

**Rules:** processing is idempotent (deterministic point ids, delete-then-upsert per
document, content-hash skip); `cli reindex` and `cli index-products` rebuild from the source
of truth if anything is ever lost.

## 6. Tools (AI → Kirana REST)

| Phase | Kirana endpoint | Used by |
|---|---|---|
| P4 | `GET /products/batch?ids=` → `[ProductSummary]` (new) | Product search hydration |
| P4 | `GET /products?page=&size=` (exists) | `make index-products` |
| P5 ✅ | `GET /orders`, `GET /orders/{id}` (user from the forwarded token; 404 if foreign) | `get_my_orders(status?)`, `get_order(order_id)`: offered only when the turn has a token; no identity parameter |
| P6 ✅ | `POST /auth/token-exchange` (HTTP Basic as client `kirana-ai`) | every Kirana call: `orders:read` for order tools; `orders:write` / `cart:write cart:read` only when an approval runs |
| P6 ✅ | `POST /orders/{id}/cancel`, `Idempotency-Key` = approval id | `cancel_order` after approval |
| P6 ✅ | `POST /cart/items`, one call per line, `Idempotency-Key` = approval id + `:` + product id; then `GET /cart` for the total | `add_to_cart` after approval |

Rules: timeouts on every call (P11); retries only on GETs and idempotency-keyed writes;
Kirana's ProblemDetail is passed to the LLM as a tool error, never raised to the user raw.
