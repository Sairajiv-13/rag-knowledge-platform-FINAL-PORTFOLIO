"""Chunker edge cases — the module most likely to corrupt retrieval silently."""

import pytest

from rag_platform.ingestion.chunking import chunk_blocks, estimate_tokens
from rag_platform.ingestion.parsers import Block


def sentences(n: int, words_per: int = 6, tag: str = "s") -> str:
    return " ".join(
        f"{tag}{i} " + " ".join(f"w{i}_{j}" for j in range(words_per - 1)) + "." for i in range(n)
    )


def test_small_block_yields_single_chunk():
    chunks = chunk_blocks([Block(text="One tidy sentence.")], target_tokens=100, overlap_tokens=10)
    assert len(chunks) == 1
    assert chunks[0].text == "One tidy sentence."
    assert chunks[0].token_count == estimate_tokens("One tidy sentence.")


def test_packing_bounded_by_target_plus_overlap():
    blocks = [Block(text=sentences(60))]
    target, overlap = 80, 20
    chunks = chunk_blocks([*blocks], target_tokens=target, overlap_tokens=overlap)
    assert len(chunks) > 1
    # emit-before-add keeps chunks near target; the carried overlap plus one
    # sentence is the worst case — never more.
    assert all(c.token_count <= target + overlap for c in chunks)


def test_overlap_sentences_repeat_at_next_chunk_start():
    blocks = [Block(text=sentences(30))]
    chunks = chunk_blocks(blocks, target_tokens=80, overlap_tokens=20)
    assert len(chunks) >= 2
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        # the next chunk must begin with the tail of the previous one
        first_sentence_of_next = nxt.text.split(".")[0] + "."
        assert first_sentence_of_next in prev.text


def test_pathological_unpunctuated_text_terminates_and_covers_everything():
    words = [f"tok{i}" for i in range(3000)]
    blocks = [Block(text=" ".join(words))]  # one giant "sentence"
    chunks = chunk_blocks(blocks, target_tokens=100, overlap_tokens=10)
    assert len(chunks) > 10
    assert all(c.token_count <= 110 for c in chunks)
    # nothing lost: every word appears in the concatenation
    joined = " ".join(c.text for c in chunks)
    assert all(w in joined for w in (words[0], words[1500], words[-1]))


def test_overlap_must_be_smaller_than_target():
    with pytest.raises(ValueError):
        chunk_blocks([Block(text="x.")], target_tokens=50, overlap_tokens=50)


def test_empty_and_whitespace_blocks_yield_nothing():
    assert chunk_blocks([], target_tokens=50, overlap_tokens=5) == []
    assert chunk_blocks([Block(text="   \n\n  ")], target_tokens=50, overlap_tokens=5) == []


def test_page_meta_merged_and_headings_from_first_sentence():
    blocks = [
        Block(text=sentences(10, tag="a"), meta={"page": 1, "headings": ["Intro"]}),
        Block(text=sentences(10, tag="b"), meta={"page": 2, "headings": ["Body"]}),
    ]
    chunks = chunk_blocks(blocks, target_tokens=1000, overlap_tokens=50)
    assert len(chunks) == 1
    assert chunks[0].meta["page_start"] == 1 and chunks[0].meta["page_end"] == 2
    assert chunks[0].meta["headings"] == ["Intro"]
