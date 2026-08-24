# ADR 0003: Hybrid retrieval — RRF fusion, Postgres FTS, optional cross-encoder rerank

Date: 2026-07-06 · Status: accepted

## Context
Grounded answers need chunks that are relevant both semantically ("reset my
credentials" ≈ "change your password") and lexically (exact error codes,
product names, identifiers — where dense embeddings are weak). Hence hybrid:
dense k-NN over pgvector + keyword search over Postgres full-text, fused.

## Decisions

**1. Fusion by Reciprocal Rank Fusion (RRF), not score blending.**
Cosine similarity (~0.6–0.9 for bge) and `ts_rank_cd` (unbounded,
corpus-dependent) are on incomparable scales; a weighted sum needs per-corpus
normalization that silently drifts as data changes. RRF uses only ranks:
`score(d) = Σ 1/(k + rank_i(d))`, k=60 (Cormack et al. 2009). Trade-off: score
magnitudes are discarded — a barely-first result counts like a runaway-first
one.

**2. Keyword side is Postgres FTS (`ts_rank_cd`), which is NOT true BM25.**
It has no corpus-level IDF, so it under-penalizes common terms relative to
BM25. Honest alternatives: ParadeDB's pg_search extension (real BM25 in
Postgres, but a nonstandard image), or OpenSearch/Elasticsearch (real BM25,
plus an entire second datastore to operate). Staying inside vanilla
postgres+pgvector keeps ops surface minimal; the eval harness will measure
whether the gap matters on real question sets, and the README states the
limitation plainly.

**3. Reranking: local cross-encoder (ms-marco-MiniLM-L-6-v2), implemented but
default-off.** A cross-encoder reads (query, passage) together and reliably
beats bi-encoder ordering — at real CPU latency per candidate. Defaulting it
on would be claiming a quality win we haven't measured; the eval harness
reports retrieval metrics with and without rerank, and the default flips only
if the numbers justify it. `RAG_RERANKER=cross_encoder` enables it.

**4. Tuning constants live in Settings** (`k_dense=30`, `k_keyword=30`,
`top_n=8`) and change only with eval evidence, not vibes.

## Consequences
- Retrieval quality claims are deferred to measured eval results — nothing in
  the README asserts unmeasured superiority of hybrid over dense-only.
- Per-source scores (cosine, ts_rank, rrf, rerank) are preserved on every
  result for debuggability and eval analysis.
- If BM25 fidelity proves necessary, the seam is `retrieval/repository.py`:
  swap `keyword_search`'s implementation without touching fusion or callers.
