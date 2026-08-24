"""Seed synthetic chunks for latency benchmarking.

WHY SYNTHETIC IS VALID: pgvector's HNSW scan and Postgres' GIN/tsvector lookup
cost depends on the NUMBER and DIMENSIONALITY of vectors and the size of the
text index — NOT on whether the vectors carry real semantic meaning. Random
unit vectors exercise the exact same index machinery as real embeddings, so
they give honest LATENCY numbers. They say nothing about retrieval QUALITY —
that is what evals/ measures, with real models. We keep the two concerns
strictly separate and label everything.

Usage:
    python -m benchmarks.seed --chunks 10000 --tenant-slug bench-10k
"""

import argparse
import asyncio
import random
import uuid

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from rag_platform.config import get_settings
from rag_platform.models import EMBEDDING_DIM

WORDS = (
    "system database vector index latency throughput tenant document chunk "
    "embedding retrieval ranking postgres redis celery worker ingestion "
    "search query answer citation token cost metric scaling cache broker "
    "migration schema isolation deployment observability benchmark corpus"
).split()


def _random_unit_vector(rng: np.random.Generator) -> list[float]:
    v = rng.standard_normal(EMBEDDING_DIM)
    v /= np.linalg.norm(v)
    return v.astype(float).tolist()


def _random_text(rng: random.Random) -> str:
    n = rng.randint(40, 80)
    return " ".join(rng.choices(WORDS, k=n))


async def seed(n_chunks: int, tenant_slug: str, batch: int = 500) -> None:
    settings = get_settings()
    engine = create_async_engine(str(settings.database_url))
    np_rng = np.random.default_rng(42)
    py_rng = random.Random(42)

    async with engine.begin() as conn:
        # Clean re-seed so numbers are never contaminated by a previous run.
        await conn.execute(
            text("DELETE FROM tenants WHERE slug = :slug"), {"slug": tenant_slug}
        )
        tenant_id = uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO tenants (id, name, slug, created_at) "
                "VALUES (:id, :name, :slug, now())"
            ),
            {"id": tenant_id, "name": f"Bench {tenant_slug}", "slug": tenant_slug},
        )
        doc_id = uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO documents (id, tenant_id, filename, source_type, "
                "content_sha256, status, created_at, updated_at) VALUES "
                "(:id, :tid, :fn, 'markdown', :sha, 'completed', now(), now())"
            ),
            {
                "id": doc_id,
                "tid": tenant_id,
                "fn": f"bench_{tenant_slug}.md",
                "sha": uuid.uuid4().hex + uuid.uuid4().hex,
            },
        )

        inserted = 0
        while inserted < n_chunks:
            m = min(batch, n_chunks - inserted)
            # Generate the whole batch of vectors in one numpy call, then
            # normalize row-wise — far cheaper than a Python loop per vector.
            mat = np_rng.standard_normal((m, EMBEDDING_DIM))
            mat /= np.linalg.norm(mat, axis=1, keepdims=True)
            rows = [
                {
                    "tid": str(tenant_id),
                    "did": str(doc_id),
                    "idx": inserted + i,
                    "content": _random_text(py_rng),
                    "tok": 60,
                    "emb": "[" + ",".join(f"{x:.6f}" for x in mat[i]) + "]",
                }
                for i in range(m)
            ]
            await conn.execute(
                text(
                    "INSERT INTO chunks (document_id, tenant_id, chunk_index, "
                    "content, token_count, embedding, created_at) VALUES "
                    "(:did, :tid, :idx, :content, :tok, CAST(:emb AS vector), now())"
                ),
                rows,
            )
            inserted += len(rows)
            print(f"  seeded {inserted}/{n_chunks}", end="\r")
    print(f"\nseeded tenant '{tenant_slug}' with {n_chunks} chunks")
    await engine.dispose()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", type=int, required=True)
    ap.add_argument("--tenant-slug", required=True)
    args = ap.parse_args()
    asyncio.run(seed(args.chunks, args.tenant_slug))


if __name__ == "__main__":
    main()
