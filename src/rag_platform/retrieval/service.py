"""Retrieval service: dense + keyword -> RRF fusion -> optional rerank.

Why Reciprocal Rank Fusion instead of blending raw scores: cosine similarity
(~0.6-0.9) and ts_rank_cd (unbounded, corpus-dependent) live on incomparable
scales, so any weighted sum needs per-corpus normalization that drifts as data
changes. RRF uses only *ranks*, which are always comparable, at the cost of
throwing away score magnitudes. Standard constant k=60 from the literature
(Cormack et al. 2009); tune only with eval-harness evidence.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from rag_platform.exceptions import InvalidQueryError
from rag_platform.llm.base import EmbeddingProvider, Reranker
from rag_platform.retrieval import repository
from rag_platform.retrieval.repository import ChunkHit

SearchMode = Literal["hybrid", "dense", "keyword"]


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: int
    document_id: uuid.UUID
    filename: str
    chunk_index: int
    content: str
    meta: dict[str, Any]
    # Full per-source breakdown (dense_cosine_sim / keyword_ts_rank / rrf /
    # rerank) — kept, not collapsed to one number, so API responses and the
    # eval harness can show WHY a chunk ranked where it did.
    scores: dict[str, float] = field(default_factory=dict)


class RetrievalService:
    def __init__(
        self,
        embedder: EmbeddingProvider,
        reranker: Reranker | None,
        *,
        k_dense: int,
        k_keyword: int,
        rrf_k: int,
        top_n: int,
    ) -> None:
        self._embedder = embedder
        self._reranker = reranker
        self._k_dense = k_dense
        self._k_keyword = k_keyword
        self._rrf_k = rrf_k
        self._top_n = top_n

    async def search(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        query: str,
        mode: SearchMode = "hybrid",
        top_n: int | None = None,
    ) -> list[RetrievedChunk]:
        if not query.strip():
            # An empty tsquery matches nothing and embedding "" is noise —
            # no sensible ranking exists, so refuse rather than guess.
            raise InvalidQueryError()
        top_n = top_n or self._top_n

        hits: dict[int, ChunkHit] = {}
        scores: dict[int, dict[str, float]] = {}
        rank_lists: list[list[int]] = []

        if mode in ("hybrid", "dense"):
            query_embedding = await self._embedder.embed_query(query)
            dense = await repository.dense_search(
                session, tenant_id=tenant_id, query_embedding=query_embedding, k=self._k_dense
            )
            rank_lists.append(self._register(dense, "dense_cosine_sim", hits, scores))
        if mode in ("hybrid", "keyword"):
            keyword = await repository.keyword_search(
                session, tenant_id=tenant_id, query=query, k=self._k_keyword
            )
            rank_lists.append(self._register(keyword, "keyword_ts_rank", hits, scores))

        if mode == "hybrid":
            fused: dict[int, float] = {}
            for ranked_ids in rank_lists:
                for rank, chunk_id in enumerate(ranked_ids):
                    fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (self._rrf_k + rank + 1)
            for chunk_id, rrf_score in fused.items():
                scores[chunk_id]["rrf"] = rrf_score
            ordered = sorted(fused, key=lambda cid: (-fused[cid], cid))
        else:
            ordered = rank_lists[0] if rank_lists else []

        if self._reranker is not None and ordered:
            rerank_scores = await self._reranker.rerank(
                query, [hits[cid].content for cid in ordered]
            )
            for chunk_id, score in zip(ordered, rerank_scores, strict=True):
                scores[chunk_id]["rerank"] = score
            ordered = sorted(ordered, key=lambda cid: (-scores[cid]["rerank"], cid))

        return [
            RetrievedChunk(
                chunk_id=cid,
                document_id=hits[cid].document_id,
                filename=hits[cid].filename,
                chunk_index=hits[cid].chunk_index,
                content=hits[cid].content,
                meta=hits[cid].meta,
                scores=scores[cid],
            )
            for cid in ordered[:top_n]
        ]

    @staticmethod
    def _register(
        result: list[ChunkHit],
        score_name: str,
        hits: dict[int, ChunkHit],
        scores: dict[int, dict[str, float]],
    ) -> list[int]:
        ranked_ids: list[int] = []
        for hit in result:
            hits.setdefault(hit.chunk_id, hit)
            scores.setdefault(hit.chunk_id, {})[score_name] = hit.score
            ranked_ids.append(hit.chunk_id)
        return ranked_ids
