"""Retrieval endpoint: returns chunks with the full score breakdown so callers
(and the eval harness) can see why each result ranked where it did."""

from typing import Annotated

from fastapi import APIRouter, Depends

from rag_platform.api.deps import CurrentTenant, DbSession, get_retrieval_service
from rag_platform.api.schemas import SearchRequest, SearchResponse, SearchResultOut
from rag_platform.retrieval.service import RetrievalService
from rag_platform.services.answering import format_location

router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    tenant: CurrentTenant,
    session: DbSession,
    service: Annotated[RetrievalService, Depends(get_retrieval_service)],
) -> SearchResponse:
    results = await service.search(
        session, tenant_id=tenant.tenant_id, query=body.query, mode=body.mode, top_n=body.top_n
    )
    return SearchResponse(
        results=[
            SearchResultOut(
                chunk_id=r.chunk_id,
                document_id=r.document_id,
                filename=r.filename,
                chunk_index=r.chunk_index,
                content=r.content,
                location=format_location(r.meta),
                scores=r.scores,
            )
            for r in results
        ]
    )
