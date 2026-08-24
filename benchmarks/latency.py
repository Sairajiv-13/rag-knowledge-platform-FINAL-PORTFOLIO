"""Measure hybrid-search latency percentiles against seeded corpora.

Calls the real RetrievalService.search() against real Postgres+pgvector, so
the numbers reflect the actual dense (HNSW) + keyword (GIN) + RRF fusion path.
The LLM is never involved — this isolates retrieval/index latency from
generation variance, which is the number that actually scales with corpus size.

For each (corpus_size, concurrency) cell it fires N queries through a bounded
worker pool and reports p50/p95/p99.

Usage:
    python -m benchmarks.latency --sizes 1000 10000 50000 \
        --concurrency 1 8 32 --requests 200
"""

import argparse
import asyncio
import time

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from benchmarks.seed import seed
from rag_platform.config import get_settings
from rag_platform.llm.fake import FakeEmbeddingProvider, FakeReranker
from rag_platform.models import EMBEDDING_DIM
from rag_platform.retrieval.service import RetrievalService

QUERIES = [
    "vector index latency under load",
    "tenant isolation and document retrieval",
    "celery worker ingestion throughput",
    "postgres schema migration and scaling",
    "embedding cache and query ranking",
    "cost metering per tenant token usage",
]


async def _measure_cell(
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
    tenant_id: str,
    service: RetrievalService,
    n_requests: int,
    concurrency: int,
) -> dict[str, float]:
    import uuid as _uuid

    tid = _uuid.UUID(tenant_id)
    latencies: list[float] = []
    sem = asyncio.Semaphore(concurrency)

    async def one(i: int) -> None:
        q = QUERIES[i % len(QUERIES)]
        async with sem:
            async with session_factory() as session:
                start = time.perf_counter()
                await service.search(session, tenant_id=tid, query=q, mode="hybrid", top_n=5)
                latencies.append((time.perf_counter() - start) * 1000.0)

    # warm the pool/index cache so cold-start doesn't skew p50
    await one(0)
    latencies.clear()

    await asyncio.gather(*(one(i) for i in range(n_requests)))
    arr = np.array(latencies)
    return {
        "p50_ms": round(float(np.percentile(arr, 50)), 1),
        "p95_ms": round(float(np.percentile(arr, 95)), 1),
        "p99_ms": round(float(np.percentile(arr, 99)), 1),
        "mean_ms": round(float(arr.mean()), 1),
    }


async def _tenant_id_for(engine, slug: str) -> str:  # type: ignore[no-untyped-def]
    async with engine.connect() as conn:
        row = (
            await conn.execute(text("SELECT id FROM tenants WHERE slug = :s"), {"s": slug})
        ).first()
        return str(row[0])


async def run(
    sizes: list[int], concurrency: list[int], requests: int, do_seed: bool = True
) -> dict:  # type: ignore[type-arg]
    settings = get_settings()
    # NullPool: each cell opens its own connections, so pool contention is not
    # silently part of the measurement.
    engine = create_async_engine(str(settings.database_url), poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    # Fake embedder is fine: it returns a deterministic 384-dim query vector,
    # and dense-search latency depends on the index, not the vector's meaning.
    service = RetrievalService(
        FakeEmbeddingProvider(EMBEDDING_DIM), FakeReranker(),
        k_dense=20, k_keyword=20, rrf_k=60, top_n=5,
    )
    results: dict = {"cells": []}  # type: ignore[type-arg]
    for size in sizes:
        slug = f"bench-{size}"
        if do_seed:
            await seed(size, slug)
        tenant_id = await _tenant_id_for(engine, slug)
        for c in concurrency:
            cell = await _measure_cell(session_factory, tenant_id, service, requests, c)
            cell.update({"corpus_chunks": size, "concurrency": c, "requests": requests})
            results["cells"].append(cell)
            print(
                f"  {size:>6} chunks | c={c:>2} | "
                f"p50={cell['p50_ms']:>6} p95={cell['p95_ms']:>6} p99={cell['p99_ms']:>6} ms"
            )
    await engine.dispose()
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[1000, 10000, 50000])
    ap.add_argument("--concurrency", type=int, nargs="+", default=[1, 8, 32])
    ap.add_argument("--requests", type=int, default=200)
    ap.add_argument("--no-seed", action="store_true", help="measure over already-seeded tenants")
    ap.add_argument("--out", type=str, default=None, help="write results JSON here")
    args = ap.parse_args()
    results = asyncio.run(
        run(args.sizes, args.concurrency, args.requests, do_seed=not args.no_seed)
    )
    if args.out:
        import json
        from pathlib import Path

        Path(args.out).write_text(json.dumps(results, indent=2))
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
