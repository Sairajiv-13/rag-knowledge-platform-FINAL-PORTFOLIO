"""The fakes must be deterministic and carry enough similarity structure that
ranking tests mean something."""

import pytest

from rag_platform.llm.fake import FakeEmbeddingProvider, FakeLLMProvider, FakeReranker


def dot(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


async def test_embeddings_deterministic_and_normalized():
    p = FakeEmbeddingProvider(dim=32)
    v1 = await p.embed_query("postgres vector index")
    v2 = await p.embed_query("postgres vector index")
    assert v1 == v2
    assert len(v1) == 32
    assert dot(v1, v1) == pytest.approx(1.0, abs=1e-9)


async def test_embeddings_have_lexical_similarity_structure():
    p = FakeEmbeddingProvider(dim=64)
    base = await p.embed_query("postgres vector index tuning")
    near = await p.embed_query("tuning a vector index")
    far = await p.embed_query("banana smoothie recipe")
    assert dot(base, near) > dot(base, far)


async def test_embedding_empty_text_is_a_valid_unit_vector():
    p = FakeEmbeddingProvider(dim=8)
    v = (await p.embed_documents(["...", ""]))[0]
    assert dot(v, v) == pytest.approx(1.0, abs=1e-9)


async def test_fake_llm_cites_first_two_context_markers_deterministically():
    llm = FakeLLMProvider()
    user = (
        "Context passages:\n\n[1] (a.md)\nAlpha.\n\n[2] (b.md)\nBeta.\n\n"
        "[3] (c.md)\nGamma.\n\nQuestion: q?"
    )
    r1 = await llm.generate(system="s", user=user, max_tokens=100)
    r2 = await llm.generate(system="s", user=user, max_tokens=100)
    assert r1.text == r2.text
    assert "[1]" in r1.text and "[2]" in r1.text and "[3]" not in r1.text
    assert r1.usage.input_tokens > 0 and r1.usage.output_tokens > 0


async def test_fake_llm_stream_matches_generate():
    llm = FakeLLMProvider()
    user = "[1] (a.md)\nAlpha.\n\nQuestion: q?"
    full = await llm.generate(system="s", user=user, max_tokens=10)
    deltas, ends = [], []
    async for event in llm.stream(system="s", user=user, max_tokens=10):
        (deltas if hasattr(event, "text") else ends).append(event)
    assert "".join(d.text for d in deltas).strip() == full.text
    assert len(ends) == 1 and ends[0].usage == full.usage and ends[0].model == full.model


async def test_fake_reranker_prefers_lexical_overlap():
    scores = await FakeReranker().rerank(
        "tenant isolation policy",
        ["tenant isolation is enforced per query", "unrelated pasta recipe"],
    )
    assert scores[0] > scores[1]
