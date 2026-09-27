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

### Diagnostics headers (Stage 2)

Every response carries `X-Query-Count` (SQL statements the request ran) and
`X-DB-Time-Ms` (time spent inside them). The Requests panel shows both.

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
| PUT    | /products/{id} | `{ name, description, price }` | `ProductDetail`        |
| DELETE | /products/{id} |                                | 204                    |

    ProductSummary = { id, name, price, stock, thumbnailUrl }
    ProductDetail  = { id, name, description, price, stock,
                       images: [Image], createdAt, updatedAt }
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

    Order     = { id, status, total, createdAt, items: [OrderItem] }
    OrderItem = { productId, productName, unitPrice, quantity, lineTotal }

`GET /orders` returns items inline on purpose. It is the endpoint for the N+1 experiment.
