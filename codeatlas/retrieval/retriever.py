"""Hybrid retrieval: BM25 + vector search fused with RRF, optional query rewriting, cross-encoder
reranking and code-graph expansion."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, fields
from typing import Any

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
# Ranking options that can be overridden per run (`codeatlas eval -o name=value`); shown in config names.
OPTIONS = ("candidates", "rerank_pool", "graph_depth", "demote_tests", "outlines_after_methods", "graph_slots")

TEST_INTENT = re.compile(
    r"\b(tests?|testing|tested|test cases?|junit|mock(s|ed|ing|ito)?|asserts?|assertions?)\b", re.IGNORECASE
)
_TEST_FILE = re.compile(r"(^|/)src/test/|(Tests?|IT)\.java$")


def is_test_path(path: str) -> bool:
    return _TEST_FILE.search(path) is not None


@dataclass
class RetrievalConfig:
    mode: str = "hybrid"
    rerank: bool = False
    graph: bool = False
    rewrite: bool = False
    k: int = 8
    candidates: int = 40
    rerank_pool: int = 20
    graph_depth: int = 3
    demote_tests: bool = True  # rank test code below main code unless the query is about tests
    outlines_after_methods: bool = True  # a class outline ranks below its own retrieved methods
    graph_slots: int = 2  # impact/caller queries: top-5 places reserved for graph neighbours (0 = legacy)

    @staticmethod
    def parse_options(pairs: list[str]) -> dict[str, Any]:
        """['demote_tests=false', 'rerank_pool=20'] -> typed overrides."""
        types = {f.name: f.type for f in fields(RetrievalConfig)}
        out: dict[str, Any] = {}
        for pair in pairs:
            name, sep, raw = pair.partition("=")
            name = name.strip()
            if not sep or name not in OPTIONS:
                raise ValueError(f"Expected name=value with name one of {OPTIONS}, got {pair!r}")
            if types[name] in (bool, "bool"):
                if raw.strip().lower() not in ("true", "false", "on", "off", "1", "0"):
                    raise ValueError(f"{name} must be true or false, got {raw!r}")
                out[name] = raw.strip().lower() in ("true", "on", "1")
            else:
                out[name] = int(raw)
        return out

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
        base = "+".join([self.mode] + [f for f in FLAGS if getattr(self, f)])
        changed = [
            f"{f.name}={getattr(self, f.name)}"
            for f in fields(self)
            if f.name in OPTIONS and getattr(self, f.name) != f.default
        ]
        return base + (f"[{','.join(changed)}]" if changed else "")


@dataclass
class RetrievalResult:
    chunks: list[ScoredChunk]
    confidence: float
    confidence_source: str  # vector | rerank | bm25 | none
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

        demote_tests = cfg.demote_tests and not TEST_INTENT.search(query)
        if demote_tests:  # before the rerank pool is cut, so main code fills it
            scored = _tests_last(scored)

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
            if demote_tests:
                scored = _tests_last(scored)

        if cfg.outlines_after_methods:
            scored = _outlines_after_methods(scored)

        results = scored[: cfg.k]
        report = None
        if cfg.graph and self.graph is not None:
            t3 = time.perf_counter()
            # The graph report does not change the confidence: a question that merely names a class is not
            # thereby answerable, so refusal stays governed by the retrieval scores.
            results, report = self._graph_expand(query, results, cfg)
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

    def _reserve_graph_slots(
        self,
        query: str,
        results: list[ScoredChunk],
        endpoint_nodes: list[str],
        targets: list[str],
        hits: list,
        cfg: RetrievalConfig,
    ) -> list[ScoredChunk]:
        """Impact/caller queries: keep the top (5 - graph_slots) retrieved chunks, then give the next
        graph_slots places to graph neighbours: endpoint callers/servers first, then dependents by depth
        (methods before types). The symbols the query names are not neighbours; retrieval finds those."""
        window = min(cfg.k, 5)
        keep = max(0, window - cfg.graph_slots)
        head = results[:keep]
        seen = {sc.chunk.id for sc in head}
        named = set(targets) - set(endpoint_nodes)
        skip_tests = cfg.demote_tests and not TEST_INTENT.search(query)
        dependents = [h.node for h in sorted(hits, key=lambda h: (h.depth, h.kind != "method"))]
        neighbours: list[ScoredChunk] = []
        score = head[-1].score if head else (results[0].score if results else 1.0)
        for node in dict.fromkeys(endpoint_nodes + dependents):
            if len(neighbours) >= cfg.graph_slots:
                break
            chunk = self.first_chunk(node)
            if chunk is None or chunk.id in seen or node in named or (skip_tests and is_test_path(chunk.path)):
                continue
            neighbours.append(ScoredChunk(chunk, score, {"graph": node}))
            seen.add(chunk.id)
        rest = [sc for sc in results[keep:] if sc.chunk.id not in seen]
        return (head + neighbours + rest)[: cfg.k]

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
                if cfg.graph_slots > 0:
                    return self._reserve_graph_slots(query, results, endpoint_nodes, targets, hits, cfg), report
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


def _tests_last(scored: list[ScoredChunk]) -> list[ScoredChunk]:
    """Stable partition: main code in its current order, then test code in its current order."""
    return [sc for sc in scored if not is_test_path(sc.chunk.path)] + [
        sc for sc in scored if is_test_path(sc.chunk.path)
    ]


def _outlines_after_methods(scored: list[ScoredChunk]) -> list[ScoredChunk]:
    """Move each class-outline chunk to just after the lowest-ranked of its own method chunks in the list.

    The outline only lists signatures; when a method of the same class was also retrieved, the method
    holds the actual code. Outlines with none of their methods in the list keep their place.
    """
    out = list(scored)
    for outline in [sc for sc in scored if sc.chunk.kind == "class" and sc.chunk.symbol]:
        prefix = outline.chunk.symbol + "#"
        pos = out.index(outline)
        last = max(
            (i for i, sc in enumerate(out) if sc.chunk.kind == "method" and (sc.chunk.symbol or "").startswith(prefix)),
            default=-1,
        )
        if last > pos:
            out.insert(last, out.pop(pos))  # after the pop, index `last` is just past that method
    return out


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)
