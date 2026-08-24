"""RRF fusion + rerank ordering, with the repository layer stubbed out —
the SQL itself is covered by integration tests."""

import uuid

import pytest

from rag_platform.exceptions import InvalidQueryError
from rag_platform.llm.fake import FakeEmbeddingProvider, FakeReranker
from rag_platform.retrieval import repository
from rag_platform.retrieval.repository import ChunkHit
from rag_platform.retrieval.service import RetrievalService

DOC = uuid.uuid4()


def hit(chunk_id: int, content: str, score: float) -> ChunkHit:
    return ChunkHit(
        chunk_id=chunk_id,
        document_id=DOC,
        filename="f.md",
        chunk_index=chunk_id,
        content=content,
        meta={},
        score=score,
    )


def service(reranker=None) -> RetrievalService:
    return RetrievalService(
        FakeEmbeddingProvider(dim=8), reranker, k_dense=10, k_keyword=10, rrf_k=60, top_n=5
    )


@pytest.fixture
def stub_repo(monkeypatch):
    calls = {"dense": 0, "keyword": 0}

    def install(dense: list[ChunkHit], keyword: list[ChunkHit]):
        async def dense_search(session, *, tenant_id, query_embedding, k):
            calls["dense"] += 1
            return dense[:k]

        async def keyword_search(session, *, tenant_id, query, k):
            calls["keyword"] += 1
            return keyword[:k]

        monkeypatch.setattr(repository, "dense_search", dense_search)
        monkeypatch.setattr(repository, "keyword_search", keyword_search)
        return calls

    return install


async def test_hybrid_rrf_rewards_agreement(stub_repo):
    # B appears in both lists -> must fuse above everything else.
    dense = [hit(1, "A", 0.9), hit(2, "B", 0.8), hit(3, "C", 0.7)]
    keyword = [hit(2, "B", 5.0), hit(4, "D", 4.0)]
    stub_repo(dense, keyword)

    results = await service().search(None, tenant_id=uuid.uuid4(), query="q", mode="hybrid")
    assert [r.chunk_id for r in results] == [2, 1, 4, 3]
    # exact RRF math with k=60: rank r (0-based) contributes 1/(60+r+1)
    assert results[0].scores["rrf"] == pytest.approx(1 / 62 + 1 / 61)
    assert results[1].scores["rrf"] == pytest.approx(1 / 61)
    # per-source scores preserved
    assert results[0].scores["dense_cosine_sim"] == 0.8
    assert results[0].scores["keyword_ts_rank"] == 5.0
    assert "keyword_ts_rank" not in results[1].scores


async def test_reranker_reorders_fused_candidates(stub_repo):
    dense = [
        hit(1, "completely unrelated text", 0.9),
        hit(2, "tenant isolation enforced per query", 0.5),
    ]
    stub_repo(dense, [])
    results = await service(FakeReranker()).search(
        None, tenant_id=uuid.uuid4(), query="tenant isolation", mode="hybrid"
    )
    assert results[0].chunk_id == 2  # lexical-overlap rerank beats dense order
    assert results[0].scores["rerank"] > results[1].scores["rerank"]


async def test_dense_mode_never_touches_keyword_search(stub_repo):
    calls = stub_repo([hit(1, "A", 0.9)], [hit(9, "Z", 9.9)])
    results = await service().search(None, tenant_id=uuid.uuid4(), query="q", mode="dense")
    assert [r.chunk_id for r in results] == [1]
    assert calls == {"dense": 1, "keyword": 0}


async def test_top_n_truncates(stub_repo):
    stub_repo([hit(i, f"c{i}", 1 - i / 10) for i in range(1, 9)], [])
    results = await service().search(
        None, tenant_id=uuid.uuid4(), query="q", mode="hybrid", top_n=3
    )
    assert len(results) == 3


async def test_blank_query_rejected(stub_repo):
    stub_repo([], [])
    with pytest.raises(InvalidQueryError):
        await service().search(None, tenant_id=uuid.uuid4(), query="   ", mode="hybrid")
