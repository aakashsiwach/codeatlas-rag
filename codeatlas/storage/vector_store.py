"""Qdrant-backed vector store (embedded local mode, in-memory, or a Qdrant server)."""

from __future__ import annotations

import warnings
from collections.abc import Sequence

from qdrant_client import QdrantClient, models

from codeatlas.config import Settings
from codeatlas.models import Chunk


class IndexLockedError(RuntimeError):
    """The embedded index is open in another process (embedded Qdrant allows one process at a time)."""


class VectorStore:
    def __init__(self, client: QdrantClient, collection: str, dim: int):
        self.client = client
        self.collection = collection
        self.dim = dim
        self.ensure_collection()

    @classmethod
    def from_settings(cls, settings: Settings, dim: int) -> VectorStore:
        if settings.qdrant_url == ":memory:":
            client = QdrantClient(location=":memory:")
        elif settings.qdrant_url:
            client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
        else:
            path = settings.index_dir / "qdrant"
            path.mkdir(parents=True, exist_ok=True)
            try:
                client = QdrantClient(path=str(path))
            except RuntimeError as exc:
                if "already accessed" not in str(exc):
                    raise
                raise IndexLockedError(
                    f"The index at {settings.index_dir} is in use by another CodeAtlas process (for example "
                    "`codeatlas serve` or another eval). Stop it, use a different --index-dir, or set "
                    "CODEATLAS_QDRANT_URL to a Qdrant server for concurrent access."
                ) from exc
        return cls(client, settings.collection, dim)

    def ensure_collection(self) -> None:
        if self.client.collection_exists(self.collection):
            return
        self.client.create_collection(
            self.collection,
            vectors_config=models.VectorParams(size=self.dim, distance=models.Distance.COSINE),
        )
        with warnings.catch_warnings():  # embedded mode ignores payload indexes and warns about it
            warnings.simplefilter("ignore")
            self.client.create_payload_index(
                self.collection, field_name="path", field_schema=models.PayloadSchemaType.KEYWORD
            )

    def collection_dim(self) -> int | None:
        vectors = self.client.get_collection(self.collection).config.params.vectors
        return getattr(vectors, "size", None)

    def recreate(self) -> None:
        if self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)
        self.ensure_collection()

    def upsert(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]], batch_size: int = 256) -> None:
        for i in range(0, len(chunks), batch_size):
            points = [
                models.PointStruct(
                    id=c.id,
                    vector=[float(x) for x in v],
                    payload={"path": c.path, "kind": c.kind, "symbol": c.symbol},
                )
                for c, v in zip(chunks[i : i + batch_size], vectors[i : i + batch_size])
            ]
            self.client.upsert(self.collection, points=points, wait=True)

    def delete_path(self, path: str) -> None:
        self.client.delete(
            self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key="path", match=models.MatchValue(value=path))]
                )
            ),
            wait=True,
        )

    def search(self, vector: Sequence[float], limit: int) -> list[tuple[str, float]]:
        result = self.client.query_points(
            self.collection, query=[float(x) for x in vector], limit=limit, with_payload=False
        )
        return [(str(p.id), float(p.score)) for p in result.points]

    def count(self) -> int:
        return self.client.count(self.collection, exact=True).count
