"""Grounded answers with citations; optional SSE streaming.

Retrieval happens BEFORE the streaming response starts (it needs the
request-scoped DB session, which closes once streaming begins); the LLM
stream itself is DB-free until the final usage write.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from rag_platform.api.deps import CurrentTenant, DbSession, get_answer_service
from rag_platform.api.schemas import AnswerRequest, AnswerResponse, CitationOut, UsageOut
from rag_platform.services.answering import NO_CONTEXT_ANSWER, AnswerService, Citation, sse_encode

router = APIRouter(tags=["answers"])


def _citation_out(c: Citation) -> CitationOut:
    return CitationOut(
        marker=c.marker,
        chunk_id=c.chunk_id,
        document_id=c.document_id,
        filename=c.filename,
        location=c.location,
        snippet=c.snippet,
    )


@router.post("/answers", response_model=AnswerResponse)
async def answer(
    body: AnswerRequest,
    tenant: CurrentTenant,
    session: DbSession,
    service: Annotated[AnswerService, Depends(get_answer_service)],
) -> AnswerResponse | StreamingResponse:
    prepared = await service.prepare(
        session, tenant_id=tenant.tenant_id, query=body.query, top_n=body.top_n
    )

    if body.stream:
        async def event_stream():  # type: ignore[no-untyped-def]
            if prepared is None:
                # Same event shape as the real path so clients need one parser.
                yield sse_encode("citations", {"citations": []})
                yield sse_encode("delta", {"text": NO_CONTEXT_ANSWER})
                yield sse_encode("done", {"model": None, "usage": None, "cost_usd": None})
                return
            async for event, payload in service.stream(prepared, tenant_id=tenant.tenant_id):
                yield sse_encode(event, payload)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            # no-cache/no-buffering: proxies otherwise coalesce SSE into one blob
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if prepared is None:
        # No retrieved context -> honest refusal, zero LLM spend.
        return AnswerResponse(
            answer=NO_CONTEXT_ANSWER, citations=[], model=None, usage=None, cost_usd=None
        )
    result = await service.complete(session, prepared, tenant_id=tenant.tenant_id)
    return AnswerResponse(
        answer=result.answer,
        citations=[_citation_out(c) for c in result.citations],
        model=result.model,
        usage=None
        if result.usage is None
        else UsageOut(
            input_tokens=result.usage.input_tokens, output_tokens=result.usage.output_tokens
        ),
        cost_usd=result.cost_usd,
    )
