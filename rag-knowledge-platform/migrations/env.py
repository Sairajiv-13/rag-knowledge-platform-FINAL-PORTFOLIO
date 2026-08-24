"""Alembic environment.

The database URL comes from the same pydantic Settings the app uses
(RAG_DATABASE_URL), so migrations can never target a different database than
the service by accident. Runs on the async engine because that's the only
driver we ship (asyncpg).
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from rag_platform.config import get_settings
from rag_platform.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    # NOTE: Settings validates *all* env vars, so RAG_REDIS_URL must be set
    # even for migrations — acceptable, since anywhere migrations run the full
    # app environment exists anyway.
    return str(get_settings().database_url)


def run_migrations_offline() -> None:
    """Emit SQL to stdout (--sql flag) without a DB connection — used to
    review migration SQL and for DBA-gated environments."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    # compare_type=True so `alembic check` catches column type drift too.
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
