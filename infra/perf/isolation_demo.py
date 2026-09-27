#!/usr/bin/env python3
"""
Stage 2e: isolation anomalies, shown on Kirana's own tables and API.

    python3 infra/perf/isolation_demo.py            # backend on :8080, Postgres in Docker

Each psql demo runs two sessions, A and B, at the same time. pg_sleep puts their steps
in a fixed order (B acts at ~1 s, A looks again at ~2 s), so the output is repeatable.
Everything happens on one demo product created for the run; nothing else is changed.
"""
import json
import subprocess
import threading
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

API = "http://localhost:8080"
PSQL = ["docker", "exec", "-i", "kirana-postgres-1", "psql", "-U", "kirana", "-d", "kirana", "-X", "-q", "-A", "-t"]


def api(method, path, body=None):
    req = urllib.request.Request(API + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read() or "null")
    except urllib.error.HTTPError as e:
        return e.code, None


def sql(text):
    return subprocess.run(PSQL, input=text, capture_output=True, text=True).stdout.strip()


def two_sessions(a_sql, b_sql):
    """Run A and B concurrently; return what each printed (stdout and errors)."""
    out = {}

    def run(name, text):
        p = subprocess.run(PSQL, input=text, capture_output=True, text=True)
        out[name] = (p.stdout + p.stderr).strip()

    threads = [threading.Thread(target=run, args=("A", a_sql)), threading.Thread(target=run, args=("B", b_sql))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out["A"], out["B"]


def show(title, a, b, lesson):
    print(f"\n{'=' * 78}\n{title}\n{'-' * 78}")
    for name, text in (("B", b), ("A", a)):
        print(f"Session {name}:")
        for line in text.splitlines():
            if line.strip():
                print("   " + line.strip())
    print(f"-> {lesson}")


def set_stock(pid, qty):
    sql(f"update inventory set quantity = {qty} where product_id = {pid};")


def main():
    status, product = api("POST", "/products", {"name": "Isolation demo", "price": "10"})
    pid = product["id"]
    stock = f"select quantity from inventory where product_id = {pid}"
    print(f"Demo product {pid} created through the API.")

    # 1. Dirty read ------------------------------------------------------------------
    set_stock(pid, 10)
    a, b = two_sessions(f"""
        begin isolation level read uncommitted;
        select pg_sleep(1) \\g /dev/null
        \\echo 'reads stock while B has an UNCOMMITTED change to 999:'
        {stock};
        commit;""", f"""
        begin;
        update inventory set quantity = 999 where product_id = {pid};
        \\echo 'set stock to 999, not committed yet ... then rolls back'
        select pg_sleep(2) \\g /dev/null
        rollback;""")
    show("1. DIRTY READ: can A see B's uncommitted change? (A asks for READ UNCOMMITTED)", a, b,
         "No. Postgres never shows uncommitted data; READ UNCOMMITTED behaves as READ COMMITTED.")

    # 2. Non-repeatable read ---------------------------------------------------------
    for level in ["read committed", "repeatable read"]:
        set_stock(pid, 10)
        a, b = two_sessions(f"""
            begin isolation level {level};
            \\echo 'read 1:'
            {stock};
            select pg_sleep(2) \\g /dev/null
            \\echo 'read 2, same transaction:'
            {stock};
            commit;""", f"""
            select pg_sleep(1) \\g /dev/null
            update inventory set quantity = 15 where product_id = {pid};
            \\echo 'changed stock 10 -> 15 and committed'""")
        show(f"2. NON-REPEATABLE READ at {level.upper()}: same row read twice", a, b,
             "Two different answers inside one transaction." if level == "read committed"
             else "Same answer both times: the transaction reads one snapshot taken at its first query.")

    # 3. Phantom (P7: page and count in ProductService.list) -------------------------
    count = "select count(*) from products where deleted_at is null"
    for level in ["read committed", "repeatable read"]:
        a, b = two_sessions(f"""
            begin isolation level {level};
            \\echo 'count of live products (what totalElements uses):'
            {count};
            select pg_sleep(2) \\g /dev/null
            \\echo 'same count again, same transaction:'
            {count};
            commit;""", """
            select pg_sleep(1) \\g /dev/null
            with p as (insert into products (id, name, price, created_at, updated_at)
                       values (nextval('products_seq'), 'Phantom product', 1, now(), now()) returning id)
            insert into inventory (product_id, quantity, updated_at) select id, 0, now() from p;
            \\echo 'inserted a new product and committed'""")
        show(f"3. PHANTOM at {level.upper()}: a new row appears between two queries", a, b,
             "The row set changed mid-transaction. GET /products runs the page query and the count "
             "query separately, so totalElements can disagree with the page (P7)." if level == "read committed"
             else "Stable: the new product is invisible until the transaction ends.")
        sql("delete from inventory where product_id in (select id from products where name = 'Phantom product');"
            "delete from products where name = 'Phantom product';")

    # 4. Lost update, through the real API (P8: InventoryService.adjust) --------------
    set_stock(pid, 0)
    requests, workers = 100, 20
    with ThreadPoolExecutor(workers) as pool:
        codes = Counter(pool.map(lambda _: api("POST", f"/products/{pid}/inventory/adjustments", {"delta": 1})[0],
                                 range(requests)))
    final = api("GET", f"/products/{pid}/inventory")[1]["quantity"]
    print(f"\n{'=' * 78}\n4. LOST UPDATE through the API: {requests} concurrent '+1' adjustments, "
          f"{workers} at a time, starting from 0\n{'-' * 78}")
    print(f"   HTTP responses: {dict(codes)}")
    print(f"   Final stock: {final}   (expected {requests}; {requests - final} updates lost)")
    print("-> Every request got 200, yet stock is short. Each one read the quantity, added 1 in Java, and wrote\n"
          "   the result back; two requests that read the same value both write the same result.")

    # 5. Lost update at each level, same pattern as Hibernate's SELECT then UPDATE ---
    for level in ["read committed", "repeatable read"]:
        set_stock(pid, 10)
        a, b = two_sessions(f"""
            begin isolation level {level};
            \\echo 'reads stock:'
            {stock};
            select pg_sleep(2) \\g /dev/null
            \\echo 'writes 10 + 1 = 11 (computed from its read):'
            update inventory set quantity = 11 where product_id = {pid};
            commit;""", f"""
            begin isolation level {level};
            select pg_sleep(1) \\g /dev/null
            \\echo 'reads stock:'
            {stock};
            \\echo 'writes 10 + 1 = 11 and commits'
            update inventory set quantity = 11 where product_id = {pid};
            commit;""")
        after = sql(stock + ";")
        show(f"5. LOST UPDATE at {level.upper()}: both add 1 to the same stock", a, b,
             f"Final stock {after}, expected 12." + (" A overwrote B's update silently."
                                                     if level == "read committed" else
                                                     " A was stopped with a serialization error instead of "
                                                     "overwriting. The app would have to retry A."))

    api("DELETE", f"/products/{pid}")
    print(f"\nDemo product {pid} soft-deleted.")


if __name__ == "__main__":
    main()
