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
- `docs/decisions.md`: every design decision so far (D1–D71), why, and its cost.
  These are settled. Do not reverse one without raising it with Ravi.
- `docs/api-contract.md`: the API the frontend expects. The backend must satisfy it.
- `backend/src/main/resources/db/migration/V1__init_schema.sql`: the schema.
- `docs/concepts-learned.md`: running list of concepts covered.
- `docs/flows.md`: every business flow end to end (transactions, network calls, tables,
  idempotency, failures). Keep it in sync when a flow changes.

## Stack and layout

    backend/    Spring Boot 4.0.x, Java 21, Maven, Hibernate 7, Flyway, Postgres 17, MinIO SDK
    frontend/   React + Vite (done; change only if the contract changes)
    infra/      docker-compose.yml: Postgres + MinIO (pgsty/minio fork) + Redis 8 (port 6380) + payment-mock (8090) + Toxiproxy (8474) + Kafka (9094) + Kafka UI (8085) + warehouse-mock (8091)
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
- **Users and access (AI Phase 5, D72-D73):** sign in with `POST /auth/login {email, password}`
  (bcrypt) → RS256 JWT with `role` and `scope` (permissions). Controllers get the caller only via
  `@CurrentUser Long userId`; protected endpoints carry `@RequiresPermission(Permission.X)`
  (401 no token, 403 missing permission). Roles are permission bundles (`entity.Role`): check
  permissions, never role names. There is no X-User-Id. Tests sign in with `TestAuth.as(id)` /
  `TestAuth.admin()`; scripts with `infra/perf/kirana_auth.py`. Demo password `kirana123`.
- **Transactions:** `@Transactional` belongs on service methods. `open-in-view` is off.
- **Images (D7):** three-step presigned **POST policy** flow: request policy, browser
  uploads to MinIO, confirm. The policy fixes the key, requires `image/*`, and caps
  size at `kirana.storage.max-upload-bytes`. The backend must add `key` and
  `Content-Type` to `formFields` (MinIO's signing call returns only the signature
  fields). On confirm, `statObject` records the real size. Read URLs are generated
  fresh, never stored.
- Keep changes small and reviewable: one milestone at a time, not everything at once.

## Stage 7 plan (current stage): idempotency at the API edge

Stages 1–6 are complete (D1–D68). Stage 6 (Kafka: outbox, webhooks, refund and fulfilment
consumers, dead-letter topics) is summarized in `docs/stage-6.md` ("Stage 6 wrap-up").
Internal flows already de-duplicate by their own ids (event ids, payment ids, order ids,
conditional updates). The gap is Kirana's own HTTP API: a client retry carries no identity.

- **P1** a retried "Place order" can't get its answer back (409 "Cart is empty"; the order exists).
- **P2** a retried "Add to cart" adds twice (`addOrIncrement`).
- **P3** a retried cancel gets 409 instead of the first result.

Steps, each reported to Ravi in `docs/stage-7.md` (what, why, new code, flow diagrams):

- **7a** ✅ Reproduce P1–P3: a script whose response is delayed on the way back (Toxiproxy in
  front of the API), so the client times out after the server did the work, then retries.
- **7b** ✅ Idempotency keys in Postgres: `Idempotency-Key` header **required** on
  `POST /cart/items`, `POST /orders`, `POST /orders/{id}/payment`, `POST /orders/{id}/cancel`
  (400 without it). Table `idempotency_keys` (user, key, endpoint, request hash, status,
  stored response). Saved in the same transaction as the change. Same key → stored response
  replayed; same key, different request → 422; first request still running → 409 + Retry-After.
  Keys kept 24 h, then cleaned up.
- **7c** ✅ Every client updated: frontend (one key per user action, reused on retry), perf
  scripts, tests, curl examples in docs and the API contract. No compatibility mode.
- **7d** ✅ Redis implementation behind a switch, for comparison: measure it, then break it
  (Redis down, crash between steps) to show why Postgres stays the source of truth.
- **7e** ✅ The full picture: Stage 6's consumer idempotency in the same framework; Kafka's
  idempotent producer and transactions (why exactly-once doesn't reach Postgres or a gateway);
  limits (key scope, retention, non-deterministic responses).

Decisions (Ravi): recommended set, except the key is required (no optional mode) and every
client is fixed (D69). Built: D70 (client retries), D71 (Postgres stays; Redis measured and broken).

**Stage 7 done** (summary and open problems: `docs/stage-7.md` §7e). Next: wrap up with Ravi and
plan Stage 8 (distributed locking), then replace this section with that plan.

## Do not jump ahead

Only the stage marked current above is in scope. Later stages are described in
`docs/roadmap.md`; read them to understand where the project is going, not to build them.

Do not add caching, locking, retries, messaging, indexes beyond the schema, or new
infrastructure before the stage that introduces it. Some gaps are deliberate: they
are the problems later stages solve.

## AI track (parallel, separate plan)

`kirana-ai/` is the AI assistant, a separate Python service with its own phases in
`kirana-ai/docs/AI-PLAN.md` and its own `kirana-ai/CLAUDE.md`. It runs in parallel with
the stages above and does not follow them.

The Kirana-side changes listed for each AI phase (section 5 of `AI-PLAN.md`: frontend
chat panel, the `ai` Postgres schema, product events, JWT login, and so on) are approved
by Ravi and are **not** "jumping ahead". Build them when that AI phase is current, and
keep them to what the phase lists. Everything else in this file still applies to them.

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