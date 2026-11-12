"""Hybrid retrieval: BM25 + vector search fused with RRF, optional query rewriting, cross-encoder
reranking and code-graph expansion."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from codeatlas.embeddings import Embedder
from codeatlas.endpoints import QUERY_PATH, endpoint_path, paths_match
from codeatlas.graph import IMPACT_INTENT, CodeGraph
from codeatlas.models import Chunk, ScoredChunk
from codeatlas.retrieval.bm25 import BM25Index
from codeatlas.retrieval.reranker import CrossEncoderReranker, sigmoid
from codeatlas.retrieval.rewrite import QueryRewriter
from codeatlas.storage.chunk_store import ChunkStore
from codeatlas.storage.vector_store import VectorStore

RRF_K = 60
MODES = ("vector", "bm25", "hybrid")
FLAGS = ("rewrite", "rerank", "graph")


@dataclass
class RetrievalConfig:
    mode: str = "hybrid"
    rerank: bool = False
    graph: bool = False
    rewrite: bool = False
    k: int = 8
    candidates: int = 40
    rerank_pool: int = 30
    graph_depth: int = 3

    @classmethod
    def parse(cls, spec: str, **overrides) -> RetrievalConfig:
        """Parse specs like 'hybrid', 'vector', 'hybrid+rerank+graph'."""
        parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
        mode = "hybrid"
        if parts and parts[0] in MODES:
            mode, parts = parts[0], parts[1:]
        unknown = set(parts) - set(FLAGS)
        if unknown:
            raise ValueError(f"Unknown retrieval option(s) {sorted(unknown)} in {spec!r}")
        overrides = {k: v for k, v in overrides.items() if v is not None}
        return cls(
            mode=mode, rerank="rerank" in parts, graph="graph" in parts, rewrite="rewrite" in parts, **overrides
        )

    @property
    def name(self) -> str:
        return "+".join([self.mode] + [f for f in FLAGS if getattr(self, f)])


@dataclass
class RetrievalResult:
    chunks: list[ScoredChunk]
    confidence: float
    confidence_source: str  # vector | rerank | bm25 | graph | none
    queries: list[str]
    graph_report: str | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)


class Retriever:
    def __init__(
        self,
        store: ChunkStore,
        vectors: VectorStore,
        embedder: Embedder,
        *,
        reranker: CrossEncoderReranker | None = None,
        rewriter: QueryRewriter | None = None,
    ):
        self.store = store
        self.vectors = vectors
        self.embedder = embedder
        self.reranker = reranker
        self.rewriter = rewriter
        self.graph: CodeGraph | None = None
        self._chunks: dict[str, Chunk] = {}
        self._by_symbol: dict[str, list[Chunk]] = {}
        self._bm25 = BM25Index([], [])

    def refresh(self, graph: CodeGraph | None = None) -> None:
        """Reload chunks from the store and rebuild the BM25 index."""
        chunks = sorted(self.store.all_chunks(), key=lambda c: (c.path, c.start_line))
        by_symbol: dict[str, list[Chunk]] = {}
        for c in chunks:
            if c.symbol:
                by_symbol.setdefault(c.symbol, []).append(c)
        bm25 = BM25Index([c.id for c in chunks], [c.embed_text for c in chunks])
        self._chunks, self._by_symbol, self._bm25, self.graph = {c.id: c for c in chunks}, by_symbol, bm25, graph

    def chunk(self, chunk_id: str) -> Chunk | None:
        return self._chunks.get(chunk_id)

    def first_chunk(self, symbol: str) -> Chunk | None:
        chunks = self._by_symbol.get(symbol)
        return chunks[0] if chunks else None

    # --- main entry point ------------------------------------------------------------------

    def retrieve(self, query: str, cfg: RetrievalConfig) -> RetrievalResult:
        timings: dict[str, float] = {}
        t0 = time.perf_counter()

        queries = [query]
        if cfg.rewrite:
            if self.rewriter is None:
                raise RuntimeError("Query rewriting requested but no rewriter is configured")
            queries += self.rewriter.rewrite(query)
            timings["rewrite"] = _ms(t0)

        t1 = time.perf_counter()
        fused: dict[str, float] = {}
        signals: dict[str, dict] = {}
        top_similarity: float | None = None
        for qi, q in enumerate(queries):
            ranked_lists: list[tuple[str, list[tuple[str, float]]]] = []
            if cfg.mode in ("vector", "hybrid"):
                hits = self.vectors.search(self.embedder.embed_query(q), cfg.candidates)
                if qi == 0 and hits:
                    top_similarity = hits[0][1]
                ranked_lists.append(("vector", hits))
            if cfg.mode in ("bm25", "hybrid"):
                ranked_lists.append(("bm25", self._bm25.search(q, cfg.candidates)))
            for source, hits in ranked_lists:
                for rank, (cid, score) in enumerate(hits):
                    fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
                    signals.setdefault(cid, {}).setdefault(source, round(score, 4))

        if cfg.graph:  # structural boost: chunks that serve/call an HTTP path named in the query
            for cid in self._endpoint_chunk_ids(QUERY_PATH.findall(query)):
                fused[cid] = fused.get(cid, 0.0) + 2.0 / (RRF_K + 1)
                signals.setdefault(cid, {})["endpoint"] = True
        timings["search"] = _ms(t1)

        ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[: cfg.candidates]
        scored = [ScoredChunk(self._chunks[cid], s, signals[cid]) for cid, s in ranked if cid in self._chunks]

        if top_similarity is not None:
            confidence, source = top_similarity, "vector"
        else:
            confidence, source = (1.0, "bm25") if scored else (0.0, "none")

        if cfg.rerank:
            if self.reranker is None:
                raise RuntimeError("Reranking requested but no reranker model is configured")
            t2 = time.perf_counter()
            pool = scored[: cfg.rerank_pool]
            for sc, s in zip(pool, self.reranker.score(query, [sc.chunk for sc in pool])):
                sc.score = s
                sc.signals["rerank"] = round(s, 4)
            scored = sorted(pool, key=lambda sc: sc.score, reverse=True)
            confidence, source = (sigmoid(scored[0].score), "rerank") if scored else (0.0, "none")
            timings["rerank"] = _ms(t2)

        results = scored[: cfg.k]
        report = None
        if cfg.graph and self.graph is not None:
            t3 = time.perf_counter()
            results, report = self._graph_expand(query, results, cfg)
            if report:
                confidence, source = 1.0, "graph"
            timings["graph"] = _ms(t3)

        timings["total"] = _ms(t0)
        return RetrievalResult(results, confidence, source, queries, report, timings)

    # --- graph and endpoint helpers --------------------------------------------------------

    def _endpoint_chunk_ids(self, paths: list[str]) -> list[str]:
        if not paths:
            return []
        out: list[str] = []
        for cid, c in self._chunks.items():
            md = c.metadata
            served = [endpoint_path(e) for e in md.get("endpoints", []) + md.get("client_endpoints", [])]
            literals = md.get("path_literals", [])
            if any(paths_match(p, e) for p in paths for e in served) or any(
                paths_match(p, lit, prefix=lit.endswith("/")) for p in paths for lit in literals
            ):
                out.append(cid)
        return out

    def _graph_expand(
        self, query: str, results: list[ScoredChunk], cfg: RetrievalConfig
    ) -> tuple[list[ScoredChunk], str | None]:
        graph = self.graph
        assert graph is not None
        if IMPACT_INTENT.search(query):
            paths = QUERY_PATH.findall(query)
            roles = graph.endpoint_roles(paths) if paths else {}
            endpoint_nodes = [n for nodes in roles.values() for n in nodes]
            targets = list(dict.fromkeys(endpoint_nodes + graph.resolve_mentions(query)))
            if targets:
                hits = graph.impact(targets, depth=cfg.graph_depth)
                report = graph.describe(targets, hits, paths, roles)
                ordered = targets + [h.node for h in sorted(hits, key=lambda h: h.depth)]
                top_score = results[0].score if results else 1.0
                merged: list[ScoredChunk] = []
                seen: set[str] = set()
                for node in ordered:
                    chunk = self.first_chunk(node)
                    if chunk and chunk.id not in seen and len(merged) < max(cfg.k - 2, 1):
                        merged.append(ScoredChunk(chunk, top_score, {"graph": node}))
                        seen.add(chunk.id)
                for sc in results:
                    if sc.chunk.id not in seen:
                        merged.append(sc)
                        seen.add(sc.chunk.id)
                return merged[: cfg.k], report

        # Otherwise pull in direct callers/callees of the top method hits.
        out: list[ScoredChunk] = []
        seen = {sc.chunk.id for sc in results}
        budget = max(1, cfg.k // 3)
        for i, sc in enumerate(results):
            out.append(sc)
            if i >= 3 or sc.chunk.kind != "method" or not sc.chunk.symbol or budget <= 0:
                continue
            for neighbor in graph.neighbors(sc.chunk.symbol, limit=2):
                chunk = self.first_chunk(neighbor)
                if chunk and chunk.id not in seen and budget > 0:
                    out.append(ScoredChunk(chunk, sc.score, {"graph": sc.chunk.symbol}))
                    seen.add(chunk.id)
                    budget -= 1
        return out[: cfg.k], None


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)
