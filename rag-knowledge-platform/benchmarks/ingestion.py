"""Measure real ingestion throughput: parse -> chunk -> embed -> store.

Runs the ACTUAL IngestionService (the same code the Celery worker calls) over
a batch of generated markdown documents, synchronously and in-process, timing
end to end. This measures the CPU-bound pipeline honestly. Embedding uses the
configured provider: with the local bge model it's the real cost; with the
fake provider it's labeled plumbing-only (embedding is the dominant term, so
the fake number is a floor, not a estimate of production throughput).

Usage:
    RAG_EMBEDDING_PROVIDER=local python -m benchmarks.ingestion --docs 50
"""

import argparse
import asyncio
import time
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from rag_platform.config import get_settings
from rag_platform.ingestion.parsers import source_type_for_filename
from rag_platform.llm.factory import build_embedding_provider
from rag_platform.services.ingestion import IngestionService

_PARA = (
    "The retrieval system combines dense vector search over pgvector with "
    "keyword search over a generated tsvector column, fusing the two ranked "
    "lists with reciprocal rank fusion. Tenant isolation is enforced in the "
    "repository layer and covered by integration tests. "
)


def _make_doc(n_paragraphs: int, seq: int) -> bytes:
    # seq makes each document's content unique so the platform's content-hash
    # dedup (correct behavior) doesn't reject the run.
    body = "\n\n".join(f"## Section {seq}.{i}\n\n{_PARA * 3}" for i in range(n_paragraphs))
    return f"# Benchmark Document {seq}\n\n{body}\n".encode()


async def run(n_docs: int, paragraphs: int) -> dict:  # type: ignore[type-arg]
    settings = get_settings()
    engine = create_async_engine(str(settings.database_url), poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    embedder = build_embedding_provider(settings)
    service = IngestionService(
        embedder,
        chunk_target_tokens=settings.chunk_target_tokens,
        chunk_overlap_tokens=settings.chunk_overlap_tokens,
        embed_batch_size=settings.embed_batch_size,
    )

    # Dedicated tenant for the run.
    slug = f"bench-ingest-{uuid.uuid4().hex[:8]}"
    async with engine.begin() as conn:
        tid = uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO tenants (id, name, slug, created_at) "
                "VALUES (:id, :n, :s, now())"
            ),
            {"id": tid, "n": "Bench Ingest", "s": slug},
        )

    total_chunks = 0
    start = time.perf_counter()
    for i in range(n_docs):
        raw = _make_doc(paragraphs, i)
        async with session_factory() as session:
            document = await service.ingest(
                session,
                tenant_id=tid,
                filename=f"bench_{i}.md",
                raw=raw,
                source_type=source_type_for_filename("bench.md"),
            )
            row = (
                await session.execute(
                    text("SELECT count(*) FROM chunks WHERE document_id = :d"),
                    {"d": document.id},
                )
            ).scalar_one()
            total_chunks += int(row)
    elapsed = time.perf_counter() - start
    await engine.dispose()

    docs_per_min = round(n_docs / elapsed * 60, 1)
    chunks_per_sec = round(total_chunks / elapsed, 1)
    result = {
        "provider": settings.embedding_provider,
        "docs": n_docs,
        "paragraphs_per_doc": paragraphs,
        "total_chunks": total_chunks,
        "elapsed_s": round(elapsed, 2),
        "docs_per_min": docs_per_min,
        "chunks_per_sec": chunks_per_sec,
    }
    print(
        f"provider={result['provider']} docs={n_docs} chunks={total_chunks} "
        f"elapsed={result['elapsed_s']}s -> {docs_per_min} docs/min, "
        f"{chunks_per_sec} chunks/s"
    )
    if settings.embedding_provider == "fake":
        print("  NOTE: fake embedder — this is a PLUMBING FLOOR, not production "
              "throughput. Re-run with RAG_EMBEDDING_PROVIDER=local for real cost.")
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=50)
    ap.add_argument("--paragraphs", type=int, default=8)
    args = ap.parse_args()
    asyncio.run(run(args.docs, args.paragraphs))


if __name__ == "__main__":
    main()
