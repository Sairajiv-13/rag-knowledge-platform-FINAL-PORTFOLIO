"""Prompt construction, citation filtering, cost math, SSE framing."""

import uuid

import pytest

from rag_platform.llm.base import LLMResult, LLMUsage
from rag_platform.retrieval.service import RetrievedChunk
from rag_platform.services.answering import (
    AnswerService,
    Citation,
    PreparedAnswer,
    format_location,
    sse_encode,
)


def test_format_location_variants():
    assert format_location({"headings": ["A", "B"]}) == "§ A > B"
    assert format_location({"page_start": 3, "page_end": 3}) == "p. 3"
    assert format_location({"page_start": 3, "page_end": 5}) == "pp. 3-5"
    assert format_location({}) is None


def test_sse_encode_wire_format():
    assert sse_encode("delta", {"text": "hi"}) == 'event: delta\ndata: {"text": "hi"}\n\n'


class StubLLM:
    model_name = "stub"

    def __init__(self, text: str, usage: LLMUsage):
        self._text, self._usage = text, usage

    async def generate(self, *, system: str, user: str, max_tokens: int) -> LLMResult:
        return LLMResult(text=self._text, usage=self._usage, model=self.model_name)


class StubRetrieval:
    def __init__(self, chunks):
        self._chunks = chunks

    async def search(self, session, *, tenant_id, query, mode="hybrid", top_n=None):
        return self._chunks


class StubSession:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


def chunk(i: int) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=i,
        document_id=uuid.uuid4(),
        filename=f"doc{i}.md",
        chunk_index=0,
        content=f"content of passage {i}",
        meta={"headings": [f"H{i}"]},
        scores={},
    )


def svc(llm, retrieval=None, price_in=None, price_out=None) -> AnswerService:
    return AnswerService(
        retrieval,
        llm,
        session_factory=None,  # factory only used by stream()
        max_tokens=100,
        price_input_per_mtok=price_in,
        price_output_per_mtok=price_out,
    )


async def test_prepare_builds_numbered_prompt_and_citations():
    service = svc(StubLLM("x", LLMUsage(1, 1)), retrieval=StubRetrieval([chunk(1), chunk(2)]))
    prepared = await service.prepare(None, tenant_id=uuid.uuid4(), query="why?", top_n=5)
    assert prepared is not None
    assert "[1] (doc1.md — § H1)" in prepared.user and "[2] (doc2.md — § H2)" in prepared.user
    assert prepared.user.rstrip().endswith("Question: why?")
    assert [c.marker for c in prepared.citations] == [1, 2]


async def test_prepare_returns_none_without_context():
    service = svc(StubLLM("x", LLMUsage(1, 1)), retrieval=StubRetrieval([]))
    assert await service.prepare(None, tenant_id=uuid.uuid4(), query="why?", top_n=5) is None


def prepared_with(n: int) -> PreparedAnswer:
    citations = [
        Citation(
            marker=i,
            chunk_id=i,
            document_id=uuid.uuid4(),
            filename=f"d{i}.md",
            location=None,
            snippet="s",
        )
        for i in range(1, n + 1)
    ]
    return PreparedAnswer(system="sys", user="usr", citations=citations)


async def test_complete_keeps_only_real_cited_markers_and_drops_hallucinated():
    llm = StubLLM("Claim [2]. Again [2]. Bogus [9]. Also [1].", LLMUsage(100, 10))
    service = svc(llm)
    session = StubSession()
    result = await service.complete(session, prepared_with(3), tenant_id=uuid.uuid4())
    assert [c.marker for c in result.citations] == [1, 2]  # deduped, sorted, [9] dropped
    assert len(session.added) == 1  # usage recorded exactly once
    record = session.added[0]
    assert record.input_tokens == 100 and record.output_tokens == 10
    assert record.cost_usd is None  # no configured prices -> no invented cost


async def test_cost_computed_only_with_prices():
    llm = StubLLM("ok [1]", LLMUsage(1_000_000, 100_000))
    service = svc(llm, price_in=3.0, price_out=15.0)
    session = StubSession()
    result = await service.complete(session, prepared_with(1), tenant_id=uuid.uuid4())
    assert result.cost_usd == pytest.approx(3.0 + 1.5)
    assert float(session.added[0].cost_usd) == pytest.approx(4.5)
