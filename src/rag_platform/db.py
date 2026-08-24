"""Database engine and session construction.

ORM models live in models.py; Alembic migrations in migrations/.
"""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str) -> AsyncEngine:
    # pool_pre_ping: transparently replace connections that died while idle
    # (e.g. Postgres restarted) instead of failing the next request.
    return create_async_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: committed objects stay readable without a
    # refresh round-trip — implicit refresh IO is a classic async footgun.
    return async_sessionmaker(engine, expire_on_commit=False)
