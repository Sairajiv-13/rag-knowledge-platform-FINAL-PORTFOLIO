"""Integration fixtures: a real Postgres (recreated per session, migrated with
the real Alembic migrations), the real app behind httpx, fake providers, and
eager Celery so the whole upload->worker->completed path runs in-process.

Environment MUST be set before any rag_platform import (Settings and the
Celery app read it at first touch) — hence the top-of-file assignments.
"""
# ruff: noqa: E402  (env vars must precede rag_platform imports — see docstring)

import asyncio
import os
from pathlib import Path

ROOT = Path(__file__).parents[2]
TEST_DB_URL = os.environ.get(
    "RAG_TEST_DATABASE_URL", "postgresql+asyncpg://rag:rag@localhost:5432/rag_test"
)
os.environ["RAG_DATABASE_URL"] = TEST_DB_URL
os.environ.setdefault("RAG_REDIS_URL", "redis://localhost:6379/1")
os.environ["RAG_JWT_SECRET"] = "integration-test-secret"
os.environ["RAG_EMBEDDING_PROVIDER"] = "fake"
os.environ["RAG_LLM_PROVIDER"] = "fake"
os.environ["RAG_RERANKER"] = "fake"
os.environ["RAG_CELERY_EAGER"] = "true"
os.environ["RAG_MAX_UPLOAD_BYTES"] = "100000"  # small cap so the 413 path is testable

import pytest
from alembic import command
from alembic.config import Config
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from rag_platform import security
from rag_platform.config import Settings, get_settings

get_settings.cache_clear()  # anything cached before this file ran saw the wrong env


async def _recreate_database() -> None:
    url = make_url(TEST_DB_URL)
    admin = create_async_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    await admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """Fresh DB per test session, migrated by the REAL migrations — the test
    schema can never drift from what production would run."""
    asyncio.run(_recreate_database())
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")


@pytest.fixture
async def engine():
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_tables(engine):
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE usage_records, chunks, api_credentials, documents, tenants "
                "RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
async def app():
    from rag_platform.main import create_app

    application = create_app(Settings())
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def make_auth(engine, client):
    """Factory: create tenant + credential in the DB, get a real token via the
    API, return everything a test needs to act as that tenant."""

    async def _make(slug: str) -> dict:
        from rag_platform.models import ApiCredential, Tenant

        client_id = security.generate_client_id()
        client_secret = security.generate_client_secret()
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            tenant = Tenant(name=slug.title(), slug=slug)
            session.add(tenant)
            await session.flush()
            session.add(
                ApiCredential(
                    tenant_id=tenant.id,
                    name="test",
                    client_id=client_id,
                    secret_hash=security.hash_secret(client_secret),
                )
            )
            await session.commit()
            tenant_id = tenant.id
        resp = await client.post(
            "/v1/auth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        assert resp.status_code == 200, resp.text
        token = resp.json()["access_token"]
        return {
            "tenant_id": tenant_id,
            "client_id": client_id,
            "client_secret": client_secret,
            "headers": {"Authorization": f"Bearer {token}"},
        }

    return _make


@pytest.fixture
async def auth(make_auth):
    return await make_auth("acme")
