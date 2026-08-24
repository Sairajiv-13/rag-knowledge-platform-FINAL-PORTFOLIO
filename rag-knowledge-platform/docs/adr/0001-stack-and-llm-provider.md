# ADR 0001: Core stack and LLM/embedding provider strategy

Date: 2026-07-05 · Status: accepted (provider defaults open for review before stage 3)

## Context
The platform needs vector + keyword search, async ingestion, and a swappable
LLM layer, and must be runnable locally by a reviewer without cloud accounts.

## Decision
- **FastAPI + async SQLAlchemy + PostgreSQL/pgvector.** One database serves
  relational data, dense vectors, and BM25-style full-text search (tsvector),
  avoiding a separate vector DB the project's scale doesn't justify.
- **Redis** for cache + rate limiting; **Celery** (Redis broker) for ingestion,
  which is genuinely async (parse -> chunk -> embed can take minutes).
- **LLM layer is a provider interface**, not a hard dependency. Default
  generation provider: Anthropic (Claude). A deterministic fake provider ships
  for tests/CI so the suite never needs an API key.
- **Embeddings default to a local sentence-transformers model** (bge-small-en-
  v1.5, 384-dim) so ingestion and the eval harness run with zero API keys;
  an API-based embedder is a config switch behind the same interface.

## Consequences
- pgvector column dimensions are fixed per embedding model; switching models
  requires a re-embed migration (documented when the schema lands in stage 2).
- Local embeddings make the worker image heavier (PyTorch) — accepted for the
  sake of key-free reproducibility.
