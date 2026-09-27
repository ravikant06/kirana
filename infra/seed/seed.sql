-- Seed data for experiments. Not a Flyway migration: it is dev data, not schema.
--
-- Small (defaults: 100 users, 500 products, 10 orders per user):
--   docker exec -i kirana-postgres-1 psql -U kirana -d kirana < infra/seed/seed.sql
--
-- Large (Stage 2 index and query plan experiments), loaded next to the small set:
--   docker exec -i kirana-postgres-1 psql -U kirana -d kirana -v tag=bulk \
--     -v users=50000 -v products=100000 -v orders_per_user=20 < infra/seed/seed.sql
--
-- Runs once per tag: refuses if that tag's users already exist. To reseed, reset the
-- database (docker compose down -v, then up) and let Flyway recreate the schema.
--
-- What it creates, labelled with the tag (default 'seed') so it can be told apart:
--   users       'Seed User N', seed-user-N@kirana.test
--   products    'Seed Product N', 10 categories, price 10-1000, ~2% soft-deleted
--   inventory   one row per product, 0-200 units, ~10% out of stock
--   orders      spread over the last year; ~80% PAID, 10% CREATED, 5% FAILED, 5% CANCELLED
--   order_items 1-5 distinct products per order, 1-3 units each, name and price snapshotted
--
-- IDs come from each table's sequence. Each nextval() takes a whole block of 50 (the
-- sequences INCREMENT BY 50), so seeded IDs are 50 apart. The app's own blocks are
-- different nextval() calls, so the two can never collide.

\set ON_ERROR_STOP on
\if :{?tag} \else \set tag seed \endif
\if :{?users} \else \set users 100 \endif
\if :{?products} \else \set products 500 \endif
\if :{?orders_per_user} \else \set orders_per_user 10 \endif

select exists (select 1 from users where email like :'tag' || '-user-%@kirana.test') as already_seeded \gset
\if :already_seeded
  \echo 'Seed data tagged' :tag 'already present. Use another tag or reset the database.'
  \quit
\endif

\echo 'Seeding' :tag ':' :users 'users,' :products 'products,' :orders_per_user 'orders per user ...'
\timing on

begin;

create temp table seed_users (id bigint primary key, rn int) on commit drop;
create temp table seed_products (id bigint primary key, rn int unique) on commit drop;
create temp table seed_orders (id bigint primary key) on commit drop;

-- ------------------------------------------------------------------ users
with s as (
    select g, now() - random() * interval '400 days' as t
    from generate_series(1, :users) g
), ins as (
    insert into users (id, name, email, created_at, updated_at)
    select nextval('users_seq'), initcap(:'tag') || ' User ' || g, :'tag' || '-user-' || g || '@kirana.test', t, t
    from s
    returning id
)
insert into seed_users select id, row_number() over (order by id) from ins;

-- ------------------------------------------------------------------ products
with s as (
    select g,
           now() - random() * interval '365 days' as t,
           round((10 + random() * 990)::numeric, 2)::float8 as price,
           (array['Staples', 'Snacks', 'Beverages', 'Dairy', 'Personal care',
                  'Household', 'Frozen', 'Bakery', 'Spices', 'Fruits and vegetables'])[1 + g % 10] as category,
           random() < 0.02 as deleted
    from generate_series(1, :products) g
), ins as (
    insert into products (id, name, description, category, price, created_at, updated_at, deleted_at)
    select nextval('products_seq'), initcap(:'tag') || ' Product ' || g, 'Seeded for experiments', category, price, t, t,
           case when deleted then t + random() * (now() - t) end
    from s
    returning id
)
insert into seed_products select id, row_number() over (order by id) from ins;

-- ------------------------------------------------------------------ inventory
insert into inventory (product_id, quantity, updated_at)
select id, case when random() < 0.10 then 0 else floor(random() * 201)::int end, now()
from seed_products;

-- ------------------------------------------------------------------ orders
with s as (
    select u.id as user_id, u.created_at + random() * (now() - u.created_at) as t, random() as r
    from seed_users su
    join users u on u.id = su.id
    cross join generate_series(1, :orders_per_user)
), ins as (
    insert into orders (id, user_id, status, total, created_at, updated_at)
    select nextval('orders_seq'), user_id,
           case when r < 0.80 then 'PAID' when r < 0.90 then 'CREATED'
                when r < 0.95 then 'FAILED' else 'CANCELLED' end,
           0, t, t
    from s
    returning id
)
insert into seed_orders select id from ins;

-- ------------------------------------------------------------------ order_items
-- 1-5 lines per order. Products are picked at base, base + n/5, base + 2n/5, ... (mod n),
-- which are always distinct, so the (order_id, product_id) unique constraint holds
-- without an expensive random sort per order.
select count(*) as nproducts from seed_products \gset

with o as (
    select id as order_id,
           1 + floor(random() * 5)::int as lines,
           floor(random() * :nproducts)::int as base
    from seed_orders
), picked as (
    select o.order_id,
           sp.id as product_id,
           1 + floor(random() * 3)::int as qty
    from o
    cross join lateral generate_series(0, o.lines - 1) i
    join seed_products sp on sp.rn = 1 + (o.base + i * greatest(:nproducts / 5, 1)) % :nproducts
)
insert into order_items (id, order_id, product_id, product_name, unit_price, quantity, line_total)
select nextval('order_items_seq'), pk.order_id, p.id, p.name, p.price, pk.qty, p.price * pk.qty
from picked pk
join products p on p.id = pk.product_id;

-- Order total = sum of its lines, frozen (same rule as checkout).
update orders o
set total = s.total
from (select order_id, sum(line_total) as total
      from order_items
      where order_id in (select id from seed_orders)
      group by order_id) s
where o.id = s.order_id;

commit;

-- Fresh planner statistics, so query plans reflect the new row counts.
analyze;

\timing off
select 'users' as table_name, count(*) as seed_rows from users where email like :'tag' || '-user-%@kirana.test'
union all select 'products', count(*) from products where name like initcap(:'tag') || ' Product %'
union all select 'products (soft-deleted)', count(*) from products where name like initcap(:'tag') || ' Product %' and deleted_at is not null
union all select 'orders', count(*) from orders o join users u on u.id = o.user_id where u.email like :'tag' || '-user-%@kirana.test'
union all select 'order_items', count(*) from order_items oi join orders o on o.id = oi.order_id
          join users u on u.id = o.user_id where u.email like :'tag' || '-user-%@kirana.test';
