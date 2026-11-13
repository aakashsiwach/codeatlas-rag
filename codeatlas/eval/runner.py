"""Run an evaluation set against one or more retrieval configurations and compare them."""

from __future__ import annotations

import hashlib
import os
import platform
import re
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
from codeatlas.llm import LLM, LLMError, llm_cost_usd, thinking_for
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
    confidence: float | None = None  # retrieval confidence that the refusal gate compares
    confidence_source: str | None = None
    threshold: float | None = None  # refusal threshold that applied to it
    threshold_key: str | None = None  # the model whose score it is (embedder or reranker)
    answer: str | None = None
    refused: bool | None = None
    answer_truncated: bool | None = None  # the answer hit the output limit
    citation_precision: float | None = None
    faithfulness: float | None = None
    correct: bool | None = None
    judge_error: str | None = None  # "judge_truncated" or "judge_unparseable": the verdict was not scored
    error: str | None = None  # an LLM call failed for this question ("kind: ErrorClass: message")
    # Tokens and cost of LLM calls for this question (answer + judge); cost None = unpriced.
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_cost_usd: float | None = None


def _mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(statistics.fmean(present), 4) if present else None


@dataclass
class EvalReport:
    config: str
    k: int
    items: list[ItemResult] = field(default_factory=list)
    deep_k: int | None = None  # a second, larger cutoff for recall (e.g. recall@10)
    warmup_ms: float | None = None  # untimed first retrieval (model loading), excluded from latency

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
        if any(r.error for r in self.items):
            out["errors"] = sum(1 for r in self.items if r.error)
        out.update(self.cost())
        return out

    def cost(self) -> dict[str, Any]:
        """LLM tokens and cost for this configuration: answers plus judge. Empty when no LLM ran."""
        tokens_in = sum(r.llm_input_tokens for r in self.items)
        tokens_out = sum(r.llm_output_tokens for r in self.items)
        if not tokens_in and not tokens_out:
            return {}
        costs = [r.llm_cost_usd for r in self.items if r.llm_input_tokens or r.llm_output_tokens]
        priced = all(c is not None for c in costs)
        return {
            "llm_tokens_in": tokens_in,
            "llm_tokens_out": tokens_out,
            "llm_cost_usd": round(sum(costs), 4) if priced else "unpriced",
        }

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
            "warmup_ms": self.warmup_ms,
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
    # Embedder and reranker load lazily; without a warm-up the first question's latency would include
    # loading the model (about 20 s for a base-size reranker on CPU) and inflate p95.
    start = time.perf_counter()
    atlas.retriever.retrieve("warm-up query: where is the main entry point?", cfg)
    report.warmup_ms = round((time.perf_counter() - start) * 1000, 1)
    if any(e.symbol and e.definitions is None for item in items for e in item.expected_sources):
        if atlas.repo_path is None:
            raise ValueError("Symbol-level expected sources need the indexed repository; index a repo first")
        items = resolve_expected_sources(items, atlas.repo_path)
    for item in items:
        start = time.perf_counter()
        answer = None
        if with_answers:
            try:
                answer = atlas.engine.ask(item.question, cfg)
            except LLMError as exc:  # record it and move on: one throttle must not lose the whole run
                report.items.append(_failed_item(item, exc))
                continue
            chunks = [c for c in answer.context if c.kind != "graph"]
            confidence, confidence_source = answer.confidence, answer.confidence_source
        else:
            run_cfg = deep_cfg if deep_cfg and not cfg.graph else cfg
            rr = atlas.retriever.retrieve(item.question, run_cfg)
            chunks = [sc.chunk for sc in rr.chunks]
            confidence, confidence_source = rr.confidence, rr.confidence_source
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
            confidence=round(confidence, 4),
            confidence_source=confidence_source,
            threshold=atlas.engine.threshold_for(confidence_source),
            threshold_key=_threshold_key(atlas, confidence_source),
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
            _add_usage(result, atlas, answer.usage.get("input_tokens", 0), answer.usage.get("output_tokens", 0))
            if item.answerable and not answer.refused:
                result.citation_precision = citation_precision(
                    item.expected_sources, [c.chunk for c in answer.citations]
                )
                if judge is not None:
                    try:
                        verdict = judge_answer(
                            judge,
                            item.question,
                            answer.answer,
                            answer.context,
                            item.reference_answer if item.reference_reviewed else None,
                            thinking=thinking_for(atlas.settings, "judge"),
                        )
                    except LLMError as exc:
                        result.error = f"judge {exc.kind}: {exc.error_class}: {exc.message}"
                    else:
                        usage = verdict["judge_usage"]
                        _add_usage(result, atlas, usage.input_tokens, usage.output_tokens)
                        result.faithfulness = verdict["faithfulness"]
                        result.correct = verdict["correct"]
                        result.judge_error = verdict["judge_error"]
        report.items.append(result)
    return report


def _threshold_key(atlas: CodeAtlas, source: str) -> str | None:
    return {"rerank": atlas.settings.reranker_model, "vector": atlas.embedder.name}.get(source)


def _add_usage(result: ItemResult, atlas: CodeAtlas, input_tokens: int, output_tokens: int) -> None:
    """Accumulate one LLM call's tokens and cost on the item; cost stays None when unpriced."""
    if not input_tokens and not output_tokens:
        return
    priced_before = result.llm_cost_usd is not None or not (result.llm_input_tokens or result.llm_output_tokens)
    result.llm_input_tokens += input_tokens
    result.llm_output_tokens += output_tokens
    cost = llm_cost_usd(atlas.settings, input_tokens, output_tokens)
    result.llm_cost_usd = (result.llm_cost_usd or 0.0) + cost if cost is not None and priced_before else None


def _failed_item(item: EvalItem, exc: LLMError) -> ItemResult:
    """A question whose answer call failed: no metrics, the error is kept for the report."""
    return ItemResult(
        id=item.id,
        question=item.question,
        category=item.category,
        answerable=item.answerable,
        latency_ms=0.0,
        retrieved=[],
        expected=[e.path + (f"::{e.symbol}" if e.symbol else "") for e in item.expected_sources],
        error=f"{exc.kind}: {exc.error_class}: {exc.message}",
    )


# --- run metadata ---------------------------------------------------------------------------


def git_remote(path: Path | str | None) -> str | None:
    """The checkout's origin URL with any credentials removed (https://user:token@host/... -> https://host/...)."""
    if not path:
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "remote", "get-url", "origin"], capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    url = out.stdout.strip()
    return re.sub(r"^(\w+://)[^/@]*@", r"\1", url) if out.returncode == 0 and url else None


def display_path(path: Path | str | None, base: Path | str | None = None) -> str | None:
    """A path relative to the working directory (or base), with forward slashes; never an absolute path,
    which would put the local folder layout into committed reports. Outside it: the last component."""
    if path is None:
        return None
    p = Path(path).resolve()
    root = Path(base or os.getcwd()).resolve()
    return p.relative_to(root).as_posix() if p.is_relative_to(root) else p.name


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


# Libraries whose versions can change retrieval or scoring results.
RESULT_LIBRARIES = ("torch", "sentence-transformers", "transformers", "qdrant-client", "tree-sitter",
                    "tree-sitter-java", "rank-bm25", "networkx")


def library_versions() -> dict[str, str | None]:
    from importlib.metadata import PackageNotFoundError, version

    found: dict[str, str | None] = {"python": platform.python_version()}
    for name in RESULT_LIBRARIES:
        try:
            found[name] = version(name)
        except PackageNotFoundError:
            found[name] = None
    return found


def content_sha256(path: Path | str) -> str:
    """SHA-256 of a text file with line endings normalised, so a CRLF checkout hashes like an LF one."""
    data = Path(path).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


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
        "repo_path": display_path(atlas.repo_path),
        "repo_remote": git_remote(atlas.repo_path),
        "repo_commit": git_commit(atlas.repo_path),
        "eval_set": display_path(eval_set),
        "eval_set_sha256": content_sha256(eval_set),
        "questions": len(items),
        "answerable": sum(1 for i in items if i.answerable),
        # The judge uses a reference answer only when it is marked reviewed.
        "reference_answers_reviewed": (
            f"{sum(1 for i in items if i.reference_answer and i.reference_reviewed)}"
            f"/{sum(1 for i in items if i.reference_answer)}"
        ),
        "chunker": atlas.store.get_meta("chunker"),
        "embedder": atlas.store.get_meta("embedder"),
        "reranker": settings.reranker_model or None,
        "chunks": atlas.store.count_chunks(),
        "graph": atlas.graph.stats(),
        # Refusal thresholds in force (per model, after CODEATLAS_MIN_CONFIDENCE_BY_MODEL overrides).
        "thresholds": {
            f"vector ({atlas.embedder.name})": atlas.engine.threshold_for("vector"),
            f"rerank ({settings.reranker_model or 'none'})": atlas.engine.threshold_for("rerank"),
        },
    }
    meta["versions"] = library_versions()
    if initial:
        meta["index_build"] = {
            "files": initial.get("files_scanned"),
            "chunks": initial.get("chunks_written"),
            "embed_tokens": initial.get("embed_tokens"),
            "embed_cost_usd": initial.get("embed_cost_usd"),
            "duration_s": initial.get("duration_s"),
        }
    if with_answers or judge:
        meta["llm"] = atlas.llm.name  # the provider:model that actually ran
        if judge:
            meta["judge_thinking"] = thinking_for(settings, "judge") or "model default"
            meta["answer_thinking"] = thinking_for(settings, "answer") or "model default"
    return meta


# --- reporting ------------------------------------------------------------------------------


def _cell(value: Any, column: str = "") -> str:
    if value is None:
        return "-"
    if isinstance(value, float) and column == "llm_cost_usd":
        return f"${value:.4f}"
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


def report_markdown(
    reports: list[EvalReport],
    metadata: dict[str, Any],
    weakest: int = 5,
    calibrations: list[dict[str, Any]] | None = None,
) -> str:
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
    cost = total_cost(reports)
    if cost:
        sections[4:4] = ["## LLM cost (answers + judge)", cost_markdown(reports, cost)]
    if calibrations:
        sections[4:4] = [
            "## Refusal calibration",
            "Refuse when confidence < threshold. Best = highest balanced accuracy (answerable and "
            "unanswerable weighted equally), then fewest false refusals. Calibrate on dev only.",
            calibration_markdown(calibrations),
        ]
    return "\n\n".join(sections) + "\n"


def total_cost(reports: list[EvalReport]) -> dict[str, Any]:
    """Tokens and USD over all configurations of a run; cost is "unpriced" if any call was unpriced."""
    parts = [r.cost() for r in reports]
    parts = [p for p in parts if p]
    if not parts:
        return {}
    costs = [p["llm_cost_usd"] for p in parts]
    return {
        "llm_tokens_in": sum(p["llm_tokens_in"] for p in parts),
        "llm_tokens_out": sum(p["llm_tokens_out"] for p in parts),
        "llm_cost_usd": round(sum(costs), 4) if all(isinstance(c, float) for c in costs) else "unpriced",
    }


def cost_markdown(reports: list[EvalReport], total: dict[str, Any]) -> str:
    rows = []
    for r in reports:
        c = r.cost()
        if c:
            rows.append([r.config, str(c["llm_tokens_in"]), str(c["llm_tokens_out"]), _usd(c["llm_cost_usd"])])
    rows.append(["**total**", str(total["llm_tokens_in"]), str(total["llm_tokens_out"]), _usd(total["llm_cost_usd"])])
    return "\n".join(_table(["config", "input tokens", "output tokens", "cost"], rows))


def _usd(value: Any) -> str:
    return f"${value:.4f}" if isinstance(value, float) else str(value)


# --- refusal calibration --------------------------------------------------------------------


def _refusal_scores(rows: list[tuple[float, bool]], threshold: float) -> dict[str, Any]:
    """Refuse when confidence < threshold. Balanced accuracy weighs both classes equally, because answerable
    questions outnumber unanswerable ones and plain accuracy would favour never refusing."""
    answerable = [c for c, a in rows if a]
    unanswerable = [c for c, a in rows if not a]
    false_refusals = sum(1 for c in answerable if c < threshold)
    caught = sum(1 for c in unanswerable if c < threshold)
    return {
        "balanced_accuracy": round(0.5 * (caught / len(unanswerable) + 1 - false_refusals / len(answerable)), 4),
        "accuracy": round((caught + len(answerable) - false_refusals) / len(rows), 4),
        "false_refusals": f"{false_refusals}/{len(answerable)}",
        "unanswerable_refused": f"{caught}/{len(unanswerable)}",
    }


def calibrate(report: EvalReport) -> dict[str, Any] | None:
    """The refusal threshold that best separates answerable from unanswerable questions for one config.

    Candidates are midpoints between consecutive observed confidences (plus both ends); ties prefer fewer
    false refusals, then the lower threshold. Run this on a dev split only."""
    scored = [r for r in report.items if r.confidence is not None]
    rows = [(r.confidence, r.answerable) for r in scored]
    if not any(a for _, a in rows) or all(a for _, a in rows):
        return None
    sources = sorted({r.confidence_source for r in scored})
    values = sorted({c for c, _ in rows})
    candidates = [values[0] - 1e-4] + [(a + b) / 2 for a, b in zip(values, values[1:])] + [values[-1] + 1e-4]

    def key(t: float) -> tuple:
        s = _refusal_scores(rows, t)
        return (s["balanced_accuracy"], -int(s["false_refusals"].split("/")[0]), -t)

    best = max(candidates, key=key)
    current = next((r.threshold for r in scored if r.threshold is not None), None)
    answerable = [c for c, a in rows if a]
    unanswerable = [c for c, a in rows if not a]
    return {
        "config": report.config,
        "source": ", ".join(sources),
        "model": next((r.threshold_key for r in scored if r.threshold_key), None),
        "answerable_confidence": [round(min(answerable), 4), round(statistics.median(answerable), 4), round(max(answerable), 4)],
        "unanswerable_confidence": [round(min(unanswerable), 4), round(statistics.median(unanswerable), 4), round(max(unanswerable), 4)],
        "current_threshold": current,
        "current": _refusal_scores(rows, current) if current is not None else None,
        "best_threshold": round(best, 4),
        "best": _refusal_scores(rows, best),
    }


def calibration_markdown(calibrations: list[dict[str, Any]]) -> str:
    header = ["config", "source (model)", "answerable min/med/max", "unanswerable min/med/max", "current threshold",
              "current bal. acc", "best threshold", "best bal. acc", "false refusals", "unanswerable refused"]
    rows = []
    for c in calibrations:
        cur = c["current"] or {}
        rows.append([
            c["config"],
            f"{c['source']} ({c['model']})",
            "/".join(f"{v:.3f}" for v in c["answerable_confidence"]),
            "/".join(f"{v:.3f}" for v in c["unanswerable_confidence"]),
            _cell(c["current_threshold"]),
            _cell(cur.get("balanced_accuracy")),
            f"{c['best_threshold']:.4f}",
            f"{c['best']['balanced_accuracy']:.3f}",
            c["best"]["false_refusals"],
            c["best"]["unanswerable_refused"],
        ])
    return "\n".join(_table(header, rows))
