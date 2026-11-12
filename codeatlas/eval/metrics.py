"""Retrieval and answer metrics."""

from __future__ import annotations

from collections.abc import Sequence

from codeatlas.eval.dataset import ExpectedSource
from codeatlas.models import Chunk


def recall_at_k(expected: Sequence[ExpectedSource], retrieved: Sequence[Chunk], k: int) -> float:
    """Fraction of expected sources matched by at least one of the top-k chunks."""
    if not expected:
        return 0.0
    top = retrieved[:k]
    return sum(1 for e in expected if any(e.matches(c) for c in top)) / len(expected)


def hit_at_k(expected: Sequence[ExpectedSource], retrieved: Sequence[Chunk], k: int) -> float:
    return 1.0 if any(e.matches(c) for e in expected for c in retrieved[:k]) else 0.0


def reciprocal_rank(expected: Sequence[ExpectedSource], retrieved: Sequence[Chunk]) -> float:
    for rank, chunk in enumerate(retrieved, start=1):
        if any(e.matches(chunk) for e in expected):
            return 1.0 / rank
    return 0.0


def citation_precision(expected: Sequence[ExpectedSource], cited: Sequence[Chunk]) -> float | None:
    """Fraction of cited code/doc chunks that are expected sources (graph reports are excluded)."""
    cited = [c for c in cited if c.kind != "graph"]
    if not cited:
        return None
    return sum(1 for c in cited if any(e.matches(c) for e in expected)) / len(cited)


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[idx]
