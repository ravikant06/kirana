# Seed data

`seed.sql` loads generated users, products, inventory and orders for experiments.
Sizes and a tag are psql variables; each tag can be loaded once.

    # small set (100 users, 500 products, 1,000 orders)
    docker exec -i kirana-postgres-1 psql -U kirana -d kirana < infra/seed/seed.sql

    # Stage 2 large set (50k users, 100k products, 1M orders, ~3M lines; 1-3 minutes)
    docker exec -i kirana-postgres-1 psql -U kirana -d kirana -v tag=bulk \
      -v users=50000 -v products=100000 -v orders_per_user=20 < infra/seed/seed.sql

## Query statistics

`docker-compose.yml` preloads `pg_stat_statements`. Create the extension once per database:

    docker exec kirana-postgres-1 psql -U kirana -d kirana -c "create extension if not exists pg_stat_statements"

Reset the counters before a measurement, then read the top statements:

    docker exec kirana-postgres-1 psql -U kirana -d kirana -c "select pg_stat_statements_reset()"
    docker exec kirana-postgres-1 psql -U kirana -d kirana -c \
      "select calls, round(total_exec_time::numeric, 1) as total_ms, round(mean_exec_time::numeric, 2) as mean_ms, left(query, 80) from pg_stat_statements order by total_exec_time desc limit 10"
