-- AI track (kirana-ai/): the AI service's own schema and login role.
--
--   docker exec -i kirana-postgres-1 psql -U kirana -d kirana < infra/seed/ai-schema.sql
--
-- Safe to run more than once. Creates only the empty schema; the tables inside it belong
-- to kirana-ai's Alembic migrations, not to Kirana's Flyway.
--
-- Ownership rule: the AI service shares this Postgres *instance* but not Kirana's data.
-- kirana_ai owns schema `ai` and has no privileges on any table in `public`, so a query
-- like `SELECT * FROM public.orders` fails with "permission denied". Kirana data reaches
-- the AI service only through Kirana's REST API.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'kirana_ai') THEN
        -- Local-development password, same convention as the kirana role in docker-compose.yml.
        CREATE ROLE kirana_ai LOGIN PASSWORD 'kirana_ai';
    END IF;
END
$$;

CREATE SCHEMA IF NOT EXISTS ai AUTHORIZATION kirana_ai;

-- Unqualified table names resolve to `ai`, and only to `ai`.
ALTER ROLE kirana_ai SET search_path = ai;

-- Postgres grants USAGE on `public` to the pseudo-role PUBLIC, i.e. to every role. Kirana's
-- tables have no grants, so kirana_ai could already not read them, but it could still list
-- their names. Revoking from kirana_ai alone would do nothing (the privilege comes via
-- PUBLIC), so revoke it from PUBLIC. The only other role, kirana, owns the schema and is
-- a superuser, so it is unaffected.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
