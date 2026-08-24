"""Application configuration.

All runtime configuration comes from environment variables (12-factor); a local
`.env` file is read for developer convenience only and is never committed.
Secrets added in later stages use pydantic's SecretStr so they can't leak via
repr()/logging.
"""

from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn, RedisDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_prefix namespaces our variables (RAG_*) so they can't collide with
    # anything else already exported on the host, e.g. a global DATABASE_URL.
    model_config = SettingsConfigDict(env_file=".env", env_prefix="RAG_", extra="ignore")

    app_name: str = "rag-knowledge-platform"
    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    # No defaults for connection URLs: the app should fail loudly at startup if
    # they're missing rather than silently connect to a wrong localhost service.
    database_url: PostgresDsn
    redis_url: RedisDsn

    # --- auth (ADR 0004) ---
    # Required, no default: a guessable fallback secret would silently make
    # every deployment forgeable. Compose sets a dev-only value.
    jwt_secret: SecretStr
    jwt_ttl_seconds: int = 1800  # short-lived: revocation lag is bounded by this

    # --- worker / queue ---
    # Broker shares the one Redis (separate concerns get separate DBs at scale;
    # one instance is honest for this footprint). Eager mode runs tasks inline
    # in-process — used by tests, never in deployment.
    celery_eager: bool = False
    worker_max_retries: int = 3
    worker_retry_backoff_seconds: int = 10  # 10s, 20s, 40s; overridable for tests

    # --- rate limiting (per tenant: issuing more credentials must not
    # multiply a tenant's quota) ---
    rate_limit_per_minute: int = 120
    rate_limit_token_per_minute: int = 20  # token endpoint, keyed by client_id

    # --- observability ---
    # console: spans to stdout (dev verification); otlp: real collector
    otel_exporter: Literal["none", "console", "otlp"] = "none"
    otel_endpoint: str | None = None  # OTLP HTTP endpoint when otel_exporter=otlp

    # --- API limits ---
    max_upload_bytes: int = 10 * 1024 * 1024  # uploads are read into memory (see route)
    # Per-tenant total stored-bytes cap (sum of documents.size_bytes). Default
    # 500MB: generous for the demo, finite enough that a tenant can't exhaust
    # disk. 0 disables the check (single-tenant/self-host convenience).
    max_tenant_storage_bytes: int = 500 * 1024 * 1024

    # --- answering ---
    answer_max_tokens: int = 1024
    # Passed to the Anthropic SDK's built-in exponential backoff (429/5xx/
    # connection errors) — deliberately not a second hand-rolled retry layer.
    llm_max_retries: int = 3
    # USD per 1M tokens. Unset -> usage rows record NULL cost rather than a
    # number we invented; set these to the prices you actually pay.
    price_input_per_mtok: float | None = None
    price_output_per_mtok: float | None = None

    # --- providers (ADR 0001) ---
    llm_provider: Literal["anthropic", "fake"] = "anthropic"
    anthropic_api_key: SecretStr | None = None  # required iff llm_provider=anthropic
    anthropic_model: str = "claude-sonnet-4-6"
    embedding_provider: Literal["local", "fake"] = "local"
    embedding_model_name: str = "BAAI/bge-small-en-v1.5"  # dim must stay 384 (schema)
    embed_batch_size: int = 64

    # --- chunking ---
    # ~400-token chunks: big enough that a chunk usually carries a whole idea,
    # small enough that several fit in a prompt alongside the question. Kept
    # comfortably under bge's 512-token cap: sentence-transformers silently
    # TRUNCATES longer inputs, and our counts are word-based estimates.
    chunk_target_tokens: int = 400
    chunk_overlap_tokens: int = 60

    # --- retrieval (ADR 0003) ---
    retrieval_k_dense: int = 30
    retrieval_k_keyword: int = 30
    retrieval_rrf_k: int = 60  # standard RRF damping constant from the literature
    retrieval_top_n: int = 8  # candidates surviving fusion (and rerank, if enabled)
    # Off until the eval harness justifies the latency (ADR 0003)
    reranker: Literal["none", "cross_encoder", "fake"] = "none"
    rerank_model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    @model_validator(mode="after")
    def _validate_chunking(self) -> "Settings":
        if self.chunk_overlap_tokens >= self.chunk_target_tokens:
            raise ValueError("chunk_overlap_tokens must be < chunk_target_tokens")
        return self


@lru_cache
def get_settings() -> Settings:
    """Build Settings once per process; also overridable in tests via DI."""
    return Settings()  # type: ignore[call-arg]  # fields come from the environment
