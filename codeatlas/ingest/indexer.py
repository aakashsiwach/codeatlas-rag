"""Full and incremental indexing: only files whose content hash changed are re-chunked and re-embedded."""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codeatlas.config import Settings
from codeatlas.embeddings import Embedder
from codeatlas.ingest.docs import chunk_markdown, chunk_naive, chunk_pdf, chunk_plain
from codeatlas.ingest.java import JavaFileFacts, parse_java
from codeatlas.ingest.text import estimate_tokens
from codeatlas.ingest.walker import SourceFile, discover, load_source_file
from codeatlas.models import Chunk
from codeatlas.storage.chunk_store import ChunkStore
from codeatlas.storage.vector_store import VectorStore

CHUNKERS = ("ast", "naive")


class IndexMismatchError(RuntimeError):
    """The existing index was built with a different embedder or chunker."""


@dataclass
class IndexRunStats:
    repo: str
    mode: str  # full | incremental | targeted
    chunker: str
    embedder: str
    started_at: str
    files_scanned: int = 0
    files_changed: int = 0
    files_unchanged: int = 0
    files_deleted: int = 0
    chunks_written: int = 0
    # Chunks longer than the embedding model reads (estimated tokens): their tail is invisible to vector search.
    chunks_over_embed_limit: int = 0
    embed_tokens: int = 0
    embed_cost_usd: float = 0.0
    duration_s: float = 0.0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Indexer:
    def __init__(self, settings: Settings, store: ChunkStore, vectors: VectorStore, embedder: Embedder):
        self.settings = settings
        self.store = store
        self.vectors = vectors
        self.embedder = embedder
        self.batch_size = max(16, settings.embedding_batch_size * 4)

    def run(
        self,
        repo: Path,
        *,
        force: bool = False,
        chunker: str = "ast",
        only_paths: Iterable[str] | None = None,
    ) -> IndexRunStats:
        if chunker not in CHUNKERS:
            raise ValueError(f"Unknown chunker {chunker!r}; expected one of {CHUNKERS}")
        repo = repo.resolve()
        start = time.perf_counter()
        force = self._prepare(force, chunker)

        existing = self.store.file_hashes()
        if only_paths is None:
            files = discover(repo, self.settings.exclude_dirs, self.settings.max_file_bytes)
            present = {f.path for f in files}
            deleted = sorted(p for p in existing if p not in present)
            mode = "full" if force or not existing else "incremental"
        else:
            files, deleted = [], []
            for rel in sorted({p.replace("\\", "/").lstrip("/") for p in only_paths}):
                sf = load_source_file(repo, rel, self.settings.exclude_dirs, self.settings.max_file_bytes)
                if sf is not None:
                    files.append(sf)
                elif rel in existing and not (repo / rel).is_file():
                    deleted.append(rel)
            mode = "targeted"

        stats = IndexRunStats(
            repo=str(repo),
            mode=mode,
            chunker=chunker,
            embedder=self.embedder.name,
            started_at=datetime.now(timezone.utc).isoformat(),
            files_scanned=len(files),
        )
        for path in deleted:
            self.vectors.delete_path(path)
            self.store.remove_file(path)
        stats.files_deleted = len(deleted)

        changed = [f for f in files if force or existing.get(f.path) != f.sha256]
        stats.files_changed = len(changed)
        stats.files_unchanged = len(files) - len(changed)

        pending: list[tuple[SourceFile, list[Chunk], JavaFileFacts | None]] = []
        pending_chunks = 0
        for sf in changed:
            try:
                chunks, facts = self.chunk_file(sf, chunker)
            except Exception as exc:  # noqa: BLE001 - one bad file must not stop the run
                stats.errors.append(f"{sf.path}: {exc}")
                continue
            pending.append((sf, chunks, facts))
            pending_chunks += len(chunks)
            if pending_chunks >= self.batch_size:
                self._flush(pending, stats)
                pending, pending_chunks = [], 0
        self._flush(pending, stats)

        stats.embed_cost_usd = round(stats.embed_tokens * self.settings.embedding_cost_per_mtok / 1_000_000, 6)
        stats.duration_s = round(time.perf_counter() - start, 2)
        self.store.record_run(stats.to_dict())
        return stats

    def chunk_file(self, sf: SourceFile, chunker: str = "ast") -> tuple[list[Chunk], JavaFileFacts | None]:
        if sf.language == "pdf":
            return chunk_pdf(sf.path, sf.abs_path, self.settings.doc_chunk_chars), None
        text = sf.abs_path.read_bytes().decode("utf-8", errors="replace")
        if chunker == "naive":
            return chunk_naive(sf.path, text, sf.language, self.settings.naive_chunk_chars), None
        if sf.category == "java":
            facts, chunks = parse_java(sf.path, text, self.settings.max_chunk_lines)
            if not chunks:  # package-info.java, module-info.java, unparseable files
                chunks = chunk_plain(sf.path, text, "code", "java")
            return chunks, facts
        if sf.category == "doc" and sf.language in ("markdown", "asciidoc"):
            return chunk_markdown(sf.path, text, self.settings.doc_chunk_chars, sf.language), None
        kind = "doc" if sf.category == "doc" else "config"
        return chunk_plain(sf.path, text, kind, sf.language), None

    # --- internals -------------------------------------------------------------------------

    def _prepare(self, force: bool, chunker: str) -> bool:
        """Validate that the existing index is compatible; reset it when forced. Returns force."""
        has_data = bool(self.store.file_hashes())
        wanted = {"embedder": self.embedder.name, "dim": str(self.embedder.dim), "chunker": chunker}
        if has_data and not force:
            mismatched = [
                f"{key}: index has {self.store.get_meta(key)!r}, requested {value!r}"
                for key, value in wanted.items()
                if self.store.get_meta(key) not in (None, value)
            ]
            dim = self.vectors.collection_dim()
            if dim is not None and dim != self.embedder.dim:
                mismatched.append(f"vector size: collection has {dim}, embedder has {self.embedder.dim}")
            if mismatched:
                raise IndexMismatchError(
                    "The existing index is incompatible (" + "; ".join(mismatched) + "). Re-run with --force."
                )
            if self.vectors.count() == 0 and self.store.count_chunks() > 0:
                force = True  # vectors were lost (e.g. a fresh Qdrant volume): rebuild everything
        if force:
            self.store.clear()
            self.vectors.recreate()
        for key, value in wanted.items():
            self.store.set_meta(key, value)
        return force

    def _flush(
        self, pending: list[tuple[SourceFile, list[Chunk], JavaFileFacts | None]], stats: IndexRunStats
    ) -> None:
        if not pending:
            return
        all_chunks = [c for _, chunks, _ in pending for c in chunks]
        vectors = self.embedder.embed_documents([c.embed_text for c in all_chunks]) if all_chunks else []
        offset = 0
        for sf, chunks, facts in pending:
            file_vectors = vectors[offset : offset + len(chunks)]
            offset += len(chunks)
            self.vectors.delete_path(sf.path)
            if chunks:
                self.vectors.upsert(chunks, file_vectors)
            self.store.replace_file(sf.path, sf.sha256, sf.category, chunks, facts.to_dict() if facts else None)
        stats.chunks_written += len(all_chunks)
        stats.embed_tokens += sum(estimate_tokens(c.embed_text) for c in all_chunks)
        limit = getattr(self.embedder, "max_tokens", None)
        if limit:
            stats.chunks_over_embed_limit += sum(1 for c in all_chunks if estimate_tokens(c.embed_text) > limit)
