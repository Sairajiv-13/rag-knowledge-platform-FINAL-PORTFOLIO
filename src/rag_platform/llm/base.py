"""Provider interfaces (ADR 0001).

Everything downstream (ingestion, retrieval, answering) depends on these
Protocols, never on a concrete SDK, so swapping Anthropic for another vendor —
or for the deterministic fake in tests — is a config change, not a refactor.

Both protocols are async even though the local embedder is CPU-bound under the
hood; implementations own the thread-offloading so callers never block the
event loop by accident.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMUsage:
    """Token counts as reported by the provider — the raw input for
    per-tenant cost tracking (stage 4)."""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class LLMResult:
    text: str
    usage: LLMUsage
    model: str


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class StreamEnd:
    """Terminal stream event. Usage only becomes known at end-of-stream, and an
    async generator can't `return` a value — hence an explicit event."""

    usage: LLMUsage
    model: str


StreamEvent = TextDelta | StreamEnd


class LLMProvider(Protocol):
    async def generate(self, *, system: str, user: str, max_tokens: int) -> LLMResult: ...

    def stream(
        self, *, system: str, user: str, max_tokens: int
    ) -> AsyncIterator[StreamEvent]: ...


class EmbeddingProvider(Protocol):
    dim: int
    model_name: str

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    # Separate from embed_documents because asymmetric models (e.g. the bge
    # family) want an instruction prefix on queries but not on passages.
    async def embed_query(self, text: str) -> list[float]: ...


class Reranker(Protocol):
    """Scores (query, text) pairs; higher = more relevant. Only the *relative*
    order of scores is meaningful — cross-encoder logits are not probabilities."""

    model_name: str

    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]: ...
