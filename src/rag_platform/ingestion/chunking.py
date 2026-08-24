"""Chunking: Blocks -> ChunkDrafts.

Strategy: split blocks into sentences, greedily pack sentences up to a token
target, carry the trailing sentences forward as overlap. Sentences are never
split (except pathological ones longer than the whole budget), so a chunk
boundary can't land mid-thought.

Token counts are ESTIMATES (words / 0.75 — the usual English prose ratio).
Chunk sizing only needs a budget, not exactness, and a real tokenizer would
tie us to one vendor's tokenization and require a network download. The stored
chunks.token_count carries the same caveat.
"""

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from rag_platform.ingestion.parsers import Block

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n{2,}")


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text.split()) / 0.75))


@dataclass(frozen=True)
class ChunkDraft:
    text: str
    token_count: int
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _Sentence:
    text: str
    tokens: int
    meta: dict[str, Any]


def _sentences(blocks: Sequence[Block], hard_limit: int) -> Iterator[_Sentence]:
    for block in blocks:
        for raw in _SENTENCE_SPLIT_RE.split(block.text):
            raw = " ".join(raw.split())  # collapse internal whitespace/newlines
            if not raw:
                continue
            tokens = estimate_tokens(raw)
            if tokens <= hard_limit:
                yield _Sentence(raw, tokens, block.meta)
                continue
            # Pathological "sentence" (minified text, giant table row): hard-split
            # by words so no single unit can ever exceed the chunk budget.
            words = raw.split()
            step = max(1, int(hard_limit * 0.75))
            for i in range(0, len(words), step):
                piece = " ".join(words[i : i + step])
                yield _Sentence(piece, estimate_tokens(piece), block.meta)


def _merge_meta(sentences: Sequence[_Sentence]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    pages = sorted({s.meta["page"] for s in sentences if "page" in s.meta})
    if pages:
        meta["page_start"], meta["page_end"] = pages[0], pages[-1]
    # Heading path of the chunk's first sentence: "where the chunk begins" is
    # the honest citation for a chunk that straddles a section boundary.
    headings = sentences[0].meta.get("headings")
    if headings:
        meta["headings"] = list(headings)
    return meta


def chunk_blocks(
    blocks: Sequence[Block], *, target_tokens: int, overlap_tokens: int
) -> list[ChunkDraft]:
    if overlap_tokens >= target_tokens:  # Settings validates too; guard direct callers
        raise ValueError("overlap_tokens must be < target_tokens")

    chunks: list[ChunkDraft] = []
    current: list[_Sentence] = []
    current_tokens = 0
    has_new = False  # anything beyond carried-over overlap in `current`?

    def emit(*, carry_overlap: bool) -> None:
        nonlocal current, current_tokens, has_new
        chunks.append(
            ChunkDraft(
                text=" ".join(s.text for s in current),
                token_count=current_tokens,
                meta=_merge_meta(current),
            )
        )
        carried: list[_Sentence] = []
        if carry_overlap:
            budget = 0
            for sentence in reversed(current):
                if budget + sentence.tokens > overlap_tokens:
                    break
                carried.insert(0, sentence)
                budget += sentence.tokens
            if len(carried) == len(current):
                # Overlap must never be the *whole* chunk or we'd loop forever
                # re-emitting the same text.
                carried = []
        current = carried
        current_tokens = sum(s.tokens for s in carried)
        has_new = False

    for sentence in _sentences(blocks, hard_limit=target_tokens):
        if current and current_tokens + sentence.tokens > target_tokens:
            emit(carry_overlap=True)
        current.append(sentence)
        current_tokens += sentence.tokens
        has_new = True

    if has_new:  # skip a final chunk that would be pure overlap of the previous one
        emit(carry_overlap=False)
    return chunks
