# API contract (Stage 1)

The contract between `frontend/` and `backend/`. The backend must satisfy it and
the frontend relies on it, so change both sides together.

## Image uploads go straight to MinIO (presigned POST policy)

    1. POST /products/{id}/images/upload-url        → backend signs a POST policy
    2. POST <bucket URL> (multipart form)            → browser → MinIO directly
    3. POST /products/{id}/images/{imageId}/confirm  → backend checks the object and records its real size

The policy is what makes the size limit real. The backend signs conditions and
MinIO rejects any upload that breaks them:

- `key` must equal the object key the backend chose
- `Content-Type` must start with `image/`
- size must be within `content-length-range` (for example 1 byte to 5 MB)
- the policy expires (for example after 10 minutes)

The browser also checks size before uploading, but that is only for user
experience. Tick "Skip the browser size check" in Manage to send an oversized
file anyway and watch MinIO answer `EntityTooLarge`.

Things that will bite you:

- **Form fields.** MinIO's signing call returns only the signature fields
  (`policy`, `x-amz-*`). The backend must add `key` and `Content-Type` to
  `formFields` before returning them. The UI posts every entry in `formFields`
  as-is, then appends the file last, as S3 requires.
- **CORS on MinIO.** Step 2 is cross-origin (5173 → 9000). MinIO allows all
  origins by default; if you see a CORS failure, set
  `MINIO_API_CORS_ALLOW_ORIGIN=http://localhost:5173` on the MinIO container.
- **Host in the upload URL.** Create the MinIO client with
  `http://localhost:9000` so the browser can resolve the URL. (This gets
  interesting in Stage 10, when the backend runs inside Docker.)

## API contract

Every endpoint that needs a user reads it from `Authorization: Bearer <token>` (AI Phase 5,
D72-D73). There is no other way: the `X-User-Id` header of Stages 1-7 is gone. No or bad token on
such an endpoint → 401; a token without the endpoint's permission → 403.

| Permission (`scope`) | SHOPPER | ADMIN | Endpoints |
|---|---|---|---|
| `shop` | ✅ | ✅ | `/cart/**`, `/orders/**` (payment, verify, cancel) |
| `chat` | ✅ | ✅ | the AI assistant (kirana-ai) |
| `catalog:write` | | ✅ | product create/update/delete, `/products/{id}/images/**`, `/products/{id}/inventory/**`, flash-sale create/delete |
| `users:read` | | ✅ | `GET /users` |
| `kb:write` | | ✅ | knowledge-base admin (kirana-ai) |
| `system` | | ✅ | `/system/**` (resilience lab) |
| public | | | `GET /products/**`, `GET /flash-sales`, `GET /products/{id}/flash-sale`, `GET /payments/providers`, `POST /users` (sign-up), `POST /auth/login`, `GET /.well-known/jwks.json`, webhooks |

Errors use RFC 7807 `ProblemDetail`:

    { "type": "...", "title": "Out of stock", "status": 409,
      "detail": "Only 1 unit of 'Tea' left, 3 requested",
      "errors": [ { "field": "price", "message": "must be greater than 0" } ] }

`errors` is optional and used for validation failures (400). The UI shows
field errors next to the matching form input.

Money is displayed as INR. The UI accepts a JSON number or a numeric string.

### Diagnostics headers (Stage 2, 4)

Every response carries `X-Query-Count` (SQL statements the request ran) and
`X-DB-Time-Ms` (time spent inside them). Requests that used the cache also carry
`X-Cache`: `HIT` (answered by Redis), `MISS` (loaded from Postgres), `BYPASS` (Redis
unavailable). The Requests panel shows all three.

### Idempotency-Key (Stage 7, required on the endpoints marked ★)

`POST /cart/items`, `POST /orders`, `POST /orders/{id}/payment` and `POST /orders/{id}/cancel`
require an `Idempotency-Key` header: a unique value (a UUID) per user action, sent again,
unchanged, when retrying that action. Keys belong to the signed-in shopper and are kept 24 h.
Follows the IETF draft *The Idempotency-Key HTTP Header Field*:

| Situation | Response |
|---|---|
| no key | 400 validation ProblemDetail, `errors: [{ field: "Idempotency-Key" }]` |
| first request with a key | handled normally; a success is stored with the key |
| same key, that request finished | the stored status, body and `Location`, plus `Idempotent-Replayed: true` |
| same key, that request still running | 409 `"Request in progress"`, `Retry-After: 1`: retry with the same key |
| same key, different endpoint or body | 422 `"Idempotency-Key reused"`: use a new key for a new action |

Failures are not stored: a request that failed (4xx or 5xx) without changing anything can be
retried with the same key and runs again. Retry with the same key only when the outcome is
unknown (no response, 409 in progress, 502/503/504).

### Rate limits (Stage 4)

`POST /orders` and cart writes are limited per signed-in shopper. Responses carry
`X-RateLimit-Remaining`. Over the limit: 429 ProblemDetail `"Too many requests"` with a
`Retry-After` header in seconds.

### Paged response (your own DTO, not Spring's `PageImpl`)

    { "content": [...], "page": 0, "size": 12, "totalElements": 40, "totalPages": 4 }

### Sign-in (AI Phase 5)

| Method | Path                   | Body                   | Returns |
|--------|------------------------|------------------------|---------|
| POST   | /auth/login            | `{ email, password }`  | `{ accessToken, tokenType: "Bearer", expiresAt, user: User, permissions: [scope] }`; 401 "Invalid email or password" for an unknown email or a wrong password alike |
| GET    | /auth/me               | — (token)              | `User` |
| GET    | /.well-known/jwks.json | —                      | `{ keys: [ { kty: "RSA", kid, alg: "RS256", use: "sig", n, e } ] }` |

The token is an RS256 JWT: `sub` (user id), `role`, `scope` (space-separated permissions), `iss` =
`kirana`, `aud` = `["kirana-api", "kirana-ai"]`, `exp` (1 h), `name`, header `kid`. Passwords are
bcrypt; seeded users have the demo password `kirana123`, user 1 is the ADMIN. The key is generated
at startup, so a restart invalidates every token (the UI goes back to sign-in). 401/403 bodies are
ProblemDetail with `code` `UNAUTHENTICATED` / `FORBIDDEN`.

### Users

| Method | Path   | Body              | Returns      |
|--------|--------|-------------------|--------------|
| GET    | /users | `?q=&limit=`      | `[User]` (admin) |
| POST   | /users | `{ name, email, password }` | `User` (201): public sign-up, always SHOPPER |

`User = { id, name, email, role }`

`GET /users` is a bounded search, newest first: `q` matches part of the name or email
(case-insensitive), `limit` defaults to 20 and is clamped to 1–50. Without `q` it returns
the newest shoppers. It never returns the whole table.

### Products

| Method | Path           | Body                           | Returns                |
|--------|----------------|--------------------------------|------------------------|
| GET    | /products      | `?page=&size=`                 | `Page<ProductSummary>` |
| GET    | /products/{id} |                                | `ProductDetail`        |
| POST   | /products      | `{ name, description, price, category? }` | `ProductDetail` (201)  |
| PUT    | /products/{id} | `{ name, description, price, version, category? }` | `ProductDetail` |
| DELETE | /products/{id} |                                | 204                    |

    ProductSummary = { id, name, price, stock, thumbnailUrl, category }
    ProductDetail  = { id, name, description, price, stock,
                       images: [Image], createdAt, updatedAt, version, category }

`category` (AI track, Phase 4 prep) is optional free text, at most 100 characters; blank means
none. The admin form offers a fixed list (`CATEGORIES` in `frontend/src/api.js`), and the AI's
product search filters on those exact values. PUT replaces it like the other fields.

`PUT /products/{id}` must include `version`: the one the client loaded (400 without it).
If someone saved the product since, the answer is 409 `"Product changed"`; reload and
edit again. `POST /products` ignores `version`.
    Image          = { id, url, contentType, sizeBytes, position }

The form sends `price` exactly as typed, as a string. Deciding how to parse and
validate it is your job.

### Images

| Method | Path                                    | Body                                   | Returns        |
|--------|-----------------------------------------|----------------------------------------|----------------|
| POST   | /products/{id}/images/upload-url        | `{ fileName, contentType, sizeBytes }` | `UploadTicket` |
| POST   | /products/{id}/images/{imageId}/confirm |                                        | `Image`        |
| DELETE | /products/{id}/images/{imageId}         |                                        | 204            |

    UploadTicket = { imageId, objectKey, expiresAt,
                     uploadUrl,   // bucket URL, e.g. http://localhost:9000/product-images
                     formFields } // { key, Content-Type, policy, x-amz-algorithm,
                                  //   x-amz-credential, x-amz-date, x-amz-signature }

Only confirmed images should appear in `ProductDetail.images`.
Soft-deleted products return 404 from every product endpoint and are left out of listings.

### Inventory

| Method | Path                                 | Body           | Returns     |
|--------|--------------------------------------|----------------|-------------|
| GET    | /products/{id}/inventory             |                | `Inventory` |
| PUT    | /products/{id}/inventory             | `{ quantity }` | `Inventory` |
| POST   | /products/{id}/inventory/adjustments | `{ delta }`    | `Inventory` |

`Inventory = { productId, quantity, updatedAt }`

### Flash-sale gate (Stage 4, admin)

| Method | Path                           | Returns          |
|--------|--------------------------------|------------------|
| GET    | /flash-sales                   | `[FlashSaleSummary]` (every armed sale; the Shop polls it) |
| GET    | /products/{id}/flash-sale      | `FlashSale`      |
| POST   | /products/{id}/flash-sale      | `FlashSale` (arms it, or re-syncs it from current stock) |
| DELETE | /products/{id}/flash-sale      | 204              |

`FlashSale = { productId, active, remaining }` (`remaining` is null when not active).
`FlashSaleSummary = { productId, name, price, remaining }` (`remaining` live from Redis).
While active, checkout refuses buyers once the gate's units run out, with the usual
409 "Out of stock".

### Checkout and payment (Stage 5, signed in)

| Method | Path                          | Body                                       | Returns            |
|--------|-------------------------------|--------------------------------------------|--------------------|
| POST ★ | /orders                       | optional `{ paymentProvider }`             | `Checkout` (201)   |
| POST ★ | /orders/{id}/payment          | optional `{ paymentProvider }`             | `PaymentSession`   |
| POST   | /orders/{id}/payment/verify   | `{ gatewayOrderId, paymentId, signature }` | `Order`            |
| POST ★ | /orders/{id}/cancel           |                                            | `Order`            |
| GET    | /payments/providers           |                                            | `[Provider]`       |

    Checkout       = { order: Order, payment: PaymentSession | null, paymentProblem: string | null }
    PaymentSession = { provider, orderId, gatewayOrderId, amountPaise, currency, keyId, checkoutUrl }
    Provider       = { id: "mock" | "razorpay", label, available, isDefault }

`POST /orders` holds the stock and returns an order with status `CREATED` (awaiting payment,
until `paymentDueAt`). If the gateway is unavailable, `payment` is null and `paymentProblem`
says so; pay later with `POST /orders/{id}/payment`. Verify accepts only a valid gateway
signature (400 otherwise). Statuses: `CREATED`, `PAID`, `CANCELLED`, `FAILED`
(`closedReason` says why). `latePaymentId` is set when money reached the gateway after the
order closed (Stage 6c): a refund is due, and the order stays closed. `refundStatus` follows that
refund (Stage 6d): `REQUESTED` → `PENDING` (at the gateway) → `PROCESSED`, or `FAILED` (needs a
person); null until the refund consumer has recorded it. `shipmentId` / `sentToWarehouseAt` are set
once a paid order has been handed to the warehouse (Stage 6e); a paid order without them is on its
way there. 503 `"Payment unavailable"` and 503 `"Checkout busy"` carry
`Retry-After`.

### Kafka in the lab (Stage 6f, dev)

`GET /system/status` → `kafka` also has `groups: [{ name, topic, state, members, lag }]` and
`deadLetters: [{ name, waiting }]`. `POST /system/kafka/dead-letters/{topic}/redrive` →
`{ topic, redriven }` (topic `orders.v1-dlt` or `payments.v1-dlt`; 404 otherwise, 409 if Kafka is off).

### Payment webhooks (Stage 6c, called by gateways, not the frontend)

`POST /webhooks/payment/{provider}`. Raw Razorpay-shaped event body; headers
`X-Razorpay-Signature` (HMAC-SHA256 of the raw body with the provider's webhook secret) and
`X-Razorpay-Event-Id`. 200 once recorded (also for duplicates and events Kirana doesn't use),
400 for a bad signature, 404 for an unknown provider.

### Resilience lab (Stage 5, dev)

`GET /system/status` (breaker states, checkout slots, payment-mock mode, network faults, Kafka reachability and topics),
`POST /system/breakers/{name}/reset`, `POST /system/chaos/payment` `{ mode, delayMs, failureRate }`,
`POST /system/chaos/network/{redis|minio|payment}` `{ fault: normal|latency|hang|down, latencyMs }`
(network faults need the `chaos` profile).

### Cart (signed in)

| Method | Path                    | Body                      | Returns |
|--------|-------------------------|---------------------------|---------|
| GET    | /cart                   |                           | `Cart`  |
| POST ★ | /cart/items             | `{ productId, quantity }` | `Cart`  |
| PUT    | /cart/items/{productId} | `{ quantity }`            | `Cart`  |
| DELETE | /cart/items/{productId} |                           | `Cart`  |

    Cart     = { items: [CartItem], total }
    CartItem = { productId, productName, unitPrice, quantity, lineTotal, thumbnailUrl }

`POST /cart/items` adds to the existing quantity if the product is already in the cart, which is
why it needs an `Idempotency-Key` (★): a retry with the same key adds nothing.

### Orders (signed in)

| Method | Path         | Body | Returns       |
|--------|--------------|------|---------------|
| POST ★ | /orders      |      | see Checkout above |
| GET    | /orders      |      | `[Order]`     |
| GET    | /orders/{id} |      | `Order`       |

    Order     = { id, status, total, createdAt, items: [OrderItem],
                  paymentProvider, paymentDueAt, paidAt, closedReason, latePaymentId, refundStatus,
                  shipmentId, sentToWarehouseAt }
    OrderItem = { productId, productName, unitPrice, quantity, lineTotal }

`GET /orders` returns items inline on purpose. It is the endpoint for the N+1 experiment.
