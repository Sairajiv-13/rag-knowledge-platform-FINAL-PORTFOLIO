"""Build concrete providers from Settings — the single place where config
strings become objects. Everything else takes providers as constructor args."""

from rag_platform.config import Settings
from rag_platform.exceptions import ProviderNotConfiguredError
from rag_platform.llm.anthropic_provider import AnthropicProvider
from rag_platform.llm.base import EmbeddingProvider, LLMProvider, Reranker
from rag_platform.llm.fake import FakeEmbeddingProvider, FakeLLMProvider, FakeReranker
from rag_platform.llm.local_embeddings import LocalEmbeddingProvider
from rag_platform.llm.reranker import CrossEncoderReranker
from rag_platform.models import EMBEDDING_DIM


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "fake":
        return FakeEmbeddingProvider(dim=EMBEDDING_DIM)
    return LocalEmbeddingProvider(settings.embedding_model_name, expected_dim=EMBEDDING_DIM)


def build_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "fake":
        return FakeLLMProvider()
    if settings.anthropic_api_key is None:
        # Fail fast with the fix in the message, not at first request with a 401.
        raise ProviderNotConfiguredError(
            "llm_provider=anthropic requires RAG_ANTHROPIC_API_KEY to be set"
        )
    return AnthropicProvider(
        api_key=settings.anthropic_api_key.get_secret_value(),
        model=settings.anthropic_model,
        max_retries=settings.llm_max_retries,
    )


def build_reranker(settings: Settings) -> Reranker | None:
    """None means "reranking disabled" — callers branch on that, not on a
    do-nothing reranker that would hide whether reranking actually ran."""
    if settings.reranker == "none":
        return None
    if settings.reranker == "fake":
        return FakeReranker()
    return CrossEncoderReranker(settings.rerank_model_name)
