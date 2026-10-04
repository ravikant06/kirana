# Kirana

An e-commerce platform built to learn backend system design, one problem at a time.
It starts as a Spring Boot monolith and evolves stage by stage.

## Repository layout

    kirana/
    ├── backend/      Spring Boot monolith (Java 21, Maven)
    ├── frontend/     React UI (Vite)
    ├── infra/        Local dependencies: Postgres and MinIO (Docker Compose)
    └── docs/
        ├── api-contract.md      the API between frontend and backend
        ├── decisions.md         every design decision, why, and its cost
        └── concepts-learned.md  running list of concepts covered

## Prerequisites

| Tool | Version | Check | Why |
|---|---|---|---|
| Git | any recent | `git --version` | version control |
| JDK | 21 (Temurin recommended) | `java -version` | runs the backend |
| Maven | 3.9+ | `mvn -v` | builds the backend |
| Node.js | 20 or 22 LTS (npm included) | `node -v` | runs the frontend |
| Docker | Docker Desktop, or Engine with Compose v2 | `docker compose version` | runs Postgres and MinIO |

Optional: IntelliJ IDEA, a database GUI such as DBeaver (or `psql`), and `curl`.

Not needed in Stage 1: Kubernetes, Redis, Kafka, or a locally installed Postgres or MinIO.

Ports used: `5432` Postgres, `9000` MinIO API, `9001` MinIO console, `6380` Redis (Stage 4), `8090` payment-mock and `8474` Toxiproxy (Stage 5), `9094` Kafka, `8085` Kafka UI and `8091` warehouse-mock (Stage 6),
`8080` backend, `5173` frontend. Stop anything already using them first.

## First-time setup

**1. Put the code under Git.**

    cd kirana
    git init
    git add .
    git commit -m "Stage 1 scaffold"

**2. Start Postgres and MinIO.**

    cd infra
    docker compose up -d
    docker compose ps

Wait until `postgres` shows `healthy`. Open the MinIO console at
http://localhost:9001 and log in with `minioadmin` / `minioadmin`. There are no
buckets yet; the backend creates one on startup.

**3. Start the backend** (in a new terminal).

    cd backend
    mvn spring-boot:run

The first run downloads dependencies and takes a few minutes. In the log, look for:

- `Successfully applied 1 migration to schema "public"` from Flyway
- `Storage bucket 'product-images' created at http://localhost:9000`
- `Tomcat started on port 8080`

Then check health:

    curl http://localhost:8080/actuator/health

Expect `"status":"UP"` with a `db` component that is also `UP`.

**4. Check the tables Flyway created.**

    cd infra
    docker compose exec postgres psql -U kirana -d kirana -c '\dt'

Expect eight tables (`users`, `products`, `product_images`, `inventory`, `carts`,
`cart_items`, `orders`, `order_items`) plus `flyway_schema_history`.

**5. Start the frontend** (in a new terminal).

    cd frontend
    npm install
    npm run dev

Open http://localhost:5173. At this stage every page shows an error, because the
backend has no endpoints yet. Open **Requests** in the top bar: each call reaches
Spring Boot and comes back `404`. That confirms the whole path works:
browser → Vite proxy → backend.

## Daily use

Status, start, stop and restart for every server (Docker containers, the backend, the frontend,
the AI service and its workers), with health checks:

    python3 infra/infra.py status               # what is running, healthy, on which port
    python3 infra/infra.py restart kirana-ai    # or: kafka, backend, kirana, mocks, ...
    python3 infra/infra.py restart-all          # rebuild every image, restart everything
    python3 infra/infra.py logs backend         # servers started by infra.py log to infra/data/logs/

Every command, with examples, is listed at the top of `infra/infra.py`.

    cd infra && docker compose up -d      # dependencies
    cd backend && mvn spring-boot:run     # backend
    cd frontend && npm run dev            # frontend

## Stopping and resetting

    docker compose stop       # stop containers, keep data
    docker compose down       # remove containers, keep data (volumes survive)
    docker compose down -v    # remove containers AND data: empty database, empty bucket

## Troubleshooting

- **Port already in use.** Another Postgres or app holds the port. Stop it, or change
  the left side of the port mapping in `infra/docker-compose.yml` (for example
  `"5433:5432"`) and start the backend with `DB_PORT=5433`.
- **Backend fails at startup with a connection error.** Postgres or MinIO isn't
  running. Check `docker compose ps`.
- **`Schema-validation: missing table` or `wrong column type`.** An entity doesn't
  match the Flyway schema. That check is intentional; fix the entity or add a migration.
