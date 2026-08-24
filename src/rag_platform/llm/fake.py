"""Deterministic fake providers.

These exist so tests, CI, and the eval harness run with zero API keys and zero
model downloads — and so failures are reproducible. They are selected via
RAG_LLM_PROVIDER=fake / RAG_EMBEDDING_PROVIDER=fake and are never a default in
any non-test environment.
"""

import hashlib
import random
import re
from collections.abc import AsyncIterator, Sequence
from functools import lru_cache

from rag_platform.llm.base import LLMResult, LLMUsage, StreamEnd, StreamEvent, TextDelta

_WORD_RE = re.compile(r"\w+")


@lru_cache(maxsize=65536)
def _token_vector(token: str, dim: int) -> tuple[float, ...]:
    # Seed from a stable hash (builtin hash() is salted per process, which
    # would silently break cross-process determinism).
    seed = int.from_bytes(hashlib.sha256(token.encode()).digest()[:8], "big")
    rng = random.Random(seed)
    return tuple(rng.gauss(0.0, 1.0) for _ in range(dim))


class FakeEmbeddingProvider:
    """Bag-of-words over random-but-stable per-token vectors, L2-normalized.

    Not semantic — but texts sharing vocabulary get high cosine similarity,
    which is exactly enough structure for retrieval tests to assert real
    ranking behavior deterministically.
    """

    model_name = "fake-embedding"

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def _embed_one(self, text: str) -> list[float]:
        acc = [0.0] * self.dim
        for token in _WORD_RE.findall(text.lower()):
            vec = _token_vector(token, self.dim)
            for i in range(self.dim):
                acc[i] += vec[i]
        norm = sum(v * v for v in acc) ** 0.5
        if norm == 0.0:  # e.g. punctuation-only text: any fixed unit vector will do
            acc[0] = 1.0
            return acc
        return [v / norm for v in acc]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text.split()))


class FakeLLMProvider:
    """Echoes a deterministic answer that includes a digest of the prompt, so
    tests can assert the right context actually reached the model."""

    model_name = "fake-llm"

    async def generate(self, *, system: str, user: str, max_tokens: int) -> LLMResult:
        text = self._answer(user)
        usage = LLMUsage(
            input_tokens=_estimate_tokens(system) + _estimate_tokens(user),
            output_tokens=_estimate_tokens(text),
        )
        return LLMResult(text=text, usage=usage, model=self.model_name)

    async def stream(
        self, *, system: str, user: str, max_tokens: int
    ) -> AsyncIterator[StreamEvent]:
        result = await self.generate(system=system, user=user, max_tokens=max_tokens)
        for word in result.text.split(" "):
            yield TextDelta(text=word + " ")
        yield StreamEnd(usage=result.usage, model=self.model_name)

    @staticmethod
    def _answer(user: str) -> str:
        digest = hashlib.sha256(user.encode()).hexdigest()[:12]
        # Echo the first two context markers as citations so the whole
        # citation pipeline (prompt -> answer -> parsed refs) is testable
        # deterministically without a real model.
        markers = re.findall(r"^\[(\d+)\]", user, flags=re.MULTILINE)
        cites = "".join(f"[{m}]" for m in markers[:2])
        return f"FAKE_ANSWER{cites} (prompt_sha={digest}, prompt_words={_estimate_tokens(user)})"


class FakeReranker:
    """Jaccard token overlap between query and text: deterministic, and close
    enough to 'relevance' that rerank-ordering tests assert real behavior."""

    model_name = "fake-reranker"

    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        q = set(_WORD_RE.findall(query.lower()))
        scores: list[float] = []
        for text in texts:
            words = set(_WORD_RE.findall(text.lower()))
            union = q | words
            scores.append(len(q & words) / len(union) if union else 0.0)
        return scores
