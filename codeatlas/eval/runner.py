"""Run an evaluation set against one or more retrieval configurations and compare them."""

from __future__ import annotations

import statistics
import subprocess
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from codeatlas import __version__
from codeatlas.eval.dataset import EvalItem, resolve_expected_sources
from codeatlas.eval.judge import judge_answer
from codeatlas.eval.metrics import citation_precision, hit_at_k, percentile, recall_at_k, reciprocal_rank
from codeatlas.llm import LLM, thinking_for
from codeatlas.retrieval.retriever import RetrievalConfig

if TYPE_CHECKING:
    from codeatlas.atlas import CodeAtlas

_MS_COLUMNS = ("p50_ms", "p95_ms")


@dataclass
class ItemResult:
    id: str
    question: str
    category: str
    answerable: bool
    latency_ms: float
    retrieved: list[str]
    expected: list[str] = field(default_factory=list)
    recall: float | None = None
    recall_deep: float | None = None  # recall at EvalReport.deep_k
    hit: float | None = None
    reciprocal_rank: float | None = None
    answer: str | None = None
    refused: bool | None = None
    answer_truncated: bool | None = None  # the answer hit the output limit
    citation_precision: float | None = None
    faithfulness: float | None = None
    correct: bool | None = None
    judge_error: str | None = None  # "judge_truncated" or "judge_unparseable": the verdict was not scored


def _mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(statistics.fmean(present), 4) if present else None


@dataclass
class EvalReport:
    config: str
    k: int
    items: list[ItemResult] = field(default_factory=list)
    deep_k: int | None = None  # a second, larger cutoff for recall (e.g. recall@10)

    def _recall_columns(self, rows: list[ItemResult]) -> dict[str, Any]:
        out: dict[str, Any] = {f"recall@{self.k}": _mean([r.recall for r in rows])}
        if self.deep_k:
            out[f"recall@{self.deep_k}"] = _mean([r.recall_deep for r in rows])
        return out

    def summary(self) -> dict[str, Any]:
        answerable = [r for r in self.items if r.answerable]
        latencies = [r.latency_ms for r in self.items]
        out: dict[str, Any] = {
            "config": self.config,
            "k": self.k,
            "questions": len(self.items),
            **self._recall_columns(answerable),
            f"hit@{self.k}": _mean([r.hit for r in answerable]),
            "mrr": _mean([r.reciprocal_rank for r in answerable]),
            "p50_ms": round(percentile(latencies, 0.5), 1),
            "p95_ms": round(percentile(latencies, 0.95), 1),
        }
        if any(r.refused is not None for r in self.items):
            out["refusal_accuracy"] = _mean(
                [float(r.refused != r.answerable) for r in self.items if r.refused is not None]
            )
            out["citation_precision"] = _mean([r.citation_precision for r in answerable])
            out["faithfulness"] = _mean([r.faithfulness for r in answerable])
            correct = [float(r.correct) for r in answerable if r.correct is not None]
            out["answer_accuracy"] = round(statistics.fmean(correct), 4) if correct else None
            out["answers_truncated"] = sum(1 for r in self.items if r.answer_truncated)
            if any(r.faithfulness is not None or r.judge_error for r in answerable):
                out["judge_truncated"] = sum(1 for r in answerable if r.judge_error == "judge_truncated")
                out["judge_unparseable"] = sum(1 for r in answerable if r.judge_error == "judge_unparseable")
        return out

    def by_category(self) -> dict[str, dict[str, Any]]:
        """Recall, hit rate and MRR per question category (answerable questions only)."""
        out: dict[str, dict[str, Any]] = {}
        for category in sorted({r.category for r in self.items if r.answerable}):
            rows = [r for r in self.items if r.answerable and r.category == category]
            out[category] = {
                "questions": len(rows),
                **self._recall_columns(rows),
                f"hit@{self.k}": _mean([r.hit for r in rows]),
                "mrr": _mean([r.reciprocal_rank for r in rows]),
            }
        return out

    def weakest(self, n: int = 5) -> list[ItemResult]:
        """The answerable questions with the lowest recall, then the lowest reciprocal rank."""
        scored = [r for r in self.items if r.answerable and r.recall is not None]
        return sorted(scored, key=lambda r: (r.recall, r.reciprocal_rank or 0.0, r.id))[:n]

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "by_category": self.by_category(),
            "items": [asdict(r) for r in self.items],
        }


def evaluate(
    atlas: CodeAtlas,
    items: list[EvalItem],
    cfg: RetrievalConfig,
    *,
    with_answers: bool = False,
    judge: LLM | None = None,
    deep_k: int | None = None,
) -> EvalReport:
    """Score one retrieval configuration. With deep_k, also report recall at that larger cutoff.

    The deep cutoff never changes the top-k numbers: graph expansion depends on k, so graph configs
    are retrieved a second time at deep_k; other configs rank identically and are retrieved once.
    """
    with_answers = with_answers or judge is not None  # judging needs answers
    deep_k = deep_k if deep_k and deep_k > cfg.k else None
    deep_cfg = replace(cfg, k=deep_k) if deep_k else None
    report = EvalReport(config=cfg.name, k=cfg.k, deep_k=deep_k)
    if any(e.symbol and e.definitions is None for item in items for e in item.expected_sources):
        if atlas.repo_path is None:
            raise ValueError("Symbol-level expected sources need the indexed repository; index a repo first")
        items = resolve_expected_sources(items, atlas.repo_path)
    for item in items:
        start = time.perf_counter()
        answer = None
        if with_answers:
            answer = atlas.engine.ask(item.question, cfg)
            chunks = [c for c in answer.context if c.kind != "graph"]
        else:
            run_cfg = deep_cfg if deep_cfg and not cfg.graph else cfg
            chunks = [sc.chunk for sc in atlas.retriever.retrieve(item.question, run_cfg).chunks]
        latency = (time.perf_counter() - start) * 1000
        deep_chunks = None
        if deep_cfg:
            if not with_answers and not cfg.graph:
                deep_chunks = chunks
            else:
                deep_chunks = [sc.chunk for sc in atlas.retriever.retrieve(item.question, deep_cfg).chunks]

        result = ItemResult(
            id=item.id,
            question=item.question,
            category=item.category,
            answerable=item.answerable,
            latency_ms=round(latency, 1),
            retrieved=[f"{c.location} {c.symbol or ''}".strip() for c in chunks[: cfg.k]],
            expected=[e.path + (f"::{e.symbol}" if e.symbol else "") for e in item.expected_sources],
        )
        if item.answerable and item.expected_sources:
            result.recall = recall_at_k(item.expected_sources, chunks, cfg.k)
            if deep_chunks is not None and deep_k:
                result.recall_deep = recall_at_k(item.expected_sources, deep_chunks, deep_k)
            result.hit = hit_at_k(item.expected_sources, chunks, cfg.k)
            result.reciprocal_rank = reciprocal_rank(item.expected_sources, chunks[: cfg.k])
        if answer is not None:
            result.answer = answer.answer
            result.refused = answer.refused
            result.answer_truncated = answer.truncated
            if item.answerable and not answer.refused:
                result.citation_precision = citation_precision(
                    item.expected_sources, [c.chunk for c in answer.citations]
                )
                if judge is not None:
                    verdict = judge_answer(
                        judge,
                        item.question,
                        answer.answer,
                        answer.context,
                        item.reference_answer,
                        thinking=thinking_for(atlas.settings, "judge"),
                    )
                    result.faithfulness = verdict["faithfulness"]
                    result.correct = verdict["correct"]
                    result.judge_error = verdict["judge_error"]
        report.items.append(result)
    return report


# --- run metadata ---------------------------------------------------------------------------


def git_commit(path: Path | str | None) -> str | None:
    """Short commit hash of the git checkout at path, suffixed with -dirty if it has local changes."""
    if not path:
        return None
    try:
        head = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
        )
        if head.returncode != 0:
            return None
        status = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"], capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    return head.stdout.strip() + ("-dirty" if status.stdout.strip() else "")


def run_metadata(
    atlas: CodeAtlas,
    eval_set: Path | str,
    items: list[EvalItem],
    *,
    with_answers: bool = False,
    judge: bool = False,
) -> dict[str, Any]:
    """Everything needed to reproduce or interpret an eval run."""
    settings = atlas.settings
    runs = [r for r in atlas.store.runs(50) if r.get("mode") in ("full", "incremental")]
    initial = next((r for r in runs if r.get("files_changed") == r.get("files_scanned")), runs[-1] if runs else None)
    meta: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "codeatlas_version": __version__,
        "codeatlas_commit": git_commit(Path(__file__).resolve().parents[2]),
        "repo_path": str(atlas.repo_path) if atlas.repo_path else None,
        "repo_commit": git_commit(atlas.repo_path),
        "eval_set": str(eval_set),
        "questions": len(items),
        "answerable": sum(1 for i in items if i.answerable),
        "chunker": atlas.store.get_meta("chunker"),
        "embedder": atlas.store.get_meta("embedder"),
        "reranker": settings.reranker_model or None,
        "chunks": atlas.store.count_chunks(),
        "graph": atlas.graph.stats(),
    }
    if initial:
        meta["index_build"] = {
            "files": initial.get("files_scanned"),
            "chunks": initial.get("chunks_written"),
            "embed_tokens": initial.get("embed_tokens"),
            "embed_cost_usd": initial.get("embed_cost_usd"),
            "duration_s": initial.get("duration_s"),
        }
    if with_answers or judge:
        meta["llm"] = f"{settings.llm_provider}:{settings.llm_model}"
        if judge:
            meta["judge_thinking"] = thinking_for(settings, "judge") or "model default"
            meta["answer_thinking"] = thinking_for(settings, "answer") or "model default"
        meta["min_vector_similarity"] = settings.min_vector_similarity
        meta["min_rerank_confidence"] = settings.min_rerank_confidence
    return meta


# --- reporting ------------------------------------------------------------------------------


def _cell(value: Any, column: str = "") -> str:
    if value is None:
        return "-"
    if isinstance(value, float) and column not in _MS_COLUMNS:
        return f"{value:.3f}"
    return str(value)


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + [
        "| " + " | ".join(row) + " |" for row in rows
    ]


def to_markdown(reports: list[EvalReport]) -> str:
    """Overall comparison table, one row per configuration."""
    if not reports:
        return ""
    summaries = [r.summary() for r in reports]
    columns: list[str] = []
    for s in summaries:
        columns += [c for c in s if c not in columns and c != "questions"]
    return "\n".join(_table(columns, [[_cell(s.get(c), c) for c in columns] for s in summaries]))


def category_markdown(reports: list[EvalReport]) -> str:
    """Recall@k per category (rows) and configuration (columns): shows where each technique helps."""
    if not reports:
        return ""
    key = f"recall@{reports[0].k}"
    per_report = [r.by_category() for r in reports]
    categories = sorted({c for cats in per_report for c in cats})
    rows = []
    for category in categories:
        n = next((cats[category]["questions"] for cats in per_report if category in cats), 0)
        rows.append([f"{category} ({n})"] + [_cell(cats.get(category, {}).get(key)) for cats in per_report])
    return "\n".join(_table(["category (questions)"] + [r.config for r in reports], rows))


def weakest_markdown(reports: list[EvalReport], n: int = 5) -> str:
    lines: list[str] = []
    for report in reports:
        lines.append(f"### {report.config}")
        lines.append("")
        rows = [
            [
                r.id,
                r.category,
                _cell(r.recall),
                "<br>".join(r.expected) or "-",
                "<br>".join(r.retrieved[:3]) or "-",
            ]
            for r in report.weakest(n)
        ]
        lines += _table(["question", "category", "recall", "expected", "top 3 retrieved"], rows)
        lines.append("")
    return "\n".join(lines).rstrip()


def report_markdown(reports: list[EvalReport], metadata: dict[str, Any], weakest: int = 5) -> str:
    """Full Markdown report: run metadata, overall table, per-category table and weakest questions."""
    meta_lines = []
    for key, value in metadata.items():
        if isinstance(value, dict):
            value = ", ".join(f"{k}={v}" for k, v in value.items())
        meta_lines.append(f"- **{key}**: {value}")
    k = reports[0].k if reports else "k"
    sections = [
        "# CodeAtlas evaluation",
        "## Run",
        "\n".join(meta_lines),
        "## Overall",
        to_markdown(reports),
        f"## Recall@{k} by category",
        category_markdown(reports),
        f"## Weakest {weakest} questions per configuration",
        weakest_markdown(reports, weakest),
    ]
    return "\n\n".join(sections) + "\n"
