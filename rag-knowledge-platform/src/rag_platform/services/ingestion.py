"""Ingestion: raw bytes -> parsed -> chunked -> embedded -> stored.

Split into register() (fast, in the upload request) and process() (heavy, in a
Celery worker — stage 5). The CLI's ingest() runs both in-process. The
documents row is the ONLY status source of truth: no Celery result backend to
disagree with it.

Failure semantics:
- ParseError is PERMANENT: process() marks the row failed itself; retrying a
  corrupt PDF can never help.
- Anything else is treated as TRANSIENT: process() re-raises and leaves the row
  in PROCESSING; the *caller* (the task) owns retries and marks the row failed
  only when they're exhausted, via mark_failed().
"""

import hashlib
import uuid

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from rag_platform.exceptions import (
    DuplicateDocumentError,
    NotFoundError,
    ParseError,
    StorageQuotaExceededError,
)
from rag_platform.ingestion.chunking import ChunkDraft, chunk_blocks
from rag_platform.ingestion.parsers import parse_document
from rag_platform.llm.base import EmbeddingProvider
from rag_platform.models import Chunk, Document, DocumentSourceType, DocumentStatus, Tenant

log = structlog.get_logger(__name__)


class IngestionService:
    def __init__(
        self,
        embedder: EmbeddingProvider,
        *,
        chunk_target_tokens: int,
        chunk_overlap_tokens: int,
        embed_batch_size: int,
        max_tenant_storage_bytes: int = 0,
    ) -> None:
        self._embedder = embedder
        self._target = chunk_target_tokens
        self._overlap = chunk_overlap_tokens
        self._batch_size = embed_batch_size
        # 0 disables the quota (single-tenant/self-host). Enforced in register().
        self._max_tenant_storage = max_tenant_storage_bytes

    async def register(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        filename: str,
        raw: bytes,
        source_type: DocumentSourceType,
    ) -> Document:
        """Create the PENDING row (with the payload) — cheap enough for the
        request path; the heavy work happens in process()."""
        tenant = await session.get(Tenant, tenant_id)
        if tenant is None:
            raise NotFoundError(f"tenant {tenant_id} not found")

        # Quota check BEFORE building the row: sum the tenant's existing
        # size_bytes (cheap — indexed by tenant_id, no raw_content loaded) and
        # refuse if this upload would push it over. func.coalesce handles the
        # tenant's first upload (SUM over zero rows is NULL).
        if self._max_tenant_storage > 0:
            used = await session.scalar(
                select(func.coalesce(func.sum(Document.size_bytes), 0)).where(
                    Document.tenant_id == tenant_id
                )
            )
            used_bytes = int(used or 0)
            if used_bytes + len(raw) > self._max_tenant_storage:
                raise StorageQuotaExceededError(
                    used_bytes=used_bytes,
                    limit_bytes=self._max_tenant_storage,
                    incoming_bytes=len(raw),
                )

        document = Document(
            tenant_id=tenant_id,
            filename=filename,
            source_type=source_type,
            content_sha256=hashlib.sha256(raw).hexdigest(),
            size_bytes=len(raw),
            status=DocumentStatus.PENDING,
            raw_content=raw,
        )
        session.add(document)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            # The (tenant_id, content_sha256) unique constraint is the dedup
            # mechanism — the DB decides, not a racy SELECT-then-INSERT.
            raise DuplicateDocumentError() from exc
        return document

    async def process(self, session: AsyncSession, *, document_id: uuid.UUID) -> Document:
        """Parse -> chunk -> embed -> store. Idempotent: COMPLETED rows no-op
        (at-least-once delivery with acks_late means re-runs WILL happen), and
        stale chunks from a crashed prior attempt are deleted before insert."""
        document = (
            await session.execute(
                select(Document)
                .where(Document.id == document_id)
                .options(undefer(Document.raw_content))
            )
        ).scalar_one_or_none()
        if document is None:
            raise NotFoundError(f"document {document_id} not found")
        if document.status == DocumentStatus.COMPLETED:
            log.info("document_already_ingested", document_id=str(document_id))
            return document
        if document.raw_content is None:
            raise ParseError("no stored payload for this document (uploaded pre-async-pipeline)")

        document.status = DocumentStatus.PROCESSING
        await session.commit()

        try:
            raw = document.raw_content
            drafts = self._parse_and_chunk(raw, document.source_type)
            embeddings = await self._embed_all(drafts)

            await session.execute(delete(Chunk).where(Chunk.document_id == document.id))
            session.add_all(
                Chunk(
                    document_id=document.id,
                    tenant_id=document.tenant_id,
                    chunk_index=i,
                    content=draft.text,
                    token_count=draft.token_count,
                    embedding=embedding,
                    meta=draft.meta,
                )
                for i, (draft, embedding) in enumerate(zip(drafts, embeddings, strict=True))
            )
            document.status = DocumentStatus.COMPLETED
            document.chunk_count = len(drafts)
            document.error_message = None
            await session.commit()
        except ParseError as exc:
            # Permanent: no retry can fix a corrupt/unreadable file.
            await session.rollback()
            await self.mark_failed(session, document_id=document.id, message=exc.detail)
            raise
        except Exception:
            # Transient until proven otherwise: leave PROCESSING; the caller
            # (worker task) owns retries and final failure marking.
            await session.rollback()
            raise

        log.info(
            "document_ingested",
            document_id=str(document.id),
            tenant_id=str(document.tenant_id),
            filename=document.filename,
            chunks=document.chunk_count,
        )
        return document

    async def ingest(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        filename: str,
        raw: bytes,
        source_type: DocumentSourceType,
    ) -> Document:
        """Synchronous register+process — the CLI path (operator tooling)."""
        document = await self.register(
            session, tenant_id=tenant_id, filename=filename, raw=raw, source_type=source_type
        )
        return await self.process(session, document_id=document.id)

    @staticmethod
    async def mark_failed(session: AsyncSession, *, document_id: uuid.UUID, message: str) -> None:
        """Terminal failure, written on its own so it survives whatever broke
        processing. Safe on a fresh session."""
        document = await session.get(Document, document_id)
        if document is None:  # deleted while in-flight; nothing to record
            return
        document.status = DocumentStatus.FAILED
        document.error_message = message[:500]
        await session.commit()
        log.warning("document_ingestion_failed", document_id=str(document_id), error=message)

    def _parse_and_chunk(self, raw: bytes, source_type: DocumentSourceType) -> list[ChunkDraft]:
        blocks = parse_document(raw, source_type)
        return chunk_blocks(blocks, target_tokens=self._target, overlap_tokens=self._overlap)

    async def _embed_all(self, drafts: list[ChunkDraft]) -> list[list[float]]:
        # Batched: bounds peak memory and, for API-backed embedders later,
        # respects request-size limits.
        embeddings: list[list[float]] = []
        for start in range(0, len(drafts), self._batch_size):
            batch = drafts[start : start + self._batch_size]
            embeddings.extend(await self._embedder.embed_documents([d.text for d in batch]))
        return embeddings
