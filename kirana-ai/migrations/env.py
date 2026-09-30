"""
Alembic environment. Runs migrations as kirana_ai, inside schema `ai`.

The version table lives in `ai` too (ai.alembic_version), next to the tables
it describes and away from Kirana's flyway_schema_history in `public`.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from kirana_ai import config as app_config
from kirana_ai.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def use_qualified_names(connection) -> None:
    """
    kirana_ai's search_path is `ai`, so Postgres reports `ai` as the default
    schema, and SQLAlchemy then reflects ai.threads as plain "threads". The
    models say "ai.threads", so autogenerate would see every table as missing.
    Declaring `public` the default makes `ai` tables reflect by their full name.
    """
    connection.dialect.default_schema_name = "public"


def run_migrations_online() -> None:
    # A URL set programmatically (the tests do this) wins over the app config.
    url = config.get_main_option("sqlalchemy.url") or app_config.DATABASE_URL
    engine = create_engine(url)
    with engine.connect() as connection:
        use_qualified_names(connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema="ai",
            include_schemas=True,
            include_name=lambda name, type_, _: type_ != "schema" or name == "ai",
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline (SQL script) mode is not used here; run `alembic upgrade head`.")
run_migrations_online()
