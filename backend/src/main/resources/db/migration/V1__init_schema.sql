-- V1: initial schema (Stage 1). Flyway owns the schema; Hibernate only validates it.
--
-- Conventions
--   IDs      : one sequence per table, INCREMENT BY 50 to match Hibernate's default
--              pooled optimizer (@SequenceGenerator allocationSize = 50).
--   Time     : timestamptz everywhere (Instant in Java).
--   Money    : DOUBLE PRECISION, your call. Revisit when a total comes out wrong.
--   Enums    : VARCHAR + CHECK (not a Postgres ENUM type), mapped with EnumType.STRING.
--   Indexes  : only primary keys and uniqueness rules. Postgres does NOT index foreign
--              key columns automatically. That is deliberate: Stage 2 starts here.

-- ---------------------------------------------------------------- users
CREATE SEQUENCE users_seq INCREMENT BY 50;

CREATE TABLE users (
    id          BIGINT       PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    email       VARCHAR(255) NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL,
    updated_at  TIMESTAMPTZ  NOT NULL
);

-- Ravi@x.com and ravi@x.com are the same person.
CREATE UNIQUE INDEX uq_users_email_lower ON users (LOWER(email));

-- ---------------------------------------------------------------- products
CREATE SEQUENCE products_seq INCREMENT BY 50;

CREATE TABLE products (
    id           BIGINT           PRIMARY KEY,
    name         VARCHAR(200)     NOT NULL,
    description  TEXT,
    category     VARCHAR(100),
    price        DOUBLE PRECISION NOT NULL CHECK (price > 0),
    created_at   TIMESTAMPTZ      NOT NULL,
    updated_at   TIMESTAMPTZ      NOT NULL,
    deleted_at   TIMESTAMPTZ               -- soft delete: NULL = live
);

-- ---------------------------------------------------------------- product_images
-- Bucket name lives in config, not per row.
-- No URL column: read URLs are generated fresh when the response is built.
CREATE SEQUENCE product_images_seq INCREMENT BY 50;

CREATE TABLE product_images (
    id            BIGINT       PRIMARY KEY,
    product_id    BIGINT       NOT NULL REFERENCES products (id),
    object_key    VARCHAR(500) NOT NULL UNIQUE,   -- e.g. products/42/9f3c...jpg
    content_type  VARCHAR(100) NOT NULL,
    size_bytes    BIGINT,                         -- NULL until confirm; real size from statObject
    status        VARCHAR(20)  NOT NULL CHECK (status IN ('PENDING', 'ACTIVE')),
    position      INT          NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ  NOT NULL,          -- lets a future cleanup job find stale PENDING rows
    updated_at    TIMESTAMPTZ  NOT NULL
);

-- ---------------------------------------------------------------- inventory
-- Shared primary key with products (@MapsId). Created in the same transaction as
-- the product, so "product exists but no inventory row" cannot happen.
CREATE TABLE inventory (
    product_id  BIGINT      PRIMARY KEY REFERENCES products (id),
    quantity    INT         NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    updated_at  TIMESTAMPTZ NOT NULL
);

-- ---------------------------------------------------------------- carts
-- One cart per user, kept for the user's lifetime. Checkout deletes its items,
-- not the cart. Home for future cart-level fields (coupon, discount).
CREATE SEQUENCE carts_seq INCREMENT BY 50;

CREATE TABLE carts (
    id          BIGINT      PRIMARY KEY,
    user_id     BIGINT      NOT NULL UNIQUE REFERENCES users (id),
    created_at  TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL
);

-- ---------------------------------------------------------------- cart_items
-- No price column: the cart shows the current price. The snapshot happens at checkout.
CREATE SEQUENCE cart_items_seq INCREMENT BY 50;

CREATE TABLE cart_items (
    id          BIGINT      PRIMARY KEY,
    cart_id     BIGINT      NOT NULL REFERENCES carts (id) ON DELETE CASCADE,
    product_id  BIGINT      NOT NULL REFERENCES products (id),
    quantity    INT         NOT NULL CHECK (quantity > 0),
    created_at  TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_cart_items_cart_product UNIQUE (cart_id, product_id)
);

-- ---------------------------------------------------------------- orders
CREATE SEQUENCE orders_seq INCREMENT BY 50;

CREATE TABLE orders (
    id          BIGINT           PRIMARY KEY,
    user_id     BIGINT           NOT NULL REFERENCES users (id),
    status      VARCHAR(20)      NOT NULL
                CHECK (status IN ('CREATED', 'PAID', 'FAILED', 'CANCELLED')),
    total       DOUBLE PRECISION NOT NULL CHECK (total >= 0),  -- = sum of line_total, frozen
    created_at  TIMESTAMPTZ      NOT NULL,
    updated_at  TIMESTAMPTZ      NOT NULL
);

-- ---------------------------------------------------------------- order_items
-- Immutable snapshot of what was bought. No created_at: the order's timestamp covers it.
CREATE SEQUENCE order_items_seq INCREMENT BY 50;

CREATE TABLE order_items (
    id            BIGINT           PRIMARY KEY,
    order_id      BIGINT           NOT NULL REFERENCES orders (id),
    product_id    BIGINT           NOT NULL REFERENCES products (id),
    product_name  VARCHAR(200)     NOT NULL,   -- snapshot
    unit_price    DOUBLE PRECISION NOT NULL,   -- snapshot
    quantity      INT              NOT NULL CHECK (quantity > 0),
    line_total    DOUBLE PRECISION NOT NULL,   -- unit_price * quantity, frozen
    CONSTRAINT uq_order_items_order_product UNIQUE (order_id, product_id)
);
