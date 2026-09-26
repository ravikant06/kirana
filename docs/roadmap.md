# Roadmap

The full plan for the project, from Ravi's original brief. It describes the destination.
**Only the current stage is built.** It is planned in detail in `CLAUDE.md` when we
reach it, using this file plus the problems the code actually has at that point.

## Roles

- **Ravi** makes design decisions, predicts outcomes, runs everything locally, and learns
  system design. He does not write code.
- **The assistant** writes the code, proposes options with trade-offs before each real
  design choice, challenges Ravi's decisions when it disagrees, explains what happens
  internally, sets up experiments and failure scenarios, helps debug, asks senior-level
  interview questions, and keeps `docs/concepts-learned.md` up to date.

## Principle

The architecture evolves from problems:

    Problem
       ↓
    Understand why it happens
       ↓
    Try a simple solution
       ↓
    Identify its limitations
       ↓
    Introduce the next concept
       ↓
    Measure and experiment
       ↓
    Understand the trade-offs

Examples of the chains we expect:

    Concurrent inventory update → optimistic locking → high contention → pessimistic locking
    Too many DB reads → Redis cache → cache stampede → protection strategy
    Slow downstream service → timeout → retry → retry storm → circuit breaker
    Multiple application instances → duplicate scheduled job → distributed lock
    Huge database → sharding → shard rebalancing problem → consistent hashing

## Every stage answers five questions

    What problem are we solving?
    Why does it matter?
    What should be implemented?
    What should Ravi observe?
    What should Ravi be able to explain afterwards?

## Learning loop for every major concept

1. **Problem:** explain the real-world problem first.
2. **Mental model:** explain the concept simply.
3. **Tiny experiment:** the smallest possible demonstration.
4. **Implementation:** add it to the project.
5. **Break it:** intentionally create failure, concurrency, or load scenarios.
6. **Observe:** use logs, metrics, and tests to understand what happened.
7. **Trade-offs:** alternatives, and when not to use the solution.
8. **Interview explanation:** a concise senior-level explanation.

Ravi predicts the outcome before every experiment runs.

## Constraints

Do not:

- build the entire architecture upfront
- introduce technologies because they are popular
- create unnecessary microservices
- blindly follow "best practices"
- produce huge changes at once
- hide the important mechanics behind libraries
- assume something is understood because it has been used before

Do:

- challenge design decisions and ask Ravi to reason about trade-offs
- ask for predictions before experiments
- create failure scenarios and compare alternatives
- explain what happens internally
- keep the implementation production-oriented but manageable
- keep everything runnable locally
- prefer simple implementations before sophisticated ones

---

## Stage 1: Basic backend (done)

Starting point:

    Client → Spring Boot → PostgreSQL
                  └────→ MinIO (product images, never stored in Postgres)

Spring Boot project structure, REST APIs, PostgreSQL, JPA/Hibernate, entities and
relationships, validation, exception handling, unit and integration tests. User, Product,
product images, Cart, Order, OrderItem, Inventory. Product CRUD, cart, order creation,
inventory. Kept simple, with deliberate gaps (the naive order flow) for later stages.

## Stage 2: Database fundamentals

Introduce and experiment with:

- indexes
- query plans
- transactions
- ACID
- isolation levels
- dirty reads
- non-repeatable reads
- phantom reads
- connection pooling

For each concept, a small reproducible experiment. Ravi should **see** the behaviour,
not just read about it.

## Stage 3: Concurrency

Use the inventory system to create a real concurrency problem:

    Stock = 1

    User A → buys product
    User B → buys product

    Both requests execute concurrently.

Experiment with:

- race conditions
- `synchronized` code
- Java locks
- `ExecutorService`
- `ThreadPoolExecutor`
- `BlockingQueue`
- concurrent collections

Then introduce **optimistic locking** and **pessimistic locking**, and compare:

- how they work
- when they are useful
- contention
- performance
- failure behaviour
- trade-offs

## Stage 4: Redis

Introduce Redis when PostgreSQL becomes a bottleneck for frequently accessed product data.

Implement:

- cache-aside
- TTL
- cache invalidation
- eviction
- cache stampede
- cache penetration
- distributed cache

Then implement a rate limiter using Redis, and experiment with:

- fixed window
- sliding window
- token bucket

## Stage 5: Resilience

Introduce failures between services and components. Implement:

- timeout
- retry
- exponential backoff
- jitter
- circuit breaker
- bulkhead
- graceful degradation

Do not just implement them. Create failure experiments:

    Payment service becomes slow
    Payment service becomes unavailable
    Network timeout
    Repeated failures

Show how the system behaves **before and after** each resilience mechanism.

## Stage 6: Kafka

Introduce Kafka when some operations become asynchronous:

    Order Created
          ↓
        Kafka
     ┌────┼──────────┬──────────────┐
     ↓    ↓          ↓              ↓
    Payment Inventory Notification Shipping

Teach and implement:

- producers
- consumers
- topics
- partitions
- consumer groups
- offsets
- ordering
- retries
- dead-letter queues
- backpressure
- at-least-once delivery
- idempotent consumers

Create experiments demonstrating partition assignment and consumer scaling.

## Stage 7: Idempotency

Introduce a realistic duplicate payment or order scenario:

    Client
       ↓
    POST /payment
       ↓
    Payment succeeds
       ↓
    Network timeout
       ↓
    Client retries

Implement idempotency keys. Explain:

- why duplicates happen
- how idempotency works
- a database implementation
- a Redis implementation
- idempotent Kafka consumers
- limitations and trade-offs

## Stage 8: Distributed locking

Create a scheduled or background job that can accidentally run on multiple application
instances. (Candidate from Stage 1: the cleanup of abandoned `PENDING` images.)

Then introduce:

- distributed locks
- Redis-based locking
- lock expiration
- lease renewal
- worker crash
- lock ownership
- duplicate execution

With failure experiments for each.

## Stage 9: Split into services

Only after the monolith is understood, gradually extract services such as:

    Product Service
    Order Service
    Inventory Service
    Payment Service
    Notification Service

Do not create unnecessary services. Explain the problems service boundaries introduce:

- network calls
- latency
- partial failures
- distributed transactions
- data ownership
- service discovery
- observability

(Expected from decision D10: layer-based packages will reveal cross-domain coupling here.)

## Stage 10: Docker

Containerize the application and infrastructure using:

- Dockerfile
- Docker images
- containers
- Docker networks
- volumes
- environment variables
- health checks
- Docker Compose

Run locally: Spring Boot, PostgreSQL, Redis, Kafka, MinIO.

Explain networking between containers. (Expected problem: presigned URLs signed for a
hostname the browser cannot resolve once the backend runs inside Docker.)

## Stage 11: Kubernetes

Only after Docker and multiple service instances are understood. Run locally with kind
or Minikube.

Teach through hands-on experiments:

- Pod
- Deployment
- Service
- Ingress
- ConfigMap
- Secret
- ReplicaSet
- readiness probe
- liveness probe
- resource requests and limits
- Horizontal Pod Autoscaler
- rolling deployment
- service discovery
- self-healing

Experiments:

    Kill a pod
      → Kubernetes recreates it

    Scale 2 replicas → 5 replicas

    Make a service unavailable

    Generate load

    Observe traffic distribution

## Stage 12: Database scaling

Introduce:

- read replicas
- replication lag
- read/write splitting
- partitioning
- sharding
- hot partitions
- shard keys

Create a realistic reason for sharding. Start simple:

    customerId % N

Then demonstrate why changing N causes massive key movement.

## Stage 13: Consistent hashing

Implement a simplified consistent hashing system:

    addNode()
    removeNode()
    getNode(key)

Then add virtual nodes. Run experiments measuring:

    Number of keys
    Number of nodes
    Keys moved when a node is added
    Keys moved when a node is removed
    Distribution across nodes

Explain where consistent hashing is useful and where it isn't.

## Stage 14: Observability

Introduce:

- structured logging
- correlation IDs
- metrics
- Prometheus
- Grafana
- distributed tracing
- OpenTelemetry

Create dashboards for:

- request latency
- throughput
- error rate
- Kafka lag
- database connection pool
- JVM metrics
- CPU and memory
- cache hit ratio