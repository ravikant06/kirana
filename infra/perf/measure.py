#!/usr/bin/env python3
"""
Stage 2 measurement: the same hot queries and endpoints, measured the same way every time,
so each milestone can be compared with the baseline.

    python3 infra/perf/measure.py --label 2a-baseline --api http://localhost:8080

Writes docs/perf/<label>.json. Needs the bulk seed (infra/seed/README.md), Docker, and a
running backend with kirana.diagnostics.query-metrics=true.

Two kinds of measurement:
  endpoints  HTTP calls through the backend: median wall time, SQL count, DB time (headers)
  queries    the SQL Hibernate sends, run with EXPLAIN (ANALYZE, BUFFERS): plan, time, rows read
Timings are "warm": each is run a few times first so data is in Postgres's cache.
"""
import argparse
import json
import statistics
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import kirana_auth  # AI Phase 5: bearer tokens instead of X-User-Id

CONTAINER = "kirana-postgres-1"
REPEAT = 5
BULK_USER_EMAIL = "bulk-user-777@kirana.test"
PAGE_SIZE = 12


def psql(sql):
    out = subprocess.run(
        ["docker", "exec", CONTAINER, "psql", "-U", "kirana", "-d", "kirana", "-tAX", "-c", sql],
        check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


def http(api, path, user=None):
    req = urllib.request.Request(api + path)
    for k, v in (kirana_auth.headers(api, user) if user else {}).items():
        req.add_header(k, v)
    start = time.perf_counter()
    with urllib.request.urlopen(req) as res:
        res.read()
        ms = (time.perf_counter() - start) * 1000
        return ms, int(res.headers.get("X-Query-Count", -1)), float(res.headers.get("X-DB-Time-Ms", -1))


def measure_endpoint(api, name, path, user=None):
    http(api, path, user)  # warm-up
    runs = [http(api, path, user) for _ in range(REPEAT)]
    return {
        "name": name,
        "path": path,
        "median_ms": round(statistics.median(r[0] for r in runs), 1),
        "sql_statements": runs[-1][1],
        "db_ms": round(statistics.median(r[2] for r in runs), 1),
    }


def walk(node, out):
    """Flatten a JSON plan into the facts that matter: what was read, how, and how much was thrown away."""
    loops = node.get("Actual Loops", 1)
    out.append({
        "node": node["Node Type"],
        "relation": node.get("Relation Name"),
        "index": node.get("Index Name"),
        "rows_out": node.get("Actual Rows", 0) * loops,
        "rows_removed_by_filter": node.get("Rows Removed by Filter", 0) * loops,
        "sort_method": node.get("Sort Method"),
        "sort_space_kb": node.get("Sort Space Used"),
    })
    for child in node.get("Plans", []):
        walk(child, out)
    return out


def explain(name, sql, why):
    for _ in range(2):  # warm-up
        psql("explain (analyze) " + sql)
    plan = json.loads(psql("explain (analyze, buffers, format json) " + sql))[0]
    root = plan["Plan"]
    nodes = walk(root, [])
    return {
        "name": name,
        "why": why,
        "sql": " ".join(sql.split()),
        "execution_ms": round(plan["Execution Time"], 2),
        "rows_returned": root.get("Actual Rows", 0),
        "rows_scanned": sum(n["rows_out"] + n["rows_removed_by_filter"]
                            for n in nodes if n["node"].endswith("Scan")),
        "buffers_hit": root.get("Shared Hit Blocks", 0),
        "buffers_read": root.get("Shared Read Blocks", 0),
        "nodes": nodes,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--api", default="http://localhost:8080")
    args = ap.parse_args()

    user = int(psql(f"select id from users where email = '{BULK_USER_EMAIL}'"))
    kirana_auth.login(args.api, BULK_USER_EMAIL, kirana_auth.DEMO_PASSWORD)   # seeded users have the demo password
    order = int(psql(f"select id from orders where user_id = {user} order by created_at desc, id desc limit 1"))
    live = int(psql("select count(*) from products where deleted_at is null"))
    deep_page = live // PAGE_SIZE - 1
    page_ids = psql(f"select string_agg(id::text, ',') from (select id from products where deleted_at is null "
                    f"order by created_at desc, id desc limit {PAGE_SIZE}) p")

    endpoints = [
        measure_endpoint(args.api, "Order history", "/orders", user),
        measure_endpoint(args.api, "One order", f"/orders/{order}", user),
        measure_endpoint(args.api, "Products, first page", f"/products?page=0&size={PAGE_SIZE}"),
        measure_endpoint(args.api, "Products, last page", f"/products?page={deep_page}&size={PAGE_SIZE}"),
    ]

    # The statements Hibernate sends for those endpoints (seen in the SQL log), with real parameters.
    queries = [
        explain("Orders of one user", f"""
            select o.id, o.created_at, o.status, o.total, o.updated_at, o.user_id
            from orders o where o.user_id = {user} order by o.created_at desc, o.id desc""",
            "GET /orders. P1: orders.user_id has no index. P2: the result must be sorted."),
        explain("Lines of one order", f"""
            select i.order_id, i.id, i.line_total, i.product_id, i.product_name, i.quantity, i.unit_price
            from order_items i where i.order_id = {order} order by i.id""",
            "Runs once per order in GET /orders (the N+1, P4). P1: order_items.order_id has no index."),
        explain("Products, first page", f"""
            select p.id, p.category, p.created_at, p.deleted_at, p.description, p.name, p.price, p.updated_at
            from products p where p.deleted_at is null order by p.created_at desc, p.id desc
            offset 0 rows fetch first {PAGE_SIZE} rows only""",
            "GET /products?page=0. P3: no index matches the sort, so every live product is read and sorted."),
        explain("Products, last page", f"""
            select p.id, p.category, p.created_at, p.deleted_at, p.description, p.name, p.price, p.updated_at
            from products p where p.deleted_at is null order by p.created_at desc, p.id desc
            offset {deep_page * PAGE_SIZE} rows fetch first {PAGE_SIZE} rows only""",
            "GET /products?page=last. P3: OFFSET reads and discards every row before the page."),
        explain("Products, total count", """
            select count(p.id) from products p where p.deleted_at is null""",
            "Runs on every product page to fill totalElements. P3."),
        explain("Thumbnails for a page", f"""
            select i.id, i.content_type, i.created_at, i.object_key, i.position, i.product_id, i.size_bytes,
                   i.status, i.updated_at
            from product_images i where i.product_id in ({page_ids}) and i.status = 'ACTIVE'
            order by i.position, i.id""",
            "Runs on every product page. P1: product_images.product_id has no index (the table is nearly empty today)."),
    ]

    sizes = {t: int(psql(f"select count(*) from {t}"))
             for t in ["users", "products", "orders", "order_items", "product_images", "inventory"]}
    indexes = psql("select string_agg(indexname, ', ' order by indexname) from pg_indexes where schemaname = 'public'")

    result = {
        "label": args.label,
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "table_rows": sizes,
        "indexes": indexes.split(", "),
        "bulk_user_id": user,
        "endpoints": endpoints,
        "queries": queries,
    }
    out = Path(__file__).resolve().parents[2] / "docs" / "perf" / f"{args.label}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {out}")
    for e in endpoints:
        print(f"  {e['name']:<24} {e['median_ms']:>8} ms  {e['sql_statements']:>3} SQL  {e['db_ms']:>8} ms in DB")
    for q in queries:
        scans = ", ".join(f"{n['node']} on {n['relation']}" for n in q["nodes"] if n["relation"])
        print(f"  {q['name']:<24} {q['execution_ms']:>8} ms  read {q['rows_scanned']:>9,} rows for "
              f"{q['rows_returned']:>3}  [{scans}]")


if __name__ == "__main__":
    main()
