"""Document endpoints. Every query is scoped by the authenticated tenant —
`tenant_id` comes from the token, never from the request body/path (ADR 0002)."""

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from rag_platform.api.deps import CurrentTenant, DbSession, SettingsDep, get_ingestion_service
from rag_platform.api.schemas import (
    BatchItemResult,
    DocumentBatchOut,
    DocumentListOut,
    DocumentOut,
)
from rag_platform.exceptions import (
    FileTooLargeError,
    NotFoundError,
    QueueUnavailableError,
    RagPlatformError,
)
from rag_platform.ingestion.parsers import source_type_for_filename
from rag_platform.models import Document, DocumentStatus
from rag_platform.services.answering import sse_encode
from rag_platform.services.ingestion import IngestionService

router = APIRouter(tags=["documents"])

# Terminal states: once a document reaches one, its status never changes again,
# so pollers and the SSE stream can stop.
_TERMINAL = {DocumentStatus.COMPLETED, DocumentStatus.FAILED}
# How often a client should re-poll GET /documents/{id} while non-terminal.
_POLL_INTERVAL_SECONDS = 2


@router.post("/documents", response_model=DocumentOut, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    file: UploadFile,
    tenant: CurrentTenant,
    session: DbSession,
    settings: SettingsDep,
    service: Annotated[IngestionService, Depends(get_ingestion_service)],
) -> Document:
    """202: the row is created PENDING and parse/embed happens in a worker.
    Poll GET /v1/documents/{id} for completed/failed + error_message."""
    # Deferred import: keeps route modules importable without a broker configured
    from rag_platform.worker.tasks import ingest_document

    source_type = source_type_for_filename(file.filename or "")
    raw = await file.read()  # whole file in memory: acceptable under the size cap
    if len(raw) > settings.max_upload_bytes:
        raise FileTooLargeError(f"file is {len(raw)} bytes; limit is {settings.max_upload_bytes}")
    document = await service.register(
        session,
        tenant_id=tenant.tenant_id,
        filename=file.filename or "upload",
        raw=raw,
        source_type=source_type,
    )
    try:
        ingest_document.delay(str(document.id))
    except Exception as exc:
        # Broker down AFTER the row exists: mark it failed rather than leaving
        # a forever-PENDING mystery, and tell the client to retry.
        await service.mark_failed(
            session, document_id=document.id, message="could not enqueue ingestion"
        )
        raise QueueUnavailableError() from exc
    return document


@router.post(
    "/documents/batch",
    response_model=DocumentBatchOut,
    status_code=status.HTTP_207_MULTI_STATUS,
)
async def upload_documents_batch(
    files: list[UploadFile],
    tenant: CurrentTenant,
    session: DbSession,
    settings: SettingsDep,
    service: Annotated[IngestionService, Depends(get_ingestion_service)],
) -> DocumentBatchOut:
    """Register MANY documents in one request, each still processed as its own
    task (ADR 0006). This collapses N HTTP round-trips into one — the real win
    of "batching" for bulk upload — WITHOUT coupling the documents into one
    transaction or one task, so a single corrupt file can't sink its 49
    neighbors. Returns 207 with a per-file result: registered or rejected.

    Failure isolation is deliberate: registration of each file is committed
    independently, and each is enqueued separately. One file over quota, or a
    duplicate, is reported against that file alone.
    """
    from rag_platform.worker.tasks import ingest_document

    results: list[BatchItemResult] = []
    for file in files:
        filename = file.filename or "upload"
        raw = await file.read()
        try:
            if len(raw) > settings.max_upload_bytes:
                raise FileTooLargeError(
                    f"file is {len(raw)} bytes; limit is {settings.max_upload_bytes}"
                )
            document = await service.register(
                session,
                tenant_id=tenant.tenant_id,
                filename=filename,
                raw=raw,
                source_type=source_type_for_filename(filename),
            )
            try:
                ingest_document.delay(str(document.id))
            except Exception as exc:
                await service.mark_failed(
                    session, document_id=document.id, message="could not enqueue ingestion"
                )
                raise QueueUnavailableError() from exc
            results.append(
                BatchItemResult(filename=filename, document=DocumentOut.model_validate(document))
            )
        except RagPlatformError as exc:
            # Expected rejection (too large, duplicate, quota, queue) — record
            # it against this file and keep going. Unexpected errors still 500.
            results.append(BatchItemResult(filename=filename, error=exc.detail))

    accepted = sum(1 for r in results if r.document is not None)
    return DocumentBatchOut(
        accepted=accepted, rejected=len(results) - accepted, results=results
    )


@router.get("/documents", response_model=DocumentListOut)
async def list_documents(
    tenant: CurrentTenant,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentListOut:
    where = Document.tenant_id == tenant.tenant_id
    total = (
        await session.execute(select(func.count()).select_from(Document).where(where))
    ).scalar_one()
    rows = (
        (
            await session.execute(
                select(Document)
                .where(where)
                .order_by(Document.created_at.desc(), Document.id)
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return DocumentListOut(
        items=[DocumentOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )


async def _get_owned(session: DbSession, tenant: CurrentTenant, document_id: uuid.UUID) -> Document:
    document = await session.get(Document, document_id)
    # 404 (not 403) for other tenants' documents: a 403 would confirm the id exists.
    if document is None or document.tenant_id != tenant.tenant_id:
        raise NotFoundError(f"document {document_id} not found")
    return document


@router.get("/documents/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID, tenant: CurrentTenant, session: DbSession, response: Response
) -> Document:
    document = await _get_owned(session, tenant, document_id)
    # Poll hint: while the document is still working, tell the client how long
    # to wait before asking again — a graceful fallback for clients that don't
    # use the SSE stream below. Absent once the status is terminal.
    if document.status not in _TERMINAL:
        response.headers["X-Poll-Interval"] = str(_POLL_INTERVAL_SECONDS)
    return document


@router.get("/documents/{document_id}/events")
async def document_events(
    document_id: uuid.UUID,
    tenant: CurrentTenant,
    session: DbSession,
    request: Request,
) -> StreamingResponse:
    """Server-Sent Events stream of a document's status transitions.

    Emits one `status` event immediately (current state), then one more each
    time the status changes, and closes with a `done` event when the document
    reaches a terminal state (completed/failed). Replaces poll loops for
    clients that prefer a push. Ownership is checked up front — same 404-not-403
    rule as everywhere else.

    Implementation is DB polling, not LISTEN/NOTIFY: the worker writes status
    to the row (the single source of truth, ADR 0005), and a 2s poll is
    entirely adequate for an ingestion job measured in seconds. LISTEN/NOTIFY
    would add a second delivery path to keep correct for no real latency win.
    """
    # Authorize before opening the stream.
    await _get_owned(session, tenant, document_id)
    tenant_id = tenant.tenant_id
    session_factory = request.app.state.session_factory

    async def event_stream():  # type: ignore[no-untyped-def]
        last_status: str | None = None
        # Safety bound: never stream forever if something wedges upstream.
        deadline = asyncio.get_event_loop().time() + 300
        while True:
            async with session_factory() as s:
                doc = await s.get(Document, document_id)
            if doc is None or doc.tenant_id != tenant_id:
                yield sse_encode("error", {"detail": "document not found"})
                return
            if doc.status != last_status:
                last_status = doc.status
                yield sse_encode(
                    "status",
                    {
                        "document_id": str(document_id),
                        "status": doc.status,
                        "chunk_count": doc.chunk_count,
                        "error_message": doc.error_message,
                    },
                )
            if doc.status in _TERMINAL:
                yield sse_encode("done", {"status": doc.status})
                return
            if asyncio.get_event_loop().time() > deadline:
                # Non-terminal after the bound: tell the client to fall back to
                # polling rather than hold the connection open indefinitely.
                yield sse_encode(
                    "timeout",
                    {"status": doc.status, "poll_after": _POLL_INTERVAL_SECONDS},
                )
                return
            # Stop early if the client disconnected.
            if await request.is_disconnected():
                return
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID, tenant: CurrentTenant, session: DbSession
) -> None:
    document = await _get_owned(session, tenant, document_id)
    await session.delete(document)  # chunks go with it (FK ON DELETE CASCADE)
    await session.commit()
