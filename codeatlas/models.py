"""Core data types shared by ingestion, retrieval and generation."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Chunk:
    """A retrievable unit of source: a class skeleton, a method, a doc section or a file window."""

    id: str
    path: str
    kind: str  # class | method | doc | config | code | naive | graph
    text: str  # content shown to the user and the LLM
    embed_text: str  # content plus a contextual header, used for embeddings and BM25
    start_line: int
    end_line: int
    language: str = ""
    symbol: str | None = None  # "pkg.Type" or "pkg.Type#method" for Java chunks
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def location(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Chunk:
        return cls(**data)


@dataclass
class ScoredChunk:
    chunk: Chunk
    score: float
    signals: dict[str, Any] = field(default_factory=dict)  # per-retriever scores and ranks


def make_chunk_id(path: str, kind: str, symbol: str | None, start_line: int, part: int = 0) -> str:
    """Deterministic UUID so re-indexing a file produces stable point ids in the vector store."""
    raw = f"{path}|{kind}|{symbol or ''}|{start_line}|{part}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, raw))
