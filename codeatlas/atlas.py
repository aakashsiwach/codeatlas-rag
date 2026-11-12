"""CodeAtlas facade: wires storage, indexing, retrieval, the code graph and answer generation."""

from __future__ import annotations

import logging
import subprocess
import threading
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from codeatlas.answer import Answer, AnswerEngine
from codeatlas.config import Settings
from codeatlas.embeddings import Embedder, build_embedder
from codeatlas.graph import CodeGraph, ImpactHit
from codeatlas.ingest.indexer import Indexer, IndexRunStats
from codeatlas.ingest.java import JavaFileFacts
from codeatlas.llm import LLM, build_llm
from codeatlas.retrieval.reranker import CrossEncoderReranker
from codeatlas.retrieval.retriever import RetrievalConfig, RetrievalResult, Retriever
from codeatlas.retrieval.rewrite import QueryRewriter
from codeatlas.storage.chunk_store import ChunkStore
from codeatlas.storage.vector_store import VectorStore
from codeatlas.tracing import Tracer

log = logging.getLogger(__name__)


class CodeAtlas:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        embedder: Embedder | None = None,
        llm: LLM | None = None,
        reranker: CrossEncoderReranker | None = None,
    ):
        self.settings = settings or Settings()
        self.settings.index_dir.mkdir(parents=True, exist_ok=True)
        self.store = ChunkStore(self.settings.index_dir / "codeatlas.db")
        self.embedder = embedder or build_embedder(self.settings)
        self.vectors = VectorStore.from_settings(self.settings, self.embedder.dim)
        self.tracer = Tracer(self.settings.index_dir / "traces.jsonl" if self.settings.trace_enabled else None)
        self.indexer = Indexer(self.settings, self.store, self.vectors, self.embedder)
        self._llm = llm
        if reranker is None and self.settings.reranker_model:
            reranker = CrossEncoderReranker(self.settings.reranker_model)
        self.retriever = Retriever(
            self.store,
            self.vectors,
            self.embedder,
            reranker=reranker,
            rewriter=QueryRewriter(lambda: self.llm),
        )
        self.engine = AnswerEngine(self.retriever, lambda: self.llm, self.settings, self.tracer)
        self.graph = CodeGraph()
        self._lock = threading.RLock()
        self.reload()

    @property
    def llm(self) -> LLM:
        if self._llm is None:
            self._llm = build_llm(self.settings)
        return self._llm

    @property
    def repo_path(self) -> Path | None:
        stored = self.store.get_meta("repo_path")
        if stored:
            return Path(stored)
        return self.settings.repo_path

    # --- indexing --------------------------------------------------------------------------

    def index(
        self,
        repo: Path | str | None = None,
        *,
        force: bool = False,
        chunker: str | None = None,
        only_paths: Iterable[str] | None = None,
    ) -> IndexRunStats:
        target = Path(repo) if repo else self.repo_path
        if target is None:
            raise ValueError("No repository path given and none configured (CODEATLAS_REPO_PATH)")
        with self._lock:
            stats = self.indexer.run(
                target,
                force=force,
                chunker=chunker or self.store.get_meta("chunker") or "ast",
                only_paths=only_paths,
            )
            self.store.set_meta("repo_path", str(target.resolve()))
            self.reload()
        return stats

    def sync_from_push(self, changed_paths: list[str] | None) -> IndexRunStats:
        """Webhook handler: optionally git-pull, then re-index only what the push touched."""
        repo = self.repo_path
        if repo is None:
            raise ValueError("No repository configured for webhook sync")
        if self.settings.git_pull_on_webhook:
            result = subprocess.run(
                ["git", "-C", str(repo), "pull", "--ff-only"], capture_output=True, text=True, check=False
            )
            if result.returncode != 0:
                log.warning("git pull failed: %s", result.stderr.strip())
        return self.index(repo, only_paths=changed_paths)

    def reload(self) -> None:
        with self._lock:
            self.graph = CodeGraph.build(JavaFileFacts.from_dict(d) for d in self.store.all_facts())
            self.retriever.refresh(self.graph)

    # --- querying --------------------------------------------------------------------------

    def config(self, mode: str | None = None, k: int | None = None) -> RetrievalConfig:
        return RetrievalConfig.parse(mode or self.settings.default_mode, k=k or self.settings.default_k)

    def retrieve(self, question: str, mode: str | None = None, k: int | None = None) -> RetrievalResult:
        return self.retriever.retrieve(question, self.config(mode, k))

    def ask(self, question: str, mode: str | None = None, k: int | None = None) -> Answer:
        return self.engine.ask(question, self.config(mode, k))

    def ask_stream(self, question: str, mode: str | None = None, k: int | None = None) -> Iterator[dict[str, Any]]:
        return self.engine.ask_stream(question, self.config(mode, k))

    def impact(self, symbol: str, depth: int = 3) -> tuple[list[str], list[ImpactHit], str]:
        targets = [symbol] if symbol in self.graph.g else self.graph.resolve_mentions(symbol)
        hits = self.graph.impact(targets, depth=depth)
        return targets, hits, self.graph.describe(targets, hits)

    def stats(self) -> dict[str, Any]:
        return {
            "repo_path": str(self.repo_path) if self.repo_path else None,
            "files": len(self.store.file_hashes()),
            "chunks": self.store.count_chunks(),
            "vectors": self.vectors.count(),
            "embedder": self.store.get_meta("embedder"),
            "chunker": self.store.get_meta("chunker"),
            "graph": self.graph.stats(),
            "recent_runs": self.store.runs(5),
        }
