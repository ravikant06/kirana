# Stage 6: Kafka — implementation log

One section per step: what was built, which problem it solves, the new code, and how a
request flows through it. Diagrams are Mermaid (GitHub renders them).

---

## 0. Where Stage 6 starts

Stage 5 made checkout correct for everything that happens **inside Kirana**: order status is a
state machine in Postgres, every change is a conditional `UPDATE`, and two jobs (reconciler,
expiry) settle every unpaid order. What is still wrong involves **money that moves after we stop
looking**:

| Gap | User story | Today |
|---|---|---|
| **G1** | Bala pays at 10:09:58, the capture lands at 10:10:03, expiry closed the order at 10:10:01, his tab is closed | Order `FAILED`, money taken, **nothing notices** (the reconciler only looks at `CREATED` orders) |
| **G2** | Same, but the browser reports it | 409 and a `REFUND NEEDED` log line; **nobody refunds** |
| **G3** | The gateway answers "unknown order" (mock restarted, wrong keys) | Expiry treats it as "not paid" and closes it |
| **Fulfilment** | A paid order should go to the warehouse | Nothing happens; adding it naively is a **dual write** |

Kafka is not the fix on its own. The fixes are webhooks (6c), a transactional outbox (6b) and
consumers (6d, 6e); Kafka is how events reach several independent consumers durably and in
order (6a, 6f).

---

## 6a. Kafka infrastructure

**Done:** a Kafka broker and Kafka UI run in Docker Compose; the backend connects with explicit
producer settings, creates its first topic (`orders.v1`, 3 partitions) at startup, reports Kafka
in the Resilience lab, and has a Kafka test container.

**Problem solved:** none for users yet. This is the plumbing every later step stands on. The
checkout flow is unchanged.

### What runs where

```mermaid
flowchart LR
    subgraph Host["Your laptop"]
        BE["Backend (Spring Boot)<br/>KafkaTemplate · KafkaAdmin"]
        BR["Browser"]
    end
    subgraph Docker["docker compose"]
        K["Kafka broker<br/>apache/kafka 4.1.2 · KRaft<br/>listeners: kafka:9092 (containers)<br/>localhost:9094 (host)"]
        UI["Kafka UI<br/>localhost:8085"]
    end
    BE -- "localhost:9094<br/>create topics, produce" --> K
    UI -- "kafka:9092<br/>read topics, offsets, lag" --> K
    BR -- "http://localhost:8085" --> UI
    BR -- "GET /api/system/status" --> BE
```

* **KRaft**: the broker runs its own controller (metadata quorum); no ZooKeeper.
* **Two listeners**: other containers reach the broker as `kafka:9092`; the backend on the host
  uses `localhost:9094`. A broker tells clients which address to reconnect to (the "advertised
  listener"), so each side needs its own.
* **Automatic topic creation is off**: a typo in a topic name fails instead of creating a new
  topic nobody reads.

### Startup: the backend creates its topics

```mermaid
sequenceDiagram
    participant App as Backend startup
    participant Admin as KafkaAdmin (Spring)
    participant K as Kafka broker
    App->>Admin: finds NewTopic beans (KafkaConfig)
    Admin->>K: create orders.v1, 3 partitions, 1 replica (if missing)
    K-->>Admin: created / already exists
    Note over App,K: KafkaConfig is active only when kirana.kafka.enabled=true
```

### How a record finds its partition

```mermaid
flowchart LR
    P["producer.send(topic=orders.v1,<br/>key=order-42, value=…)"] --> H["partition = hash(key) mod 3"]
    H --> P0["partition 0"]
    H --> P1["partition 1: every record with key order-42"]
    H --> P2["partition 2"]
```

Same key → same partition → read back in the order written. Different keys spread across
partitions, which is what lets several consumers share the work later (6f). There is **no order
across partitions**, only within one.

### New code

| File | What it does |
|---|---|
| `infra/docker-compose.yml` | `kafka` (KRaft, one node) and `kafka-ui` (port 8085) |
| `backend/pom.xml` | `spring-boot-starter-kafka`; test: `testcontainers-kafka` |
| `application.yml` → `spring.kafka.*` | bootstrap `localhost:9094`; producer `acks=all`, idempotent, 10 s delivery timeout |
| `application.yml` → `kirana.kafka.*` | `enabled` switch, partition count |
| `messaging/Topics.java` | topic names (`orders.v1`) |
| `messaging/KafkaConfig.java` | `NewTopic` beans, so the app owns its topics |
| `messaging/KafkaStatus.java` | reachability and topics for `/system/status` |
| `controller/SystemController`, `dto/SystemStatus` | `kafka` added to the status |
| `frontend/.../ResilienceLab.jsx` | Kafka card: connected, topics, link to Kafka UI |
| `src/test/resources/application.properties` | tests run with Kafka off by default |
| `KafkaContainerConfig`, `KafkaSmokeIntegrationTest` | real broker in tests; topic created; same key → same partition, in order |

### Producer settings, in plain English

| Setting | Value | Meaning |
|---|---|---|
| `acks` | `all` | the broker confirms a write only once it is stored on every in-sync copy |
| `enable.idempotence` | `true` | if a send is retried after a lost acknowledgement, the broker drops the duplicate |
| `delivery.timeout.ms` | 10 s | give up on a send after 10 s (default: 2 minutes) |
| `max.block.ms` | 5 s | don't block a caller more than 5 s when the broker is unreachable |

### Try it

```sh
cd infra && docker compose up -d kafka kafka-ui
cd ../backend && mvn spring-boot:run
```

* Kafka UI → http://localhost:8085 → cluster **kirana** → Topics: `orders.v1`, 3 partitions.
* Manage → Resilience lab: **Kafka · Connected**, `orders.v1 (3 partitions)`.
* In Kafka UI, open `orders.v1` → **Produce message**, key `order-42`, any value, three times:
  all three land in the same partition (Messages tab shows the partition and offset).

---

## 6b. Transactional outbox

**Done:** every order event (`OrderPlaced`, `OrderPaid`, `OrderClosed`, `PaymentAfterClose`) is
now written to an **`outbox` table in the same database transaction** as the order change. A
**relay** publishes waiting rows to Kafka topic `orders.v1` every half second, in order, keyed
by order id.

**Problem solved: the dual write.** A change in Postgres plus a message to another system can't
share one transaction, so one can happen without the other:

| Naive way | What can go wrong |
|---|---|
| Send to Kafka, then commit the order | the commit fails → Kafka says "order 42 paid", but it isn't (a consumer would ship an unpaid order) |
| Commit the order, then send to Kafka | the app crashes in between → "order 42 paid" is **never** announced (no shipping, no refund) |
| Stage 5 (in-memory event after commit) | the same as above: lost on a crash |

With the outbox, the event is a **row** next to the order row, so they commit together or not
at all. Delivering it to Kafka becomes a separate, retryable job.

**User-visible today:** still nothing (no consumers yet). It's the guarantee that refunds (6d)
and fulfilment (6e) will rely on: *if an order changed, the event about it will reach Kafka.*

### Before and after

```mermaid
flowchart LR
    subgraph Before["Stage 5"]
        A1["CheckoutSaga<br/>order → PAID<br/>(transaction)"] --> A2["COMMIT"]
        A2 --> A3["publish OrderPaid<br/>in memory → log line"]
        A3 -.->|"app crashes here:<br/>event lost"| X1["✗"]
    end
    subgraph After["Stage 6b"]
        B1["CheckoutSaga<br/>order → PAID<br/>+ INSERT outbox row<br/>(same transaction)"] --> B2["COMMIT<br/>both rows, or neither"]
        B2 --> B3["OutboxRelay (every 0.5 s)<br/>reads waiting rows"]
        B3 --> B4["Kafka orders.v1<br/>key = order id"]
        B4 --> B5["mark row published"]
    end
```

### One event, step by step (shopper pays)

```mermaid
sequenceDiagram
    participant B as Browser
    participant S as CheckoutSaga
    participant DB as Postgres
    participant R as OutboxRelay
    participant K as Kafka (orders.v1)
    B->>S: POST /orders/{id}/payment/verify
    S->>DB: BEGIN
    S->>DB: UPDATE orders SET status='PAID' WHERE status='CREATED'
    S->>DB: INSERT INTO outbox (OrderPaid, key=order id)
    S->>DB: COMMIT (both rows together)
    S-->>B: 200 {status: PAID}
    Note over R: up to 0.5 s later
    R->>DB: BEGIN; SELECT … WHERE published_at IS NULL<br/>ORDER BY id FOR UPDATE SKIP LOCKED
    R->>K: send(key=order id, value=JSON, headers event-id, event-type)
    K-->>R: acknowledged (acks=all)
    R->>DB: UPDATE outbox SET published_at = now(); COMMIT
```

The shopper's request ends at the COMMIT. Kafka is never on the request's path, so a Kafka
outage does not slow or fail checkout.

### What happens when something fails

| Failure | Result | Why |
|---|---|---|
| Order transaction rolls back (e.g. out of stock) | no event | the outbox row rolled back with it |
| App crashes after COMMIT, before the relay runs | event sent after restart | the row is in Postgres, still unpublished |
| Kafka down | events wait; checkout unaffected; lab shows "N waiting" | relay retries every 0.5 s |
| Relay crashes after Kafka acknowledged, before marking the row | the event is sent **twice** | **at-least-once**: consumers must ignore an `eventId` they've seen (6c) |
| One event can't be sent | it and everything after it wait | keeps per-order order (no "Closed" before "Placed") |
| Two backend instances | each relay takes different rows | `FOR UPDATE SKIP LOCKED` skips rows another relay has locked |

### New and changed code

| File | What |
|---|---|
| `db/migration/V5__outbox.sql` | `outbox` table (`event_id` unique, `topic`, `message_key`, `event_type`, `payload`, `published_at`, `attempts`, `last_error`) + index on unpublished rows |
| `entity/OutboxMessage.java`, `repository/OutboxRepository.java` | the row, inserted through JPA |
| `service/OutboxOrderEvents.java` | **new `OrderEvents` implementation**: INSERT into the outbox, joining the caller's transaction (replaces Stage 5's in-memory `SpringOrderEvents`, now deleted). `CheckoutSaga` did not change. |
| `service/OrderService.java` | `OrderPlaced` now published **inside TX1** (it used to be published after the commit, which the outbox would not have protected) |
| `messaging/OutboxRelay.java` | the polling publisher: lock, send, wait for the acknowledgement, mark; stop at the first failure |
| `messaging/OutboxStats.java` | waiting count, age of the oldest, last error → `/system/status`, lab |
| `application.yml` | `kirana.outbox.*` (interval, batch); **scheduler pool 3 threads** (the default single thread would let the relay and the payment jobs block each other) |
| `OutboxIntegrationTest` | a rolled-back checkout leaves no event; events arrive in Kafka in order with their ids |

### Message format (topic `orders.v1`)

```
key:     "50050852"                                  ← order id: all of one order's events share a partition
headers: event-id = c67e5368-…, event-type = OrderPaid
value:   {"eventId":"c67e5368-…","type":"OrderPaid","occurredAt":"2026-10-01T20:39:23Z",
          "orderId":50050852,"data":{"orderId":50050852,"provider":"mock","paymentId":"pay_…"}}
```

### Experiment (run on the real stack)

Kafka stopped → order placed and paid → the lab shows **2 waiting** → backend killed with
`kill -9` → Kafka started → backend started → `Outbox relay: published 2 event(s)` → Kafka holds
`OrderPlaced` then `OrderPaid` for order 50050852, both on partition 1, in order.

### Try it

1. `docker stop kirana-kafka-1`, then place and pay an order in the UI. It works normally.
   Lab → Kafka card: **Unreachable**, **Outbox: 2 events waiting**.
2. Stop the backend (Ctrl+C), `docker start kirana-kafka-1`, start the backend again.
   The log shows `Outbox relay: published 2 event(s)`; the lab shows 0 waiting.
3. Kafka UI → `orders.v1` → Messages: your order's events, same partition, in order.

### Known costs

* The relay holds a database transaction while it waits for Kafka (at most a few seconds), on a
  scheduler thread. At larger scale: claim rows with a lease and publish outside the
  transaction, or use change data capture (Debezium reading Postgres's write-ahead log).
* Published rows are never deleted yet; a cleanup job comes later.
* At-least-once delivery: duplicates are possible, so every consumer must deduplicate.

---

## 6c. Late-payment detection (webhooks, the first consumer)

**Done:** payment-mock now **pushes** signed webhooks (`payment.captured` / `payment.failed`)
and retries them until Kirana answers 200. Kirana checks the signature, writes the event to the
outbox on a new topic **`payments.v1`**, and the **first Kafka consumer** applies it to the
order. A payment for an order that already closed is recorded as a **late payment** (refund due),
once. Two smaller fixes go with it: a re-check job for closed orders, and "unknown" no longer
closes an order.

**Problems solved:**

| Gap | Before | Now |
|---|---|---|
| Tab closed after paying | order stays "Awaiting payment" until the reconciler runs (1–1.5 min) | **PAID in ~0.5 s** via webhook |
| **G1**: captured after expiry/cancel, nobody reports it | order `FAILED`, money taken, **silence** | webhook (or the re-check job) → `latePaymentId` set, `PaymentAfterClose` raised **once**, "Refund due" on the Orders page |
| **G3**: gateway says "unknown order" at expiry | closed as unpaid | held 5 min at a time, up to 1 h, then closed with an error log; re-check still watches |

### Three ways a payment reaches Kirana now

```mermaid
flowchart LR
    G["Gateway<br/>(payment-mock / Razorpay)"]
    B["Browser<br/>(tab open)"]
    G -- "1. signed result<br/>(postMessage)" --> B
    B -- "POST /orders/{id}/payment/verify" --> AP
    G -- "2. webhook, signed,<br/>retried until 200" --> W["WebhookController<br/>→ PaymentWebhooks"]
    W -- "INSERT outbox<br/>(payments.v1)" --> DB[("Postgres")]
    DB -- "OutboxRelay" --> K["Kafka<br/>payments.v1"]
    K -- "PaymentEventsListener<br/>group kirana-payments" --> AP
    J["PaymentJobs<br/>reconcile (CREATED)<br/>re-check (CLOSED)"] -- "3. poll: fetchStatus" --> G
    J --> AP
    AP["CheckoutSaga.applyPayment<br/>conditional UPDATEs"]
    AP --> R1["CREATED → PAID<br/>+ OrderPaid"]
    AP --> R2["already PAID<br/>→ nothing"]
    AP --> R3["CANCELLED/FAILED<br/>→ late_payment_id (once)<br/>+ PaymentAfterClose"]
```

All three paths end in **one method**, `applyPayment`, and every branch in it is a
conditional `UPDATE`. So it doesn't matter which path arrives first or how many times each one
fires.

### Use case: Asha pays, closes the tab

```mermaid
sequenceDiagram
    participant A as Asha's browser
    participant G as payment-mock
    participant W as Kirana webhook endpoint
    participant DB as Postgres
    participant R as OutboxRelay
    participant K as Kafka payments.v1
    participant L as PaymentEventsListener
    A->>G: pays on the checkout page
    A--xA: closes the tab (verify never sent)
    G->>W: POST /webhooks/payment/mock<br/>X-Razorpay-Signature, X-Razorpay-Event-Id
    W->>W: HMAC(raw body, webhook secret) matches?
    W->>DB: find order by gateway order id
    W->>DB: INSERT outbox (PaymentCaptured, key = order id,<br/>event_id = UUID from gateway event id); COMMIT
    W-->>G: 200 (gateway stops retrying)
    R->>K: send (≤ 0.5 s later)
    K->>L: deliver
    L->>DB: BEGIN
    L->>DB: INSERT processed_events (kirana-payments, event_id)<br/>ON CONFLICT DO NOTHING → 1 row = first time
    L->>DB: UPDATE orders SET status='PAID' WHERE status='CREATED'
    L->>DB: INSERT outbox (OrderPaid → orders.v1)
    L->>DB: COMMIT
    L->>K: commit offset (ack)
    Note over A: Orders page refreshes every 5 s while<br/>an order awaits payment → "Paid"
```

Measured on the real stack: **0.50 s** from payment to `PAID`, with no verify call.

### Use case: Bala's payment lands after the order closed (G1)

```mermaid
sequenceDiagram
    participant E as Expiry job / Bala cancels
    participant G as payment-mock
    participant L as PaymentEventsListener
    participant DB as Postgres
    E->>DB: CREATED → FAILED/CANCELLED, stock released
    G->>G: capture completes anyway
    G->>L: webhook → outbox → Kafka (as above)
    L->>DB: UPDATE … SET status='PAID' WHERE status='CREATED' → 0 rows
    L->>DB: UPDATE … SET late_payment_id=? WHERE late_payment_id IS NULL<br/>AND status IN (CANCELLED, FAILED) → 1 row
    L->>DB: INSERT outbox PaymentAfterClose (→ orders.v1, for 6d's refund consumer)
    Note over DB: the order stays CANCELLED/FAILED;<br/>Orders page shows "Refund due"
```

If the webhook never comes (disabled, misconfigured, lost), the **re-check job** asks the
gateway about every order closed in the last 30 minutes, every 2 minutes, and reaches the same
`applyPayment`.

### Duplicates: three layers

| Where | Catches | How |
|---|---|---|
| Webhook endpoint | the gateway retrying or sending the same event twice | outbox `event_id` = UUID derived from the gateway's event id; second insert hits `UNIQUE` → "duplicate ignored", still 200 |
| Consumer (inbox) | Kafka redelivering a record (app died after the DB commit, before the offset commit; relay sent twice) | `processed_events (consumer, event_id)` row in the same transaction as the work |
| The order itself | the same payment reported by browser + webhook + re-check | conditional `UPDATE`s: only the first gets 1 row |

For payments, layer 3 alone would already be enough. The inbox is there because 6d (refund API
call) and 6e (warehouse call) have effects that are **not** naturally idempotent.

### G3: unknown is not "not paid"

```mermaid
flowchart TD
    X["Expiry: order past due"] --> F{"fetchStatus"}
    F -->|PAID| P["applyPayment → PAID"]
    F -->|"PENDING<br/>(gateway knows it, nothing captured)"| C["close FAILED, release stock<br/>(re-check watches 30 min)"]
    F -->|"UNKNOWN<br/>(gateway has no record)"| U{"created + window + 1 h<br/>passed?"}
    U -->|no| H["postpone due by 5 min<br/>stock stays held, WARN"]
    U -->|yes| C2["close FAILED, ERROR log<br/>(re-check watches 30 min)"]
    F -->|"gateway down"| D["leave it for the next run (Stage 5)"]
```

Postponing `payment_due_at` also sends the order to the back of the expiry queue, so a pile of
UNKNOWN orders can't starve the others out of the 50-order batch.

### New and changed code

| File | What |
|---|---|
| `payment-mock/app.py` | webhook sender: HMAC-SHA256 of the raw body with `WEBHOOK_SECRET`, event id header, retries after 1, 2, 4, 8, 16, 32 s; `/admin/webhooks` (`enabled`, `duplicate`, `url`), `/admin/webhooks/deliveries` |
| `infra/docker-compose.yml` | `WEBHOOK_URL` (→ `host.docker.internal:8080`), `WEBHOOK_SECRET`, `extra_hosts` |
| `db/migration/V6__payment_events.sql` | `orders.late_payment_id`; `processed_events (consumer, event_id)` |
| `controller/WebhookController.java` | `POST /webhooks/payment/{provider}`, body as the raw string (re-serialising would break the signature) |
| `service/PaymentWebhooks.java` | verify → find order → outbox row on `payments.v1`; duplicate → 200 |
| `service/OutboxWriter.java` | the envelope + insert, shared by order events and payment events (`OutboxOrderEvents` now uses it) |
| `messaging/PaymentEventsListener.java` | **first consumer**: `@KafkaListener`, group `kirana-payments`, inbox + `applyPayment` in one transaction, then `ack` |
| `messaging/Inbox.java` | `INSERT … ON CONFLICT DO NOTHING` → first delivery or not |
| `messaging/KafkaConfig.java`, `Topics.java` | `payments.v1` (3 partitions); `DefaultErrorHandler`: 3 retries 1 s apart, then log and skip (DLQ in 6f) |
| `application.yml` | consumer: `enable-auto-commit: false`, `auto-offset-reset: earliest`, `ack-mode: manual`; `recheck-closed-for`, `unknown-grace`, mock `webhook-secret`; scheduler pool 4 |
| `service/CheckoutSaga.java` | `applyPayment` → `PAID / ALREADY_PAID / PAID_AFTER_CLOSE` (never throws; `confirmPayment` wraps it for the browser); `recheckClosed`; `expire` handles UNKNOWN |
| `service/PaymentJobs.java` | `recheckClosed()` every 2 min |
| `repository/OrderRepository.java` | `markLatePayment`, `postponeDue`, `findRecentlyClosedIds`, `findByPaymentProviderAndGatewayOrderId` |
| `payment/*` | `verifyWebhook` on the gateway port; `webhookSecret` per gateway |
| `entity/Order`, `OrderResponse`, `OrderMapper` | `latePaymentId` |
| `frontend/…/Orders.jsx` | "Refund due" line; refresh every 5 s while an order awaits payment |
| Tests | `PaymentWebhookIntegrationTest` (webhook → Kafka → PAID; late payment with a Kafka redelivery → one refund event; inbox); saga tests for UNKNOWN, late payment reported 4 ways, re-check after expiry. **82 tests pass.** |

### Message format (topic `payments.v1`)

```
key:     "50050902"                       ← our order id (the endpoint looked it up)
headers: event-id = f9ad4b05-6903-3568-…  (name-based UUID of the gateway's evt_…), event-type = PaymentCaptured
value:   {"eventId":"f9ad4b05-…","type":"PaymentCaptured","occurredAt":"…","orderId":50050902,
          "data":{"provider":"mock","gatewayEventId":"evt_…","gatewayOrderId":"order_MBCZ…",
                  "paymentId":"pay_1dzg…","status":"captured"}}
```

### Experiments (run on the real stack)

| # | Setup | Result |
|---|---|---|
| 1 | pay, never call verify (tab closed) | `PAID` after **0.50 s**; log: `Webhook from mock … queued` → `payments.v1 p0@0: PaymentCaptured … -> order PAID` |
| 2 | cancel, then pay; mock `duplicate: true` | 2 deliveries: 1 `queued`, 1 `duplicate ignored`; order stays `CANCELLED`, `latePaymentId` set; outbox for the order: OrderPlaced 1, OrderClosed 1, PaymentCaptured 1, **PaymentAfterClose 1** |
| 3 | `kill -9` the backend, pay, restart it | mock: attempts 1–4 `URLError`, attempt 5 → 200 (webhook retries bridged the outage). Then a surprise: the order became `PAID` **27 s after** the webhook was stored. The killed instance still owned the partitions until the broker's session timeout (45 s) ran out; then the group rebalanced and the event was processed. **Late, not lost.** (6f looks at this properly.) |

### Try it

Restart your backend first (it applies V6 and starts the consumer; the log shows
`kirana-payments: partitions assigned: [payments.v1-0, payments.v1-1, payments.v1-2]`).

1. **Tab closed:** place an order with the test gateway, pay, and close the payment window
   *before* the "paid" message (or pay in the gateway page opened directly at
   `http://localhost:8090/checkout/<gatewayOrderId>`). Orders page → **Paid** within ~5 s.
2. **Late payment:** place an order, open its payment window, cancel the order from another tab,
   then pay. Orders → Cancelled with **"Refund due"**; log `REFUND NEEDED`; Kafka UI → `orders.v1`
   → `PaymentAfterClose`.
3. **Duplicates:** `curl -XPOST localhost:8090/admin/webhooks -H 'Content-Type: application/json' -d '{"duplicate":true}'`,
   pay an order, and watch the log for `duplicate ignored`. See what the gateway did:
   `curl localhost:8090/admin/webhooks/deliveries`.
4. **No webhooks** (`{"enabled":false}`): it still works, now via the reconciler (≤ 1.5 min) or the
   re-check job (≤ 2 min) — the polling safety net.
5. Kafka UI → Consumers → `kirana-payments`: committed offsets and lag per partition.

### Known costs

* One more hop: webhook → outbox → relay → Kafka → consumer adds ~0.5 s (relay interval).
* A record that keeps failing is retried 3 times and then **skipped** (logged only). The
  re-check job would still catch a skipped capture; a dead-letter topic comes in 6f.
* The re-check job asks the gateway about every closed order up to 15 times in 30 min. Fine at
  this scale; with webhooks reliable, it could run far less often.
* Real Razorpay webhooks need a public URL (a tunnel such as ngrok) and
  `RAZORPAY_WEBHOOK_SECRET`; the endpoint already supports `/webhooks/payment/razorpay`.
* Refunds are still only an event (`PaymentAfterClose`); 6d makes something act on it.

---

## 6d. Refunds (G2)

**Done:** a late payment is now actually refunded. A second consumer, `RefundListener` (its own
group, `kirana-refunds`), reads `orders.v1`, picks out `PaymentAfterClose`, records a refund and
asks the gateway to refund the payment, exactly once. The gateway finishes refunds later and says
so by webhook, which takes the 6c path. The Orders page goes **Refund requested → Refund in
progress → Refunded**.

Also in this step (D61): the reconciler now waits **10 min** for gateways that send webhooks and
keeps **1 min** for gateways that don't (Razorpay without a tunnel).

**Problem solved (G2):** "REFUND NEEDED" used to be a log line. Now it's a refund. The hard part
isn't calling the API, it's calling it **exactly once**:

| Trap | Why it's dangerous | Defence |
|---|---|---|
| The event arrives twice (at-least-once) | two refunds | inbox (`processed_events`) + `refunds.payment_id UNIQUE` |
| Consumer, retry job (and later several instances) act at the same moment | two refunds | **lease**: `UPDATE … SET claimed_until = now+30s WHERE status='REQUESTED' AND claimed_until < now`; only the one that gets 1 row calls the gateway |
| The refund call **times out after the gateway refunded** | "failed, retry" → two refunds | never retry the create; the next attempt **asks first** (`GET /payments/{id}/refunds`) and adopts what it finds |
| The gateway is down | refund lost, or the partition blocked by retries | the refund is a row (`REQUESTED`) before anything is called; a job retries; the consumer has already moved on |
| The refund webhook is lost | stuck "in progress" | job polls `PENDING` refunds older than 1 min |

### The refund's states

```mermaid
stateDiagram-v2
    [*] --> REQUESTED: RefundListener<br/>(PaymentAfterClose)
    REQUESTED --> REQUESTED: gateway down / timeout<br/>(lease runs out, job retries)
    REQUESTED --> PENDING: gateway created it<br/>(or ask-first found it)
    REQUESTED --> PROCESSED: refund.processed webhook<br/>arrives first (timeout case)
    REQUESTED --> FAILED: gateway refused (4xx)
    PENDING --> PROCESSED: refund.processed webhook<br/>or polling
    PENDING --> FAILED: refund.failed
    PROCESSED --> [*]
    FAILED --> [*]: a person looks
```

### Use case: Bala gets his money back

```mermaid
sequenceDiagram
    participant P as PaymentEventsListener (6c)
    participant K1 as Kafka orders.v1
    participant R as RefundListener<br/>group kirana-refunds
    participant DB as Postgres
    participant G as payment-mock
    participant K2 as Kafka payments.v1
    P->>DB: late payment → late_payment_id + outbox PaymentAfterClose
    DB-->>K1: relay
    K1->>R: PaymentAfterClose (order 50051052)
    R->>DB: BEGIN; inbox row; INSERT refunds (REQUESTED); COMMIT
    R->>K1: ack (offset committed: the refund is a row now)
    R->>DB: claim lease (UPDATE … claimed_until)
    R->>G: GET /v1/payments/pay_…/refunds → none
    R->>G: POST /v1/payments/pay_…/refund → rfnd_… "pending"
    R->>DB: REQUESTED → PENDING (gateway_refund_id)
    Note over G: ~3 s later
    G->>DB: webhook refund.processed → outbox (6c path)
    DB-->>K2: relay
    K2->>P: RefundProcessed
    P->>DB: PENDING → PROCESSED (by payment id)
    Note over DB: Orders page: "Refunded"
```

Why the gateway call comes **after** the acknowledgement: a slow gateway must not hold up the
partition, and Kafka's own retries (3× in `KafkaConfig`) would repeat a call that isn't
idempotent. Once the intent is a committed row, the offset can move on; `RefundJobs` owns retries.

### Ask first, then act (the timeout case)

```mermaid
flowchart TD
    A["execute(refund)"] --> L{"claim lease<br/>(conditional UPDATE)"}
    L -->|0 rows| X["someone else is on it, or it's done: stop"]
    L -->|1 row| Q["GET refunds for this payment"]
    Q -->|"found one"| ADOPT["adopt it: PENDING/PROCESSED<br/>(WARN: an earlier attempt worked)"]
    Q -->|none| C["POST refund (never auto-retried)"]
    C -->|ok| P["PENDING"]
    C -->|"timeout / 5xx / breaker open"| E["stay REQUESTED, record error<br/>retry after the lease, asking first again"]
    C -->|4xx| F["FAILED: a person must look"]
```

### New and changed code

| File | What |
|---|---|
| `payment-mock/app.py` | `POST /v1/payments/{id}/refund` (not idempotent, like Razorpay), `GET /v1/payments/{id}/refunds`; refunds go `pending` → `processed` after `processing_s` with a `refund.processed` webhook; `/admin/refunds` `reply_delay_ms` makes the refund and answers late |
| `db/migration/V7__refunds.sql` | `refunds` (`payment_id UNIQUE`, `status`, `gateway_refund_id`, `attempts`, `last_error`, `claimed_until`) |
| `entity/Refund.java`, `RefundStatus.java`, `repository/RefundRepository.java` | the row; `claim` (lease), `markAtGateway`, `settle` (by payment id), `recordError`, `markFailed`, job queries |
| `service/RefundService.java` | `request` (intent row), `execute` (lease → ask → create), `settle`, `poll` |
| `messaging/RefundListener.java` | **second consumer**: group `kirana-refunds` on `orders.v1`; inbox + `request` in one transaction, ack, then `execute` once |
| `service/RefundJobs.java` | every 15 s: retry `REQUESTED` with an expired lease; every 60 s: poll stale `PENDING` |
| `payment/*` | `refund`, `fetchRefunds`, `receivesWebhooks` on the port; `GatewayRefund`; create is **not** retried by Resilience, the read is |
| `service/PaymentWebhooks.java`, `messaging/PaymentEventsListener.java` | `refund.processed` / `refund.failed` → `RefundProcessed` / `RefundFailed` on `payments.v1` → `RefundService.settle` |
| `entity/Order`, `OrderResponse`, `OrderMapper`, `OrderRepository` | `refundStatus`; refunds fetched in the order-history query (still **2 statements**: the Stage 2 test caught a 3rd) |
| `service/PaymentJobs.java`, `application.yml` | reconciler per gateway: `reconcile-after: 10m`, `reconcile-after-without-webhooks: 1m` |
| `frontend/…/Orders.jsx` | Refund requested / in progress / Refunded / failed |
| Tests | `RefundIntegrationTest`: once despite duplicates; outage then retry; **timeout after the gateway refunded → still one refund**; 10 simultaneous attempts → one gateway call. Webhook test: late payment → refund → `refund.processed` → PROCESSED, through real Kafka. **87 tests pass.** |

### Experiments (run on the real stack)

| # | Setup | Result |
|---|---|---|
| 0 | start the 6d backend | `kirana-refunds` read `orders.v1` **from the beginning**, found 6c's late payment for order 50050903, and requested its refund. payment-mock answered **404**: it keeps payments in memory and had been rebuilt since. → `FAILED`, "a person must look". Replay works; a gateway that forgot the payment is exactly what FAILED is for. |
| 1 | cancel, then pay | REQUESTED → gateway asked (none) → created → PENDING → `refund.processed` 3 s later → **Refunded, 4 s end to end** |
| 2 | `reply_delay_ms: 5000` (refund made, answer after 5 s; we time out at 2 s) | attempt 1: `HttpTimeoutException`, stays REQUESTED. 32 s later the job: `the gateway already has rfnd_… (an earlier attempt worked); not refunding again`. Gateway: **1 refund**. Row: PROCESSED, 2 attempts. |
| 2b | (by accident) your 8080 backend still ran **6c** in the same `kirana-payments` group | Kafka gave it partitions 0–1. The `RefundProcessed` event landed on partition 0: the 6c code logged `unknown type, ignored` and committed it. The 6d instance never saw it; the job's ask-first finished the refund anyway. **Lesson: during a rolling deploy, deploy consumers before the producers of new event types.** (6f) |

### Try it

Restart your backend (V7 runs; log: `kirana-refunds: partitions assigned: [orders.v1-0, orders.v1-1, orders.v1-2]`).
It will immediately try the refunds of older late payments; those whose payments payment-mock
has forgotten end FAILED (404).

1. **Refund:** place an order, open its payment window, cancel the order in another tab, pay.
   Orders: Cancelled + **Refund requested → in progress → Refunded** within ~5 s.
   `grep -E "Refund|PaymentAfterClose|RefundProcessed" backend/logs/kirana.log`
2. **The timeout case:** `curl -XPOST localhost:8090/admin/refunds -H 'Content-Type: application/json' -d '{"reply_delay_ms":5000}'`,
   repeat 1, and look for `gateway unavailable … HttpTimeoutException` then `an earlier attempt worked`.
   Reset with `{"reply_delay_ms":0}`.
3. **Gateway down:** Lab → payment-mock **down**, repeat 1 → "Refund requested" stays; set it back
   to normal → Refunded within ~45 s (`Refund job: trying refund … again`).
4. `docker exec -it kirana-postgres-1 psql -U kirana -c "select id,order_id,status,gateway_refund_id,attempts,last_error from refunds order by id desc limit 5"`
5. Kafka UI → Consumers: two groups now, `kirana-payments` (payments.v1) and `kirana-refunds` (orders.v1).

### Known costs

* After a failed attempt, a refund waits for the 30 s lease plus up to 15 s for the job.
* `FAILED` has no screen yet; it's a log line and a row (`last_error`).
* Only full refunds, one per payment.
* `orders.v1` still has events nobody acts on (`OrderPaid`): fulfilment is 6e.

---

## 6e. Fulfilment (the dual write, reproduced and fixed)

**Done:** a paid order is now handed to a warehouse. **warehouse-mock** (new, port 8091) stands
in for a logistics partner. A third consumer, `FulfilmentListener` (group `kirana-fulfilment`),
reads `OrderPaid` from `orders.v1` and asks the warehouse to ship. The Orders page shows
**Placed → Paid → Sent to warehouse** with the shipment id.

**Problem solved: the dual write.** 6b explained it; 6e shows it. The obvious way to ship a paid
order is to call the warehouse right after the "paid" commit. That's still in the code, behind
`kirana.fulfilment.mode: naive`, so it can be reproduced:

```mermaid
flowchart LR
    subgraph Naive["mode: naive (the dual write)"]
        N1["verify (shopper's request)<br/>order → PAID, COMMIT"] --> N2["afterCommit:<br/>POST warehouse (in the same request)"]
        N2 -->|"slow"| N3["shopper waits"]
        N2 -->|"down / timeout / crash"| N4["order PAID, never shipped<br/>nothing retries"]
        N2 -->|"timeout, but it worked"| N5["warehouse shipped,<br/>Kirana doesn't know"]
    end
    subgraph Events["mode: events (6e)"]
        E1["verify: order → PAID<br/>+ outbox OrderPaid, COMMIT"] --> E2["relay → orders.v1"]
        E2 --> E3["FulfilmentListener<br/>(group kirana-fulfilment)"]
        E3 -->|"Idempotency-Key:<br/>kirana-order-{id}"| E4["warehouse-mock"]
        E4 -->|"503 / timeout"| E5["retry the SAME record<br/>1, 2, 4 … 30 s (partition waits)"]
        E5 --> E3
        E4 -->|"201 / 200 replayed"| E6["shipment_id saved<br/>(WHERE shipment_id IS NULL), ack"]
    end
```

### Why this consumer is built differently from the refund consumer

| | Refunds (6d) | Fulfilment (6e) |
|---|---|---|
| Is the remote call idempotent? | **No** (each call can refund again) | **Yes** (warehouse de-duplicates by `Idempotency-Key`) |
| Protection against doing it twice | intent row + lease + ask first | the warehouse itself + conditional UPDATE; **no inbox, no intent row** |
| When is the offset acknowledged? | **before** the call (the row is the promise) | **after** the call succeeds |
| Remote down | record, move on, a job retries later | **retry the same record, block the partition** |
| Why | a failure of one refund says nothing about the next; repeating the call is dangerous | an outage fails every `OrderPaid` alike, so skipping ahead ships nothing extra; repeating is safe; waiting costs time, not data |
| 4xx (the remote will never accept it) | `FAILED`, for a person | not retried: logged, skipped (dead-letter topic in 6f) |

### Use case: Asha pays while the warehouse is down

```mermaid
sequenceDiagram
    participant A as Asha (verify)
    participant DB as Postgres
    participant K as Kafka orders.v1
    participant F as FulfilmentListener
    participant W as warehouse-mock
    A->>DB: order → PAID + outbox OrderPaid (one commit)
    DB-->>A: 200 Paid (17 ms; the warehouse isn't on this path)
    DB-->>K: relay: OrderPaid p1@8
    K->>F: OrderPaid (order 50051152)
    F->>W: POST /v1/shipments (Idempotency-Key kirana-order-50051152)
    W-->>F: 503
    Note over F,K: not acked; wait 1 s, 2 s, 4 s, 8 s, 16 s…<br/>Kafka UI: kirana-fulfilment lag 1 on partition 1
    F->>W: POST again (same key)
    W-->>F: 201 shp_40fd7fa43c
    F->>DB: UPDATE orders SET shipment_id=… WHERE shipment_id IS NULL
    F->>K: ack (offset 9)
```

### New and changed code

| File | What |
|---|---|
| `warehouse-mock/` (new service), `infra/docker-compose.yml` | FastAPI on 8091: `POST /v1/shipments` (requires `Idempotency-Key`; repeat → same shipment, `Idempotent-Replayed: true`), `GET /v1/shipments?order_id=`, `/admin/mode` normal/slow/down |
| `db/migration/V8__fulfilment.sql` | `orders.shipment_id`, `orders.sent_to_warehouse_at` |
| `warehouse/WarehouseClient.java` (+ `Shipment`, `WarehouseUnavailableException`, `WarehouseRejectedException`) | the HTTP call, explicit timeouts (1 s connect, 2 s read), 5xx/timeout vs 4xx |
| `config/FulfilmentProperties.java`, `application.yml` | `kirana.fulfilment.mode` (`events` / `naive`, env `FULFILMENT_MODE`), warehouse URL, key, timeouts |
| `service/FulfilmentService.java` | read the order and its lines, call the warehouse outside any transaction, record the shipment once. Uses `REQUIRES_NEW`: the naive path runs in `afterCommit()`, where a joined transaction would never commit |
| `service/CheckoutSaga.java` | mode `naive` only: the dual write in `afterCommit()` of the PAID transition |
| `messaging/FulfilmentListener.java` | **third consumer**: group `kirana-fulfilment`, `OrderPaid` only; call, then ack; rethrow on unavailable |
| `messaging/KafkaConfig.java` | `fulfilmentListenerFactory`: its own error handler (exponential back-off to 30 s, unlimited; `WarehouseRejectedException` not retryable) |
| `OrderRepository`, `Order`, `OrderResponse`, `OrderMapper` | `markSentToWarehouse`; `shipmentId`, `sentToWarehouseAt` |
| `frontend/…/Orders.jsx` | third step "Sent to warehouse", shipment line, refresh while a paid order isn't sent yet |
| Tests | `FulfilmentIntegrationTest` (real Kafka, mocked warehouse): sent once although `OrderPaid` arrives twice; an outage (2 failures) is waited out, not skipped. Tests never reach the real warehouse-mock. **89 tests pass.** |

### Experiments (run on the real stack)

| # | Setup | Result |
|---|---|---|
| 1 | `naive`, warehouse **slow** (5 s; we wait 2 s) | the shopper's verify took **2.03 s**; log: `order … is PAID but the warehouse call failed (HttpTimeoutException). Nothing will retry it`. Kirana: no shipment. **The warehouse: 1 shipment** (it finished after we gave up). The systems disagree. |
| 2 | restart in `events` mode | the new group `kirana-fulfilment` replayed `orders.v1`: **6 `OrderPaid`** (5 older paid orders from 6b/6c that had never shipped, plus the naive one). For the naive one the warehouse answered with the **same shipment** (`idempotency key replayed`): repaired, still 1 shipment. |
| 3 | `events`, warehouse **down** ~25 s | verify **17 ms**; retries at 1, 2, 4, 8, 16 s on `p1@8`; `kafka-consumer-groups --describe`: **lag 1** on partition 1; warehouse back → shipped 5 s later; 1 shipment. |

### Try it

Restart your backend (V8; log `kirana-fulfilment: partitions assigned: [orders.v1-0, orders.v1-1, orders.v1-2]`).
It will immediately ship every paid order in `orders.v1` that has no shipment yet.

1. Pay an order. Orders: **Sent to warehouse** within a second; `curl "localhost:8091/v1/shipments?order_id=<id>"`.
2. **Outage:** `curl -XPOST localhost:8091/admin/mode -H 'Content-Type: application/json' -d '{"mode":"down"}'`, pay an order
   (Paid instantly, "Sent to warehouse" waiting). Watch `grep "warehouse unavailable" backend/logs/kirana.log`
   and Kafka UI → Consumers → `kirana-fulfilment` → lag. Set `{"mode":"normal"}`: shipped within 30 s.
3. **The dual write:** stop the backend, start it with `FULFILMENT_MODE=naive`, turn webhooks off
   (`curl -XPOST localhost:8090/admin/webhooks -H 'Content-Type: application/json' -d '{"enabled":false}'`),
   set the warehouse `{"mode":"slow","delay_ms":5000}`, pay in the UI: the "paid" message takes 2 s
   longer, the order never shows a shipment, `curl localhost:8091/v1/shipments` shows one.
   Restart without `FULFILMENT_MODE` and watch the replay fix it. (Webhooks back on: `{"enabled":true}`.)

### Known costs

* During a warehouse outage every order on the partition waits (by design); lag must be watched.
* `WarehouseRejectedException` is logged and skipped: that order never ships until a person
  acts. 6f adds a dead-letter topic.
* The mode switch needs a restart.

---

## 6f (part 1). Gap fixes: nothing dropped, nothing invisible, nothing forever

The Kafka-mechanics experiments (keys, scaling, rebalancing, lag) are still to come. This part
fixes the gaps 6b–6e left behind:

| # | Gap | Fix | Decision |
|---|---|---|---|
| B1 | a record a consumer gave up on was **logged and dropped** (payments: after 3 retries; fulfilment: a warehouse 4xx) | **dead-letter topics** `orders.v1-dlt`, `payments.v1-dlt` + **re-drive** | D64 |
| B2 | a crashed instance kept its partitions **45 s** (6c) | session timeout **10 s**, heartbeat 3 s | D66 |
| B3 | lag was only visible in Kafka UI, though fulfilment now blocks on purpose | **lag per group and dead letters in the lab** | D68 |
| B4 | an old instance acknowledged an event type it didn't know (6d, 2b) | `payments.v1`: **unknown type → dead letter**, never "done" | D65 |
| B5 | `outbox` and `processed_events` grew forever | **hourly cleanup**, 7-day retention | D67 |

### Where a failing record goes now

```mermaid
flowchart TD
    R["record from orders.v1 / payments.v1"] --> L["listener"]
    L -->|ok| A["ack (offset committed)"]
    L -->|"throws"| C{"can retrying help?"}
    C -->|"no: bad JSON, unknown type,<br/>warehouse 4xx"| D["dead-letter at once"]
    C -->|"yes: DB hiccup, timeout"| T{"which consumer?"}
    T -->|"payments, refunds"| R3["retry 3× (1 s)"] -->|still failing| D
    T -->|"fulfilment, warehouse unavailable"| W["retry forever, 1→30 s<br/>(partition waits, lag grows)"]
    D --> DL["&lt;topic&gt;-dlt, same partition<br/>headers: original topic/partition/offset,<br/>group, exception<br/>log: Dead-lettered …"]
    DL --> CM["offset committed: the partition moves on"]
    DL -.->|"after the fix: lab → Re-drive"| RD["copy back to the original topic<br/>once per original record"]
    RD --> R
```

### Re-drive, and why it de-duplicates

`orders.v1` has two consumer groups (refunds, fulfilment). A record both choke on is
dead-lettered **twice**. Re-driving both copies would put it on `orders.v1` twice, and each copy
reaches both groups again. So a re-drive sends each original record (topic, partition, offset)
back **once**, and its progress is the offsets of its own group, `kirana-dlt-redrive` ("waiting"
in the lab = that group's lag). A re-driven record is a new delivery to **every** group of the
topic; that's safe only because every Kirana consumer is idempotent.

### New and changed code

| File | What |
|---|---|
| `messaging/KafkaConfig.java` | the two `-dlt` topics; `DeadLetterPublishingRecoverer` (same partition); both error handlers recover into it (with a `Dead-lettered …` log line) and commit the offset; not-retryable: `JacksonException`, `UnknownEventTypeException` (+ `WarehouseRejectedException` for fulfilment) |
| `messaging/UnknownEventTypeException.java`, `PaymentEventsListener.java` | an unknown `payments.v1` type throws (its transaction, inbox row included, rolls back) |
| `messaging/DeadLetters.java` | re-drive: `assign()` the dead-letter partitions under group `kirana-dlt-redrive`, copy each original record back once (keeping `event-id`/`event-type`, adding `redriven-from`), commit |
| `controller/SystemController.java` | `POST /system/kafka/dead-letters/{topic}/redrive` |
| `messaging/KafkaStatus.java` | per group: state, instances, lag; per dead-letter topic: waiting |
| `messaging/MessagingCleanup.java` | hourly, batches of 5,000: published outbox rows and `processed_events` older than 7 days |
| `application.yml` | `session.timeout.ms: 10000`, `heartbeat.interval.ms: 3000`; `kirana.messaging.retention: 7d` |
| `frontend/…/ResilienceLab.jsx`, `api.js`, `styles.css` | Kafka card: consumer-group table (instances, lag), dead letters with a **Re-drive** button |
| Tests | `DeadLetterIntegrationTest`: poison → DLT with headers; unknown type → DLT and no inbox row; re-drive once; lab sees groups/lag; cleanup keeps unpublished and recent rows. **94 tests pass.** |

### Checked on the real stack

| What | Result |
|---|---|
| Lab status | 4 topics; `kirana-payments` 2 instances (your 8080 + 8081), `kirana-refunds` 1, `kirana-fulfilment` 1, lag 0; 0 dead letters |
| Poison on `orders.v1` (`{"type":"OrderPaid", "orderId": "not-a-number`) | both groups dead-lettered it at once: **2 waiting**, **lag 0** (nothing blocked). Log: `Dead-lettered orders.v1 p0@12 (key poison-2) to orders.v1-dlt: …StreamReadException…` from each group |
| Re-drive before fixing anything | `{"redriven":1}`: one copy sent, `skipped: orders.v1/2@3 was already re-driven (another group's copy)`; still poison → dead-lettered again (2 waiting) |
| `kill -9` an instance | partitions back with the new instance **10 s** later (6c: ~45 s) |

### Try it

Restart your backend first (the new session timeout and error handling apply to new instances).

1. Lab → Kafka: the consumer-group table and two dead-letter lines.
2. Poison: `echo 'p1:not json' | docker exec -i kirana-kafka-1 /opt/kafka/bin/kafka-console-producer.sh --bootstrap-server localhost:9092 --topic orders.v1 --property parse.key=true --property key.separator=:`
   → lab: `orders.v1-dlt: 2 waiting`; `grep Dead-lettered backend/logs/kirana.log`; Kafka UI → `orders.v1-dlt` → headers.
   (Two poison records from my check are already waiting there; re-driving them just sends them back.)
3. Warehouse 4xx → dead letter: not reproducible from the UI (Kirana never sends an empty order); covered by the error-handler rule.

---

## Watching it live (logs, Kafka UI, database)

The backend also writes its log to `backend/logs/kirana.log` (when started from `backend/`).
Follow only the Stage 6 flow, without the SQL lines:

```bash
tail -f backend/logs/kirana.log | grep --line-buffered -E \
  "Outbox|Webhook from|payments.v1 p|REFUND|Reconciler|Re-check|Expiry|partitions assigned"
docker logs -f kirana-payment-mock-1 2>&1 | grep --line-buffered -E "payment pay_|webhook"
```

One order, start to finish (real output; note the thread on each line):

```
15:11:21.185 [http-nio-exec-3]  Outbox: OrderPlaced for order 50050952 queued as d0b86d4d…          ← TX1 (request thread)
15:11:21.433 [scheduling-1]     Outbox relay: OrderPlaced for order 50050952 -> orders.v1 partition 0 offset 5
             (mock)             payment pay_Khl1… for order_RBt2…: captured
             (mock)             webhook evt_db62… payment.captured for order_RBt2…: attempt 1 -> 200
15:11:22.366 [http-nio-exec-4]  Webhook from mock: payment.captured for order 50050952 … queued as 237aafcf…
15:11:22.463 [scheduling-1]     Outbox relay: PaymentCaptured for order 50050952 -> payments.v1 partition 0 offset 2
15:11:22.476 [payments-listener] Outbox: OrderPaid for order 50050952 queued as 8b1d3eac…           ← consumer's transaction
15:11:22.477 [payments-listener] payments.v1 p0@2: PaymentCaptured for order 50050952 -> order PAID
15:11:22.983 [scheduling-1]     Outbox relay: OrderPaid for order 50050952 -> orders.v1 partition 0 offset 6
```

---

## Stage 6 wrap-up

### What Stage 6 fixed

| Gap from Stage 5 | Fix | Step | Measured |
|---|---|---|---|
| Events lost on a crash (in-memory) | transactional outbox + relay | 6b | backend `kill -9`'d with Kafka down: events delivered after restart, in order |
| **G1** money taken after expiry, nobody noticed | webhooks → `payments.v1` → consumer; re-check job | 6c | tab closed: PAID in **0.5 s**; late payment detected once |
| **G3** "unknown" closed the order | hold up to 1 h, then close; re-check watches | 6c | test |
| **G2** "REFUND NEEDED" was a log line | refund consumer: intent row, lease, ask first | 6d | late payment refunded in **4 s**; timeout-after-refund → still **1 refund** |
| Paid orders went nowhere; naive call = dual write | `OrderPaid` consumer → idempotent warehouse, blocking retry | 6e | naive: verify **2.03 s**, order lost, systems disagreed; events: verify **17 ms**, outage waited out, replay repaired history |
| Failed records dropped; 45 s takeover; invisible lag; tables grow | dead-letter topics + re-drive; 10 s session; lag in lab; 7-day cleanup | 6f | poison → DLT, lag 0; takeover **10 s** |

Decisions D56–D68. Concepts 25–36 in `concepts-learned.md`. Three consumer groups now:
`kirana-payments` (payments.v1), `kirana-refunds` and `kirana-fulfilment` (orders.v1).

Roadmap coverage: producers, consumers, topics, partitions, consumer groups, offsets, ordering,
retries, dead-letter queues, at-least-once delivery and idempotent consumers were built and
observed. **Not done:** the 6f experiments (keys → partitions, scaling instances, a 4th idle
consumer, graceful vs crash rebalancing, lag build-up and drain); Ravi chose to skip them for now.
They fit naturally in Stage 9–11, when several instances run anyway. **Backpressure** was met
only indirectly (fulfilment's blocking retry, lag as the signal; the relay's batch size).

### Problems the code has now (they motivate the next stages)

| # | Problem | Seen where | Stage |
|---|---|---|---|
| P1 | **A retried "Place order" can't get its answer back.** The first request placed the order; the retry finds the cart empty and gets 409 "Cart is empty" (or "Checkout already in progress"). The client never learns order #42 exists. D55 was a stopgap. | Stage 5, D55 | **7** |
| P2 | **A retried "Add to cart" adds twice** (`addOrIncrement`): one tap after a timeout, quantity 2. | code check | **7** |
| P3 | Cancel / Pay now retried: the second gets 409 instead of the first's result. | Stage 5 | **7** |
| P4 | Every instance runs every job (reconcile, expiry, re-check, refund retries, cleanup): correct thanks to conditional updates and leases, but duplicated gateway calls and work. Stale `PENDING` images are never cleaned. | Stage 5–6 | **8** |
| P5 | All consumers live in the checkout process: one deploy for everything; old and new versions in one group dropped an event (6d). | 6d | 9 |
| P6 | Re-drive reaches every group of a topic (per-group retry topics would be cleaner). | 6f | 9 |
| P7 | One broker, replication factor 1; mocks keep state in memory (a restart forgets payments). | 6a, 6d | 10–11 |
| P8 | Lag, dead letters and refunds stuck in `FAILED` are visible only in the lab and logs: no metrics, no alerts. | 6e, 6f | 14 |
