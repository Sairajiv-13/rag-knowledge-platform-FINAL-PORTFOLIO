"""Cross-encoder reranker (ms-marco-MiniLM by default).

A cross-encoder reads query and passage *together*, so it can judge relevance
far better than the bi-encoder similarity used for candidate retrieval — at
~10-50ms per pair on CPU. That cost is why it only sees the fused top
candidates, never the whole corpus, and why it's off by default until the
eval harness shows the quality gain is worth the latency (ADR 0003).
"""

import asyncio
import threading
from collections.abc import Sequence
from typing import Any

from rag_platform.exceptions import ProviderNotConfiguredError


class CrossEncoderReranker:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model: Any = None
        self._load_lock = threading.Lock()  # load happens inside to_thread

    def _get_model(self) -> Any:
        if self._model is None:
            with self._load_lock:
                if self._model is None:
                    try:
                        from sentence_transformers import CrossEncoder
                    except ImportError as exc:
                        raise ProviderNotConfiguredError(
                            "reranker=cross_encoder requires the 'local-inference' extra: "
                            "pip install '.[local-inference]'"
                        ) from exc
                    self._model = CrossEncoder(self.model_name)
        return self._model

    def _predict(self, query: str, texts: list[str]) -> list[float]:
        model = self._get_model()
        scores = model.predict([(query, t) for t in texts], show_progress_bar=False)
        return [float(s) for s in scores]

    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        return await asyncio.to_thread(self._predict, query, list(texts))
