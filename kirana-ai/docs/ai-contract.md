# ai-contract.md: Kirana ⇄ Kirana AI (v0.3)

The one and only copy (`kirana/kirana-ai/docs/`). Bump the version on every change.
**(Pn)** = added in phase n of `AI-PLAN.md`. Build only what the current phase needs.

## 0. Conventions

- The browser reaches the AI service through the Vite proxy: `/ai/*` → `http://localhost:8000/*`
  (the prefix is stripped, as with `/api`). No CORS on either service.
- Errors are RFC 7807 `ProblemDetail`, the same shape as Kirana:
  `{ type, title, status, detail, code?, errors? }`. `code` is a stable machine string
  (`RATE_LIMITED`, `UPSTREAM_UNAVAILABLE`, `UNAUTHENTICATED`, `BUDGET_EXCEEDED`).
- Ids are strings in JSON. Timestamps are ISO-8601 UTC.
- Money from Kirana is a JSON number today (D3). The AI service **never computes money**;
  it passes Kirana's values through, or shows none.

## 1. Identity

| Phase | Browser → AI | AI → Kirana |
|---|---|---|
| P1–P4 | `X-User-Id` (used **only** to scope threads; forgeable, like Kirana today) | none: tools call only public endpoints |
| P5+ | `Authorization: Bearer <Kirana JWT>` (RS256; the AI verifies it via Kirana's JWKS) | the same header, forwarded unchanged |

The request body and tool arguments **never** contain a user id.

## 2. Chat API (Browser → AI)

| Phase | Method + path | Request → Response |
|---|---|---|
| P1 | `POST /v1/chat` | `{thread_id?, message}` → `ChatReply` (JSON) |
| P3 | `POST /v1/chat` with `Accept: text/event-stream` | same request → SSE (section 3). JSON stays for evals and curl |
| P1 | `GET /v1/threads` | → `[ThreadSummary]` newest first, max 50 |
| P1 | `GET /v1/threads/{id}` | → `Thread` (404 if not the caller's) |
| P1 | `DELETE /v1/threads/{id}` | → 204 |
| P6 | `POST /v1/approvals/{approval_id}` | `{decision: "confirm" \| "reject"}` → SSE continuing the turn |
| P9 | `POST /v1/messages/{message_id}/feedback` | `{rating: "up" \| "down", comment?}` → 204 |

    ChatReply     = { thread_id, message_id, reply, citations: [Citation], steps: [Step],
                      usage: { llm_calls, input_tokens, output_tokens, cost_usd, latency_ms } }
    Citation      = { document_id, title, page?, chunk_id }
    Step          = { tool, arguments, result_count, latency_ms }     // shown in the Requests panel
    ThreadSummary = { id, title, updated_at }
    Thread        = { id, title, messages: [{ id, role, content, citations, steps, created_at }] }

Response headers (P1): `X-AI-LLM-Calls`, `X-AI-Tokens`, `X-AI-Cost-USD`, so the Requests
panel shows AI cost the same way it shows `X-Query-Count`.

## 3. SSE events (AI → Browser), from P3

Sent as `event: <type>` followed by `data: <json>`. The browser uses `fetch` + a stream reader (the request is a POST).

| Phase | Event | Data | UI does |
|---|---|---|---|
| P3 | `status` | `{text, tool?}` | "Searching policies…" line |
| P3 | `token` | `{text}` | Append the text |
| P3 | `citation` | `Citation` | Source chip under the answer |
| P3 | `done` | `{message_id, usage}` | Stop the indicator |
| P3 | `error` | `ProblemDetail` | Error with Retry |
| P4 | `products` | `{product_ids: [..]}` | Fetch `/api/products/batch` and render cards **from Kirana's data** |
| P6 | `approval_required` | `{approval_id, summary, lines?: [..], expires_at}` | Confirm/Reject card; the turn pauses |

## 4. Knowledge base admin (Browser → AI), from P2

| Method + path | Request → Response |
|---|---|
| `POST /v1/kb/documents/upload-url` | `{title, doc_type, file_name, content_type, size_bytes}` → `UploadTicket` (same shape as Kirana's images, D7) |
| `GET /v1/kb/documents` | → `[KbDocument]` |
| `DELETE /v1/kb/documents/{id}` | → 204 (removes the object; the delete event removes the vectors) |

    KbDocument = { id, title, doc_type, file_name, size_bytes, status, chunk_count, error?, updated_at }
    status     = PENDING | INDEXING | READY | FAILED | DELETED

Policy: key fixed to `kb-docs/{document_id}/{file_name}`; `Content-Type` in
`application/pdf | text/markdown | text/plain`; size capped (config). There is no confirm call:
the MinIO event confirms the upload.

## 5. Events into the AI worker

Queue: Redis list(s) on the queue Redis (AD3). The consumer uses `BLMOVE` to a processing list; dead-letter list `ai:dlq`.

**P2: Documents.** MinIO bucket notification (MinIO's own event JSON), bucket `kb-docs`,
events `s3:ObjectCreated:*` and `s3:ObjectRemoved:*`. The worker reads the document id from the key.

**P4: Products.** Pushed by Kirana **after commit**:

```json
{ "event_id": "uuid", "event_type": "catalog.product.upserted", "product_id": "123",
  "occurred_at": "2026-09-29T10:15:00Z",
  "payload": { "name": "...", "description": "...", "category": "..." } }
```

`catalog.product.deleted` has the same shape without `payload` (also sent for a soft delete).
**Never include price or stock.**

Rules: consumers are idempotent (deterministic point ids, content-hash skip);
`make reindex` and `make index-products` rebuild everything from the source of truth. From P12, Kafka replaces these lists and Kirana publishes through an outbox.

## 6. Tools (AI → Kirana REST)

| Phase | Kirana endpoint | Used by |
|---|---|---|
| P4 | `GET /products/batch?ids=` → `[ProductSummary]` (new) | Product search hydration |
| P4 | `GET /products?page=&size=` (exists) | `make index-products` |
| P5 | `GET /orders`, `GET /orders/{id}` (exist; user from the token; 404 if foreign) | Order questions |
| P6 | `POST /orders/{id}/cancel` + `Idempotency-Key` (new; `CREATED` only) | Cancel after approval |
| P6 | `POST /cart/items/batch` + `Idempotency-Key` (new) | Cart builder after approval |

Rules: timeouts on every call (P11); retries only on GETs and idempotency-keyed writes;
Kirana's ProblemDetail is passed to the LLM as a tool error, never raised to the user raw.
