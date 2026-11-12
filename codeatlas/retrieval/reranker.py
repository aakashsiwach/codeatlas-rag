"""Cross-encoder reranking (sentence-transformers)."""

from __future__ import annotations

import math
from collections.abc import Sequence

from codeatlas.models import Chunk


def sigmoid(x: float) -> float:
    if x < -60:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


class CrossEncoderReranker:
    """Scores (query, chunk) pairs jointly. Returns raw logits; use sigmoid() for a 0-1 confidence."""

    def __init__(self, model_name: str, max_chars: int = 2000):
        self.model_name = model_name
        self.max_chars = max_chars
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as exc:
                raise ImportError("Reranking needs the local extra: pip install 'codeatlas[local]'") from exc
            self._model = CrossEncoder(self.model_name)
        return self._model

    def score(self, query: str, chunks: Sequence[Chunk]) -> list[float]:
        if not chunks:
            return []
        import torch

        model = self._load()
        pairs = [(query, c.embed_text[: self.max_chars]) for c in chunks]
        identity = torch.nn.Identity()
        try:  # sentence-transformers >= 4
            scores = model.predict(pairs, activation_fn=identity)
        except TypeError:  # older releases
            scores = model.predict(pairs, activation_fct=identity)
        return [float(s) for s in scores]
