"""The stage-5 retry-exhaustion fix, pinned by a test: transient failures back
off and terminate in a FAILED row — never a forever-PROCESSING document."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from rag_platform.llm.fake import FakeEmbeddingProvider
from rag_platform.models import EMBEDDING_DIM, Document, DocumentSourceType
from rag_platform.services.ingestion import IngestionService
from rag_platform.worker import tasks


async def _register(engine, tenant_id) -> str:
    service = IngestionService(
        FakeEmbeddingProvider(dim=EMBEDDING_DIM),
        chunk_target_tokens=400,
        chunk_overlap_tokens=60,
        embed_batch_size=8,
    )
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        doc = await service.register(
            session,
            tenant_id=tenant_id,
            filename="t.md",
            raw=b"# T\n\nBody text here.\n",
            source_type=DocumentSourceType.MARKDOWN,
        )
        return str(doc.id)


async def test_transient_failure_exhausts_retries_and_marks_failed(engine, auth, monkeypatch):
    doc_id = await _register(engine, auth["tenant_id"])

    attempts = {"n": 0}

    async def always_transient(document_id):
        attempts["n"] += 1
        raise ConnectionError("simulated transient dependency failure")

    monkeypatch.setattr(tasks, "_process", always_transient)
    # Eager scaffolding: with task_eager_propagates on (our test default),
    # nested eager applies re-RAISE Retry instead of returning it, which
    # breaks celery's inline retry chain after one hop. Turning it off for
    # this test lets the chain run to exhaustion — which is the contract
    # being pinned: our pre-check fires, and the row lands in FAILED.
    monkeypatch.setattr(tasks.app.conf, "task_eager_propagates", False)
    tasks.ingest_document.apply(args=[doc_id])

    assert attempts["n"] == 4  # 1 initial + worker_max_retries(3)
    async with async_sessionmaker(engine)() as session:
        doc = (await session.execute(select(Document).where(Document.id == doc_id))).scalar_one()
    assert doc.status == "failed"
    assert "after 3 retries" in doc.error_message


async def test_completed_document_reprocessing_is_a_noop(engine, auth, client):
    resp = await client.post(
        "/v1/documents",
        headers=auth["headers"],
        files={"file": ("idem.md", b"# I\n\nIdempotency body.\n")},
    )
    doc_id = resp.json()["id"]
    tasks.ingest_document.apply(args=[doc_id])  # redelivery simulation
    doc = (await client.get(f"/v1/documents/{doc_id}", headers=auth["headers"])).json()
    assert doc["status"] == "completed" and doc["chunk_count"] == 1
