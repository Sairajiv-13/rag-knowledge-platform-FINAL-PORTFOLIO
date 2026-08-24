"""API schemas — the public contract, kept separate from ORM models on purpose:
what we store and what we expose evolve independently (e.g. secret_hash and
tsv exist in the DB and must never appear here)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from rag_platform.models import DocumentSourceType, DocumentStatus


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class DocumentOut(BaseModel):
    id: uuid.UUID
    filename: str
    source_type: DocumentSourceType
    status: DocumentStatus
    chunk_count: int | None
    error_message: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentListOut(BaseModel):
    items: list[DocumentOut]
    total: int
    limit: int
    offset: int


class BatchItemResult(BaseModel):
    filename: str
    # Exactly one of the two is set: a registered document, or a rejection
    # reason for this file. Per-file results mean one bad file never sinks
    # the batch (ADR 0006).
    document: DocumentOut | None = None
    error: str | None = None


class DocumentBatchOut(BaseModel):
    accepted: int
    rejected: int
    results: list[BatchItemResult]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    mode: Literal["hybrid", "dense", "keyword"] = "hybrid"
    # le=50: top_n drives prompt size and rerank cost; unbounded would let one
    # request DoS the reranker and blow the context budget.
    top_n: int = Field(default=8, ge=1, le=50)


class SearchResultOut(BaseModel):
    chunk_id: int
    document_id: uuid.UUID
    filename: str
    chunk_index: int
    content: str
    location: str | None
    scores: dict[str, float]


class SearchResponse(BaseModel):
    results: list[SearchResultOut]


class AnswerRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_n: int = Field(default=8, ge=1, le=20)
    stream: bool = False


class CitationOut(BaseModel):
    marker: int  # the [n] used in the answer text
    chunk_id: int
    document_id: uuid.UUID
    filename: str
    location: str | None  # "§ Deployment > TLS" or "pp. 3-4"
    snippet: str


class UsageOut(BaseModel):
    input_tokens: int
    output_tokens: int


class AnswerResponse(BaseModel):
    answer: str
    citations: list[CitationOut]
    model: str | None  # None when answered without an LLM call (no context found)
    usage: UsageOut | None
    cost_usd: float | None


class UsageByModelOut(BaseModel):
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None


class UsageSummaryOut(BaseModel):
    days: int
    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    # None if any row lacks a cost (prices unconfigured) — a partial sum
    # presented as a total would be a lie.
    total_cost_usd: float | None
    by_model: list[UsageByModelOut]
