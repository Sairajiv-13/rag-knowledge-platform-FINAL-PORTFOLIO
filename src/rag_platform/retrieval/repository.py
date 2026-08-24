"""Tenant-scoped retrieval queries.

tenant_id is a *required* keyword argument on every function, and no function
in this module can query across tenants — that's the whole enforcement
mechanism of ADR 0002, so keep it that way.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Row, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_platform.models import Chunk, Document


@dataclass(frozen=True)
class ChunkHit:
    chunk_id: int
    document_id: uuid.UUID
    filename: str
    chunk_index: int
    content: str
    meta: dict[str, Any]
    score: float  # source-specific: cosine similarity OR ts_rank_cd — not comparable


_COLUMNS = (
    Chunk.id,
    Chunk.document_id,
    Document.filename,
    Chunk.chunk_index,
    Chunk.content,
    Chunk.meta,
)


def _to_hits(rows: Sequence[Row[Any]]) -> list[ChunkHit]:
    return [
        ChunkHit(
            chunk_id=r.id,
            document_id=r.document_id,
            filename=r.filename,
            chunk_index=r.chunk_index,
            content=r.content,
            meta=r.meta,
            score=float(r.score),
        )
        for r in rows
    ]


async def dense_search(
    session: AsyncSession, *, tenant_id: uuid.UUID, query_embedding: list[float], k: int
) -> list[ChunkHit]:
    distance = Chunk.embedding.cosine_distance(query_embedding)
    stmt = (
        # Score reported as similarity (1 - distance) so "higher is better"
        # holds across the whole retrieval layer.
        select(*_COLUMNS, (1 - distance).label("score"))
        .join(Document, Chunk.document_id == Document.id)
        .where(Chunk.tenant_id == tenant_id)
        .order_by(distance)
        .limit(k)
    )
    return _to_hits((await session.execute(stmt)).all())


async def keyword_search(
    session: AsyncSession, *, tenant_id: uuid.UUID, query: str, k: int
) -> list[ChunkHit]:
    # websearch_to_tsquery over plainto_: users get quoted phrases, OR, and -,
    # and malformed input degrades gracefully instead of erroring.
    tsquery = func.websearch_to_tsquery("english", query)
    # ts_rank_cd is NOT true BM25 (no corpus-level IDF) — accepted trade-off
    # of staying inside Postgres; see ADR 0003 for the honest comparison.
    rank = func.ts_rank_cd(Chunk.tsv, tsquery)
    stmt = (
        select(*_COLUMNS, rank.label("score"))
        .join(Document, Chunk.document_id == Document.id)
        .where(Chunk.tenant_id == tenant_id, Chunk.tsv.op("@@")(tsquery))
        .order_by(rank.desc(), Chunk.id)  # id tiebreak: deterministic order for tests
        .limit(k)
    )
    return _to_hits((await session.execute(stmt)).all())
