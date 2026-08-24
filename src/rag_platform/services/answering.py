"""Grounded answering: retrieve -> prompt -> generate -> cite -> meter.

Flow is split into prepare() and complete()/stream() because SSE needs
retrieval done *before* the response starts (the request-scoped DB session
closes when streaming begins), and the end-of-stream usage write therefore
uses its own short-lived session from the factory.

Citations: context passages are numbered [1]..[n] in the prompt; the model is
instructed to cite markers; we parse markers back out of the answer and return
only citations the answer actually used. Markers pointing outside the provided
range (a hallucinated [9]) are dropped and logged — never surfaced as if real.
"""

import json
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_platform.llm.base import LLMProvider, LLMUsage, StreamEnd, TextDelta
from rag_platform.models import UsageRecord
from rag_platform.observability.metrics import LLM_TOKENS
from rag_platform.retrieval.service import RetrievalService, RetrievedChunk

log = structlog.get_logger(__name__)

_MARKER_RE = re.compile(r"\[(\d+)\]")

NO_CONTEXT_ANSWER = (
    "I couldn't find anything in your documents relevant to this question, "
    "so I can't give a grounded answer."
)

_SYSTEM_PROMPT = (
    "You answer questions strictly from the provided context passages.\n"
    "Rules:\n"
    "- Use ONLY information present in the context. No outside knowledge.\n"
    "- Cite every claim with the bracketed marker of the passage that supports "
    "it, e.g. [1] or [2][3].\n"
    "- If the context does not contain the answer, say so plainly instead of guessing.\n"
    "- Be concise."
)


def format_location(meta: dict) -> str | None:
    """chunks.meta -> human-readable provenance for citations."""
    headings = meta.get("headings")
    if headings:
        return "§ " + " > ".join(headings)
    start, end = meta.get("page_start"), meta.get("page_end")
    if start is not None:
        return f"p. {start}" if start == end else f"pp. {start}-{end}"
    return None


@dataclass(frozen=True)
class Citation:
    marker: int
    chunk_id: int
    document_id: uuid.UUID
    filename: str
    location: str | None
    snippet: str


@dataclass(frozen=True)
class PreparedAnswer:
    system: str
    user: str
    citations: list[Citation]  # marker-ordered, 1-based; superset of what gets cited


@dataclass(frozen=True)
class AnswerResult:
    answer: str
    citations: list[Citation]
    model: str | None
    usage: LLMUsage | None
    cost_usd: float | None


class AnswerService:
    def __init__(
        self,
        retrieval: RetrievalService,
        llm: LLMProvider,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        max_tokens: int,
        price_input_per_mtok: float | None,
        price_output_per_mtok: float | None,
    ) -> None:
        self._retrieval = retrieval
        self._llm = llm
        self._session_factory = session_factory
        self._max_tokens = max_tokens
        self._price_in = price_input_per_mtok
        self._price_out = price_output_per_mtok

    async def prepare(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, query: str, top_n: int
    ) -> PreparedAnswer | None:
        """Retrieve context and build the prompt. None => nothing retrieved;
        callers short-circuit without spending an LLM call."""
        chunks = await self._retrieval.search(
            session, tenant_id=tenant_id, query=query, mode="hybrid", top_n=top_n
        )
        if not chunks:
            return None

        citations = [
            Citation(
                marker=i,
                chunk_id=c.chunk_id,
                document_id=c.document_id,
                filename=c.filename,
                location=format_location(c.meta),
                snippet=c.content[:200],
            )
            for i, c in enumerate(chunks, start=1)
        ]
        return PreparedAnswer(
            system=_SYSTEM_PROMPT, user=self._build_user_prompt(chunks, query), citations=citations
        )

    async def complete(
        self, session: AsyncSession, prepared: PreparedAnswer, *, tenant_id: uuid.UUID
    ) -> AnswerResult:
        result = await self._llm.generate(
            system=prepared.system, user=prepared.user, max_tokens=self._max_tokens
        )
        cited = self._cited_only(result.text, prepared.citations)
        cost = self._cost(result.usage)
        await self._record_usage(
            session, tenant_id=tenant_id, model=result.model, usage=result.usage, cost=cost
        )
        return AnswerResult(
            answer=result.text,
            citations=cited,
            model=result.model,
            usage=result.usage,
            cost_usd=cost,
        )

    async def stream(
        self, prepared: PreparedAnswer, *, tenant_id: uuid.UUID
    ) -> AsyncIterator[tuple[str, dict]]:
        """Yields (event, payload) tuples: 'citations' first — the UI can show
        sources while tokens arrive — then 'delta's, then 'done' with usage.
        Streamed citations are the full retrieved set: with tokens already sent
        we can't retro-filter to only-cited markers like complete() does."""
        yield (
            "citations",
            {"citations": [self._citation_payload(c) for c in prepared.citations]},
        )
        async for event in self._llm.stream(
            system=prepared.system, user=prepared.user, max_tokens=self._max_tokens
        ):
            if isinstance(event, TextDelta):
                yield ("delta", {"text": event.text})
            elif isinstance(event, StreamEnd):
                cost = self._cost(event.usage)
                # Request-scoped session is gone once streaming started;
                # metering gets its own short-lived one.
                async with self._session_factory() as session:
                    await self._record_usage(
                        session,
                        tenant_id=tenant_id,
                        model=event.model,
                        usage=event.usage,
                        cost=cost,
                    )
                yield (
                    "done",
                    {
                        "model": event.model,
                        "usage": {
                            "input_tokens": event.usage.input_tokens,
                            "output_tokens": event.usage.output_tokens,
                        },
                        "cost_usd": cost,
                    },
                )

    @staticmethod
    def _build_user_prompt(chunks: list[RetrievedChunk], query: str) -> str:
        parts = ["Context passages:"]
        for i, chunk in enumerate(chunks, start=1):
            location = format_location(chunk.meta)
            source = f"{chunk.filename}" + (f" — {location}" if location else "")
            parts.append(f"[{i}] ({source})\n{chunk.content}")
        parts.append(f"Question: {query}")
        return "\n\n".join(parts)

    def _cited_only(self, answer: str, citations: list[Citation]) -> list[Citation]:
        available = {c.marker: c for c in citations}
        seen: list[int] = []
        for raw in _MARKER_RE.findall(answer):
            marker = int(raw)
            if marker in available and marker not in seen:
                seen.append(marker)
            elif marker not in available:
                log.warning("hallucinated_citation_marker", marker=marker)
        return [available[m] for m in sorted(seen)]

    def _cost(self, usage: LLMUsage) -> float | None:
        if self._price_in is None or self._price_out is None:
            return None  # no configured prices -> no invented cost
        return (
            usage.input_tokens * self._price_in + usage.output_tokens * self._price_out
        ) / 1_000_000

    @staticmethod
    def _citation_payload(c: Citation) -> dict:
        payload = c.__dict__.copy()
        payload["document_id"] = str(c.document_id)  # UUID isn't JSON-native
        return payload

    @staticmethod
    async def _record_usage(
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        model: str,
        usage: LLMUsage,
        cost: float | None,
    ) -> None:
        LLM_TOKENS.labels(model, "input").inc(usage.input_tokens)
        LLM_TOKENS.labels(model, "output").inc(usage.output_tokens)
        session.add(
            UsageRecord(
                tenant_id=tenant_id,
                operation="answer",
                model=model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=None if cost is None else Decimal(str(round(cost, 6))),
            )
        )
        await session.commit()


def sse_encode(event: str, payload: dict) -> str:
    """Server-Sent Events wire format; single place so the framing can't drift
    between endpoints."""
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"
