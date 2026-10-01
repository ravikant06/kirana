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

All user-scoped endpoints read the shopper from the `X-User-Id` header.
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

### Rate limits (Stage 4)

`POST /orders` and cart writes are limited per shopper (`X-User-Id`). Responses carry
`X-RateLimit-Remaining`. Over the limit: 429 ProblemDetail `"Too many requests"` with a
`Retry-After` header in seconds.

### Paged response (your own DTO, not Spring's `PageImpl`)

    { "content": [...], "page": 0, "size": 12, "totalElements": 40, "totalPages": 4 }

### Users

| Method | Path   | Body              | Returns      |
|--------|--------|-------------------|--------------|
| GET    | /users | `?q=&limit=`      | `[User]`     |
| POST   | /users | `{ name, email }` | `User` (201) |

`User = { id, name, email }`

`GET /users` is a bounded search, newest first: `q` matches part of the name or email
(case-insensitive), `limit` defaults to 20 and is clamped to 1–50. Without `q` it returns
the newest shoppers. It never returns the whole table.

### Products

| Method | Path           | Body                           | Returns                |
|--------|----------------|--------------------------------|------------------------|
| GET    | /products      | `?page=&size=`                 | `Page<ProductSummary>` |
| GET    | /products/{id} |                                | `ProductDetail`        |
| POST   | /products      | `{ name, description, price }` | `ProductDetail` (201)  |
| PUT    | /products/{id} | `{ name, description, price, version }` | `ProductDetail` |
| DELETE | /products/{id} |                                | 204                    |

    ProductSummary = { id, name, price, stock, thumbnailUrl }
    ProductDetail  = { id, name, description, price, stock,
                       images: [Image], createdAt, updatedAt, version }

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

### Checkout and payment (Stage 5, needs X-User-Id)

| Method | Path                          | Body                                       | Returns            |
|--------|-------------------------------|--------------------------------------------|--------------------|
| POST   | /orders                       | optional `{ paymentProvider }`             | `Checkout` (201)   |
| POST   | /orders/{id}/payment          | optional `{ paymentProvider }`             | `PaymentSession`   |
| POST   | /orders/{id}/payment/verify   | `{ gatewayOrderId, paymentId, signature }` | `Order`            |
| POST   | /orders/{id}/cancel           |                                            | `Order`            |
| GET    | /payments/providers           |                                            | `[Provider]`       |

    Checkout       = { order: Order, payment: PaymentSession | null, paymentProblem: string | null }
    PaymentSession = { provider, orderId, gatewayOrderId, amountPaise, currency, keyId, checkoutUrl }
    Provider       = { id: "mock" | "razorpay", label, available, isDefault }

`POST /orders` holds the stock and returns an order with status `CREATED` (awaiting payment,
until `paymentDueAt`). If the gateway is unavailable, `payment` is null and `paymentProblem`
says so; pay later with `POST /orders/{id}/payment`. Verify accepts only a valid gateway
signature (400 otherwise). Statuses: `CREATED`, `PAID`, `CANCELLED`, `FAILED`
(`closedReason` says why). 503 `"Payment unavailable"` and 503 `"Checkout busy"` carry
`Retry-After`.

### Resilience lab (Stage 5, dev)

`GET /system/status` (breaker states, checkout slots, payment-mock mode, network faults, Kafka reachability and topics),
`POST /system/breakers/{name}/reset`, `POST /system/chaos/payment` `{ mode, delayMs, failureRate }`,
`POST /system/chaos/network/{redis|minio|payment}` `{ fault: normal|latency|hang|down, latencyMs }`
(network faults need the `chaos` profile).

### Cart (needs X-User-Id)

| Method | Path                    | Body                      | Returns |
|--------|-------------------------|---------------------------|---------|
| GET    | /cart                   |                           | `Cart`  |
| POST   | /cart/items             | `{ productId, quantity }` | `Cart`  |
| PUT    | /cart/items/{productId} | `{ quantity }`            | `Cart`  |
| DELETE | /cart/items/{productId} |                           | `Cart`  |

    Cart     = { items: [CartItem], total }
    CartItem = { productId, productName, unitPrice, quantity, lineTotal, thumbnailUrl }

`POST /cart/items` adds to the existing quantity if the product is already in the cart.

### Orders (needs X-User-Id)

| Method | Path         | Body | Returns       |
|--------|--------------|------|---------------|
| POST   | /orders      |      | `Order` (201) |
| GET    | /orders      |      | `[Order]`     |
| GET    | /orders/{id} |      | `Order`       |

    Order     = { id, status, total, createdAt, items: [OrderItem],
                  paymentProvider, paymentDueAt, paidAt, closedReason }
    OrderItem = { productId, productName, unitPrice, quantity, lineTotal }

`GET /orders` returns items inline on purpose. It is the endpoint for the N+1 experiment.
