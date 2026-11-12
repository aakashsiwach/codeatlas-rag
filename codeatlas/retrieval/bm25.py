"""In-memory BM25 over chunk text with the code-aware tokenizer."""

from __future__ import annotations

import numpy as np
from rank_bm25 import BM25Okapi

from codeatlas.retrieval.tokenize import code_tokens


class BM25Index:
    def __init__(self, ids: list[str], texts: list[str]):
        self.ids = ids
        self._bm25 = BM25Okapi([code_tokens(t) for t in texts]) if ids else None

    def search(self, query: str, limit: int) -> list[tuple[str, float]]:
        if self._bm25 is None or limit <= 0:
            return []
        q = code_tokens(query)
        if not q:
            return []
        scores = np.asarray(self._bm25.get_scores(q))
        n = min(limit, len(scores))
        top = np.argpartition(-scores, n - 1)[:n]
        top = top[np.argsort(-scores[top])]
        return [(self.ids[i], float(scores[i])) for i in top if scores[i] > 0]
