# CLAUDE.md

Context for any AI coding session in this repository. Read it fully before doing anything.

## What this project is

Kirana is an e-commerce platform built to learn backend **system design**: databases,
concurrency, caching, resilience, messaging, distributed systems, Docker, Kubernetes.
It starts as a Spring Boot monolith and evolves through 14 stages. Each new concept is
introduced only when the application has a real problem that motivates it.

## Roles (important)

- **Ravi makes the design decisions.** He is a senior backend engineer learning system
  design, not coding. Do not ask him to write code.
- **You write all the code**, then explain the design points in it.
- Before implementing anything with a real design choice, lay out the options and
  trade-offs briefly and ask Ravi to decide. Do not decide silently.
- Challenge his decisions when you disagree, with reasons. Once he decides, follow it.
- Before he runs an experiment, ask him to predict the outcome. Then explain what
  actually happened and why.
- Ravi configures and runs everything locally. Tell him exactly what to run and what
  output to expect.

## Read these first

- `docs/roadmap.md`: all 14 stages and the learning method. The destination, not a
  task list: only the current stage is planned in detail (below).
- `docs/decisions.md`: every design decision so far (D1–D13), why, and its cost.
  These are settled. Do not reverse one without raising it with Ravi.
- `docs/api-contract.md`: the API the frontend expects. The backend must satisfy it.
- `backend/src/main/resources/db/migration/V1__init_schema.sql`: the schema.
- `docs/concepts-learned.md`: running list of concepts covered.

## Stack and layout

    backend/    Spring Boot 4.0.x, Java 21, Maven, Hibernate 7, Flyway, Postgres 17, MinIO SDK
    frontend/   React + Vite (done; change only if the contract changes)
    infra/      docker-compose.yml: Postgres + MinIO (pgsty/minio fork)
    docs/       decisions, API contract, concepts learned

Packages are organized **by layer** (decision D10) under `com.kirana`: `controller`,
`service`, `repository`, `entity`, `dto`, `mapper`, `exception`, `storage`, `config`.
Each package has a `package-info.java` stating its rules. Follow them.

## Conventions

- **Spring Boot 4 specifics:** `spring-boot-starter-webmvc` (not `-web`),
  `spring-boot-starter-flyway`, Jackson 3, Hibernate 7, `@MockitoBean` (not `@MockBean`).
  Test slices need their own test starters (for example `spring-boot-starter-webmvc-test`).
  Check current artifact names instead of assuming Boot 3 ones.
- **Schema:** Flyway owns it, and Hibernate validates it (`ddl-auto=validate`). Schema
  changes are new migration files (`V2__...sql`), never edits to V1.
- **IDs:** each table has its own sequence, `INCREMENT BY 50`. Map with
  `@SequenceGenerator(sequenceName = "<table>_seq", allocationSize = 50)`.
- **Money:** `double` / `DOUBLE PRECISION` (D3, Ravi's choice). Don't switch unless he decides to.
- **Enums:** `@Enumerated(EnumType.STRING)`, never ORDINAL. The DB uses VARCHAR + CHECK.
- **Time:** `Instant` in Java, `timestamptz` in Postgres.
- **Soft delete:** products use `deleted_at` (D8). Deleted products return 404 and are
  excluded from listings.
- **API:** controllers return DTOs (records), never entities. Errors are RFC 7807
  `ProblemDetail` from one `@RestControllerAdvice`, with an `errors: [{field, message}]`
  array on validation failures. Paged responses use our own DTO:
  `{ content, page, size, totalElements, totalPages }`.
- **Users:** no auth yet. The shopper comes from the `X-User-Id` header.
- **Transactions:** `@Transactional` belongs on service methods. `open-in-view` is off.
- **Images (D7):** three-step presigned **POST policy** flow: request policy, browser
  uploads to MinIO, confirm. The policy fixes the key, requires `image/*`, and caps
  size at `kirana.storage.max-upload-bytes`. The backend must add `key` and
  `Content-Type` to `formFields` (MinIO's signing call returns only the signature
  fields). On confirm, `statObject` records the real size. Read URLs are generated
  fresh, never stored.
- Keep changes small and reviewable: one milestone at a time, not everything at once.

## Stage 1 plan (current stage)

Build in order. Stop after each milestone so Ravi can run it and discuss.

- **1a.** Entities for User, Product, ProductImage, Inventory. Product CRUD with soft
  delete. Creating a product creates its inventory row in the same transaction. Users
  endpoints. Validation and the ProblemDetail error format.
- **1b.** Image upload: POST policy, confirm, delete, read URLs.
- **1c.** Inventory endpoints: get, set, adjust.
- **1d.** Cart and CartItem endpoints.
- **1e.** Order creation, written **naively on purpose**: read stock, check, subtract,
  save. Mark it `// NAIVE: Stage 3 will break this`. Do not add locking; the race
  condition is the lesson of Stage 3.
- **Tests:** unit tests for pure logic, web-slice tests for validation and error shape,
  Testcontainers integration tests for the order flow (including rollback on out-of-stock).

### Stage 1 experiments (run with Ravi after 1e, predictions first)

1. A cart of 3 items where item 3 is out of stock: what happens to stock of items 1 and 2,
   with and without `@Transactional`?
2. Out-of-stock as a *checked* exception inside `@Transactional`: does it roll back?
3. Calling a `@Transactional` method from another method in the same class: is a
   transaction applied?
4. `GET /orders/{id}` mapping lazy `items` with open-in-view off: where does it fail, and
   what are the fixes?
5. `GET /orders` for a user with 10 orders: count SQL statements (N+1), then fix with
   `JOIN FETCH` or an entity graph and count again.

## Do not jump ahead

Only the stage marked current above is in scope. Later stages are described in
`docs/roadmap.md`; read them to understand where the project is going, not to build them.

Do not add caching, locking, retries, messaging, indexes beyond the schema, or new
infrastructure before the stage that introduces it. Some gaps are deliberate: they
are the problems later stages solve.

## After each milestone

- Tell Ravi what to run and what to expect (log lines, curl commands, UI behaviour).
- Explain the design points in the code in plain language.
- Add any new decision to `docs/decisions.md`, and any concept Ravi can now explain to
  `docs/concepts-learned.md`.
- Offer one or two senior-level interview questions on what was built.

## Finishing a stage and starting the next

When every milestone of the current stage works:

1. **Wrap up:** run the stage's experiments with Ravi, update `docs/concepts-learned.md`,
   and summarize what problems the current code now has. Those problems motivate the
   next stage.
2. **Plan the next stage together:** propose milestones and experiments for it, based on
   `docs/roadmap.md` and the problems just found. Ravi approves or changes the plan.
3. **Update this file:** replace the "Stage N plan" section with the approved plan for
   the new stage, and mark it current. Then start its first milestone.