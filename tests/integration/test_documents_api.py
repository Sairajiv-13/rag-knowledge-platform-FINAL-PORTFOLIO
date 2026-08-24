"""Upload -> eager worker -> status lifecycle, including every rejection path."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from rag_platform.models import Chunk

MD = b"# Retries\n\nTransient failures retry with backoff. Permanent failures never retry.\n"


async def upload(client, auth, name: str, content: bytes):
    return await client.post(
        "/v1/documents", headers=auth["headers"], files={"file": (name, content)}
    )


async def test_upload_is_202_then_completed_with_chunks(client, auth, engine):
    resp = await upload(client, auth, "retries.md", MD)
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"  # snapshot at accept time

    resp = await client.get(f"/v1/documents/{body['id']}", headers=auth["headers"])
    doc = resp.json()
    assert doc["status"] == "completed"  # eager worker already ran
    assert doc["chunk_count"] >= 1 and doc["error_message"] is None

    async with async_sessionmaker(engine)() as session:
        n = (
            await session.execute(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.document_id == uuid.UUID(body["id"]))
            )
        ).scalar_one()
    assert n == doc["chunk_count"]


async def test_duplicate_content_same_tenant_is_409(client, auth):
    assert (await upload(client, auth, "a.md", MD)).status_code == 202
    resp = await upload(client, auth, "renamed.md", MD)  # same bytes, new name
    assert resp.status_code == 409


async def test_corrupt_pdf_becomes_failed_with_reason_not_a_retry_storm(client, auth):
    resp = await upload(client, auth, "bad.pdf", b"\x00\x01 definitely not a pdf")
    assert resp.status_code == 202
    doc = (await client.get(f"/v1/documents/{resp.json()['id']}", headers=auth["headers"])).json()
    assert doc["status"] == "failed"
    assert "invalid PDF" in doc["error_message"]


async def test_unsupported_extension_is_415(client, auth):
    resp = await upload(client, auth, "x.docx", b"whatever")
    assert resp.status_code == 415
    assert ".pdf" in resp.json()["detail"]


async def test_oversized_upload_is_413(client, auth):
    big = b"# Big\n\n" + b"word " * 40_000  # > the 100KB test cap
    resp = await upload(client, auth, "big.md", big)
    assert resp.status_code == 413


async def test_delete_cascades_chunks(client, auth, engine):
    doc_id = (await upload(client, auth, "gone.md", b"# G\n\nShort lived doc.\n")).json()["id"]
    assert (
        await client.delete(f"/v1/documents/{doc_id}", headers=auth["headers"])
    ).status_code == 204
    assert (await client.get(f"/v1/documents/{doc_id}", headers=auth["headers"])).status_code == 404
    async with async_sessionmaker(engine)() as session:
        n = (
            await session.execute(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.document_id == uuid.UUID(doc_id))
            )
        ).scalar_one()
    assert n == 0


async def test_list_paginates_and_counts(client, auth):
    for i in range(3):
        assert (
            await upload(client, auth, f"d{i}.md", f"# D{i}\n\nBody {i}.\n".encode())
        ).status_code == 202
    page = (await client.get("/v1/documents?limit=2&offset=0", headers=auth["headers"])).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    page2 = (await client.get("/v1/documents?limit=2&offset=2", headers=auth["headers"])).json()
    assert len(page2["items"]) == 1


async def test_storage_quota_exceeded_is_413(engine, make_auth):
    """A tenant whose stored bytes would exceed the cap gets 413 — enforced
    before the row is written, so the over-limit upload leaves no trace."""
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient

    from rag_platform.config import Settings
    from rag_platform.main import create_app

    # Tiny cap so two small uploads cross it deterministically.
    app = create_app(Settings(max_tenant_storage_bytes=200))
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            auth = await make_auth(f"quota-{uuid.uuid4().hex[:8]}")
            first = b"# Doc one\n\n" + b"a" * 120  # ~130 bytes, under 200
            r1 = await c.post(
                "/v1/documents", headers=auth["headers"], files={"file": ("one.md", first)}
            )
            assert r1.status_code == 202, r1.text

            second = b"# Doc two\n\n" + b"b" * 120  # would push total over 200
            r2 = await c.post(
                "/v1/documents", headers=auth["headers"], files={"file": ("two.md", second)}
            )
            assert r2.status_code == 413, r2.text
            assert "quota" in r2.json()["detail"].lower()

            # the rejected upload wrote nothing: still exactly one document
            listing = await c.get("/v1/documents", headers=auth["headers"])
            assert listing.json()["total"] == 1


async def test_size_bytes_is_recorded_on_upload(client, auth, engine):
    from rag_platform.models import Document

    payload = MD
    resp = await upload(client, auth, "sized.md", payload)
    assert resp.status_code == 202
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        size = await session.scalar(
            select(Document.size_bytes).where(Document.id == uuid.UUID(resp.json()["id"]))
        )
    assert size == len(payload)


async def test_poll_interval_header_absent_when_terminal(client, auth):
    """A completed document (eager worker finishes it) carries no poll hint;
    the header only appears while work remains."""
    resp = await upload(client, auth, "polled.md", MD)
    doc_id = resp.json()["id"]
    got = await client.get(f"/v1/documents/{doc_id}", headers=auth["headers"])
    assert got.json()["status"] == "completed"
    assert "x-poll-interval" not in {k.lower() for k in got.headers}


async def test_events_stream_emits_status_then_done(client, auth):
    """The SSE stream reports the (already terminal, under eager worker) status
    and closes with a done event."""
    resp = await upload(client, auth, "streamed.md", MD)
    doc_id = resp.json()["id"]
    async with client.stream(
        "GET", f"/v1/documents/{doc_id}/events", headers=auth["headers"]
    ) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = ""
        async for chunk in r.aiter_text():
            body += chunk
            if "event: done" in body:
                break
    assert "event: status" in body
    assert '"status": "completed"' in body
    assert "event: done" in body


async def test_events_stream_404_for_other_tenant(client, make_auth):
    a = await make_auth(f"ev-a-{uuid.uuid4().hex[:6]}")
    b = await make_auth(f"ev-b-{uuid.uuid4().hex[:6]}")
    resp = await upload(client, {"headers": a["headers"]}, "mine.md", MD)
    doc_id = resp.json()["id"]
    # tenant B cannot open tenant A's event stream
    r = await client.get(f"/v1/documents/{doc_id}/events", headers=b["headers"])
    assert r.status_code == 404


async def test_batch_upload_registers_many_in_one_call(client, auth):
    """Batch endpoint accepts multiple files in one request and processes each
    as its own task (eager worker completes them)."""
    files = [
        ("files", ("batch_a.md", b"# A\n\nAlpha document body.\n", "text/markdown")),
        ("files", ("batch_b.md", b"# B\n\nBravo document body.\n", "text/markdown")),
        ("files", ("batch_c.md", b"# C\n\nCharlie document body.\n", "text/markdown")),
    ]
    resp = await client.post("/v1/documents/batch", headers=auth["headers"], files=files)
    assert resp.status_code == 207, resp.text
    body = resp.json()
    assert body["accepted"] == 3 and body["rejected"] == 0
    assert all(r["document"] is not None for r in body["results"])


async def test_batch_upload_isolates_failures(client, auth):
    """One bad file (unsupported type) is rejected individually; its neighbors
    still succeed — the failure-isolation guarantee (ADR 0006)."""
    files = [
        ("files", ("good1.md", b"# Good\n\nFirst good body.\n", "text/markdown")),
        ("files", ("bad.xyz", b"not a supported type", "application/octet-stream")),
        ("files", ("good2.md", b"# Good\n\nSecond good body.\n", "text/markdown")),
    ]
    resp = await client.post("/v1/documents/batch", headers=auth["headers"], files=files)
    assert resp.status_code == 207
    body = resp.json()
    assert body["accepted"] == 2 and body["rejected"] == 1
    rejected = [r for r in body["results"] if r["error"] is not None]
    assert len(rejected) == 1 and rejected[0]["filename"] == "bad.xyz"
