"""Ingestion task.

Sync Celery task driving async application code via asyncio.run — one code
path shared with the API/CLI instead of a parallel sync implementation.

Engine per invocation (NullPool): asyncpg connections are bound to the event
loop that created them, and each asyncio.run() is a fresh loop — a shared
module-level engine here fails with cross-loop errors. The embedder, by
contrast, IS a process-wide singleton: reloading a local model per task would
dominate runtime.

Retry policy:
- ParseError / NotFoundError: permanent — no retry (the row is already marked
  failed by process(), or the document was deleted mid-flight).
- Everything else: transient — exponential backoff (base*2^n), then a terminal
  FAILED mark when retries are exhausted, so nothing is ever left stuck in
  PROCESSING forever.
"""

import asyncio
import concurrent.futures
import uuid
from collections.abc import Coroutine
from functools import lru_cache
from typing import Any

import structlog
from celery import Task
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from rag_platform.config import get_settings
from rag_platform.exceptions import NotFoundError, ParseError
from rag_platform.llm.base import EmbeddingProvider
from rag_platform.llm.factory import build_embedding_provider
from rag_platform.services.ingestion import IngestionService
from rag_platform.worker.celery_app import app

log = structlog.get_logger(__name__)


def _run_coro(coro: Coroutine[Any, Any, None]) -> None:
    """asyncio.run, but safe when a loop is already running: Celery EAGER mode
    (tests) executes the task inline inside the API's async request handler,
    where asyncio.run() raises RuntimeError. A real worker process has no
    running loop and takes the plain path; the eager path runs the coroutine
    on a fresh loop in a throwaway thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(asyncio.run, coro).result()


@lru_cache(maxsize=1)
def _embedder() -> EmbeddingProvider:
    return build_embedding_provider(get_settings())


async def _process(document_id: uuid.UUID) -> None:
    settings = get_settings()
    engine = create_async_engine(str(settings.database_url), poolclass=NullPool)
    try:
        service = IngestionService(
            _embedder(),
            chunk_target_tokens=settings.chunk_target_tokens,
            chunk_overlap_tokens=settings.chunk_overlap_tokens,
            embed_batch_size=settings.embed_batch_size,
        )
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await service.process(session, document_id=document_id)
    finally:
        await engine.dispose()


async def _mark_failed(document_id: uuid.UUID, message: str) -> None:
    settings = get_settings()
    engine = create_async_engine(str(settings.database_url), poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await IngestionService.mark_failed(session, document_id=document_id, message=message)
    finally:
        await engine.dispose()


@app.task(bind=True, name="rag_platform.ingest_document")
def ingest_document(self: Task, document_id: str) -> None:
    doc_id = uuid.UUID(document_id)
    settings = get_settings()
    try:
        _run_coro(_process(doc_id))
    except ParseError:
        # Row already marked failed with the parse reason; retrying can't help.
        log.warning("ingestion_permanent_failure", document_id=document_id)
    except NotFoundError:
        log.info("ingestion_skipped_document_gone", document_id=document_id)
    except Exception as exc:
        # NOTE: celery's retry(exc=...) re-raises *exc* — not
        # MaxRetriesExceededError — once retries are exhausted, so catching
        # MaxRetriesExceededError here would silently never fire. Checking the
        # counter ourselves is explicit and version-proof.
        if self.request.retries >= settings.worker_max_retries:
            message = f"ingestion failed after {settings.worker_max_retries} retries"
            _run_coro(_mark_failed(doc_id, message))
            log.error(
                "ingestion_retries_exhausted",
                document_id=document_id,
                error_type=type(exc).__name__,
            )
            # Swallow: the terminal state is recorded on the row; re-raising
            # would only add a spurious task-failure traceback for a case
            # that IS handled.
            return
        backoff = settings.worker_retry_backoff_seconds * (2**self.request.retries)
        log.warning(
            "ingestion_transient_failure",
            document_id=document_id,
            attempt=self.request.retries + 1,
            retry_in_s=backoff,
            error_type=type(exc).__name__,
        )
        raise self.retry(
            exc=exc, countdown=backoff, max_retries=settings.worker_max_retries
        ) from exc
