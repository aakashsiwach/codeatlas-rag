"""Run an evaluation set against one or more retrieval configurations and compare them."""

from __future__ import annotations

import statistics
import time
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from codeatlas.eval.dataset import EvalItem
from codeatlas.eval.judge import judge_answer
from codeatlas.eval.metrics import citation_precision, hit_at_k, percentile, recall_at_k, reciprocal_rank
from codeatlas.llm import LLM
from codeatlas.retrieval.retriever import RetrievalConfig

if TYPE_CHECKING:
    from codeatlas.atlas import CodeAtlas


@dataclass
class ItemResult:
    id: str
    question: str
    category: str
    answerable: bool
    latency_ms: float
    retrieved: list[str]
    recall: float | None = None
    hit: float | None = None
    reciprocal_rank: float | None = None
    answer: str | None = None
    refused: bool | None = None
    citation_precision: float | None = None
    faithfulness: float | None = None
    correct: bool | None = None


@dataclass
class EvalReport:
    config: str
    k: int
    items: list[ItemResult] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        answerable = [r for r in self.items if r.answerable]

        def mean(values: list[float | None]) -> float | None:
            present = [v for v in values if v is not None]
            return round(statistics.fmean(present), 4) if present else None

        latencies = [r.latency_ms for r in self.items]
        out: dict[str, Any] = {
            "config": self.config,
            "k": self.k,
            "questions": len(self.items),
            f"recall@{self.k}": mean([r.recall for r in answerable]),
            f"hit@{self.k}": mean([r.hit for r in answerable]),
            "mrr": mean([r.reciprocal_rank for r in answerable]),
            "p50_ms": round(percentile(latencies, 0.5), 1),
            "p95_ms": round(percentile(latencies, 0.95), 1),
        }
        if any(r.refused is not None for r in self.items):
            out["refusal_accuracy"] = mean(
                [float(r.refused != r.answerable) for r in self.items if r.refused is not None]
            )
            out["citation_precision"] = mean([r.citation_precision for r in answerable])
            out["faithfulness"] = mean([r.faithfulness for r in answerable])
            correct = [float(r.correct) for r in answerable if r.correct is not None]
            out["answer_accuracy"] = round(statistics.fmean(correct), 4) if correct else None
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"summary": self.summary(), "items": [asdict(r) for r in self.items]}


def evaluate(
    atlas: CodeAtlas,
    items: list[EvalItem],
    cfg: RetrievalConfig,
    *,
    with_answers: bool = False,
    judge: LLM | None = None,
) -> EvalReport:
    report = EvalReport(config=cfg.name, k=cfg.k)
    for item in items:
        start = time.perf_counter()
        answer = None
        if with_answers:
            answer = atlas.engine.ask(item.question, cfg)
            chunks = [c for c in answer.context if c.kind != "graph"]
        else:
            chunks = [sc.chunk for sc in atlas.retriever.retrieve(item.question, cfg).chunks]
        latency = (time.perf_counter() - start) * 1000

        result = ItemResult(
            id=item.id,
            question=item.question,
            category=item.category,
            answerable=item.answerable,
            latency_ms=round(latency, 1),
            retrieved=[c.location for c in chunks[: cfg.k]],
        )
        if item.answerable and item.expected_sources:
            result.recall = recall_at_k(item.expected_sources, chunks, cfg.k)
            result.hit = hit_at_k(item.expected_sources, chunks, cfg.k)
            result.reciprocal_rank = reciprocal_rank(item.expected_sources, chunks[: cfg.k])
        if answer is not None:
            result.answer = answer.answer
            result.refused = answer.refused
            if item.answerable and not answer.refused:
                result.citation_precision = citation_precision(
                    item.expected_sources, [c.chunk for c in answer.citations]
                )
                if judge is not None:
                    verdict = judge_answer(judge, item.question, answer.answer, answer.context, item.reference_answer)
                    result.faithfulness = verdict["faithfulness"]
                    result.correct = verdict["correct"]
        report.items.append(result)
    return report


def to_markdown(reports: list[EvalReport]) -> str:
    if not reports:
        return ""
    summaries = [r.summary() for r in reports]
    columns = [c for c in summaries[0] if c not in ("questions",)]
    for s in summaries[1:]:
        columns += [c for c in s if c not in columns and c != "questions"]
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    for s in summaries:
        cells = []
        for c in columns:
            v = s.get(c)
            cells.append("-" if v is None else (f"{v:.3f}" if isinstance(v, float) and c not in ("p50_ms", "p95_ms") else str(v)))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)
