"""Local embedding provider: sentence-transformers, default bge-small-en-v1.5.

Chosen (ADR 0001) so ingestion and the eval harness run with zero API keys.
The model (~130MB) downloads from Hugging Face on first use and is cached.
Heavy imports are deferred and inference runs in a worker thread so the event
loop never blocks.
"""

import asyncio
import threading
from collections.abc import Sequence
from typing import Any

from rag_platform.exceptions import ConfigurationError, ProviderNotConfiguredError

# bge v1.5 models are asymmetric: queries want this instruction prefix,
# passages don't. Getting this wrong quietly costs retrieval quality.
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class LocalEmbeddingProvider:
    def __init__(self, model_name: str, expected_dim: int) -> None:
        self.model_name = model_name
        self.dim = expected_dim
        self._model: Any = None
        # Plain threading.Lock (not asyncio): the load happens inside
        # to_thread, i.e. off the event loop.
        self._load_lock = threading.Lock()

    def _get_model(self) -> Any:
        if self._model is None:
            with self._load_lock:
                if self._model is None:  # double-checked: load once per process
                    try:
                        from sentence_transformers import SentenceTransformer
                    except ImportError as exc:
                        raise ProviderNotConfiguredError(
                            "embedding_provider=local requires the 'local-inference' extra: "
                            "pip install '.[local-inference]'"
                        ) from exc
                    model = SentenceTransformer(self.model_name)
                    actual = model.get_sentence_embedding_dimension()
                    if actual != self.dim:
                        # The chunks.embedding column is vector(384); a mismatched
                        # model must fail here, not with opaque INSERT errors.
                        raise ConfigurationError(
                            f"embedding model {self.model_name} has dim {actual}, "
                            f"but the schema requires {self.dim} (see models.EMBEDDING_DIM)"
                        )
                    self._model = model
        return self._model

    def _encode(self, texts: list[str]) -> list[list[float]]:
        model = self._get_model()
        # normalize_embeddings=True: unit vectors, as bge recommends; makes
        # cosine distance in pgvector behave as expected.
        vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [v.tolist() for v in vectors]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._encode, list(texts))

    async def embed_query(self, text: str) -> list[float]:
        result = await asyncio.to_thread(self._encode, [_BGE_QUERY_PREFIX + text])
        return result[0]
