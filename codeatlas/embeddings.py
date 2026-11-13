"""Embedding backends: local sentence-transformers, OpenAI, Voyage, and an offline hashing baseline."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Protocol

from codeatlas.config import Settings
from codeatlas.retrieval.tokenize import code_tokens


class Embedder(Protocol):
    name: str

    @property
    def dim(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Feature-hashed bag of code tokens. No model download; used for tests and as a floor baseline."""

    def __init__(self, dim: int = 512):
        self._dim = dim
        self.name = f"hashing-{dim}"

    @property
    def dim(self) -> int:
        return self._dim

    def _vector(self, text: str) -> list[float]:
        v = [0.0] * self._dim
        for tok in code_tokens(text):
            h = int.from_bytes(hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest(), "little")
            v[h % self._dim] += 1.0 if (h >> 63) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in v))
        if norm == 0:
            v[0] = 1.0
            return v
        return [x / norm for x in v]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str, batch_size: int = 32):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError("Local embeddings need the local extra: pip install 'codeatlas[local]'") from exc
        self._model = SentenceTransformer(model_name)
        self._batch_size = batch_size
        self.name = f"local:{model_name}"
        lowered = model_name.lower()
        self._query_prefix = (
            "Represent this sentence for searching relevant passages: "
            if "bge" in lowered and "-en" in lowered
            else ""
        )

    @property
    def dim(self) -> int:
        # sentence-transformers renamed the method; fall back to the old name for older releases.
        getter = getattr(self._model, "get_embedding_dimension", None) or self._model.get_sentence_embedding_dimension
        dim = getter()
        if dim is None:  # e.g. models whose output size depends on the input
            raise ValueError(f"{self.name} does not report a fixed embedding dimension; choose another model")
        return int(dim)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(
            list(texts),
            batch_size=self._batch_size,
            normalize_embeddings=True,
            show_progress_bar=len(texts) > 256,
        )
        return vectors.tolist()

    def embed_query(self, text: str) -> list[float]:
        return self._model.encode([self._query_prefix + text], normalize_embeddings=True)[0].tolist()


class OpenAIEmbedder:
    def __init__(self, model: str, batch_size: int = 128, base_url: str | None = None):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError("OpenAI embeddings need the openai extra: pip install 'codeatlas[openai]'") from exc
        self._client = OpenAI(base_url=base_url) if base_url else OpenAI()
        self._model = model
        self._batch_size = batch_size
        self._dim: int | None = None
        self.name = f"openai:{model}"

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self.embed_query("dimension probe"))
        return self._dim

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = [t[:24_000] for t in texts[i : i + self._batch_size]]
            resp = self._client.embeddings.create(model=self._model, input=batch)
            out.extend(d.embedding for d in resp.data)
        return out

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class VoyageEmbedder:
    def __init__(self, model: str, batch_size: int = 128):
        try:
            import voyageai
        except ImportError as exc:
            raise ImportError("Voyage embeddings need the voyage extra: pip install 'codeatlas[voyage]'") from exc
        self._client = voyageai.Client()
        self._model = model
        self._batch_size = batch_size
        self._dim: int | None = None
        self.name = f"voyage:{model}"

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self.embed_query("dimension probe"))
        return self._dim

    def _embed(self, texts: Sequence[str], input_type: str) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = list(texts[i : i + self._batch_size])
            out.extend(self._client.embed(batch, model=self._model, input_type=input_type).embeddings)
        return out

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts, "document")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "query")[0]


def build_embedder(settings: Settings) -> Embedder:
    provider = settings.embedding_provider.lower()
    if provider == "local":
        return SentenceTransformerEmbedder(settings.embedding_model, settings.embedding_batch_size)
    if provider == "openai":
        return OpenAIEmbedder(settings.embedding_model)
    if provider == "voyage":
        return VoyageEmbedder(settings.embedding_model)
    if provider == "hashing":
        return HashingEmbedder()
    raise ValueError(f"Unknown embedding provider: {settings.embedding_provider!r}")
