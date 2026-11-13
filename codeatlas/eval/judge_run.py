"""Helpers for a judged evaluation run: the reviewed-references gate, a single answer + judge check with a
cost estimate, and the answer-quality summary over several judged reports (scripts/run-judge.*)."""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codeatlas.eval.dataset import EvalItem
from codeatlas.eval.judge import judge_answer
from codeatlas.llm import llm_cost_usd, thinking_for

# The single checked question stands in for the average one; questions vary, so the estimate carries a margin.
ESTIMATE_MARGIN = 1.25


def unreviewed(items: list[EvalItem]) -> list[str]:
    """Ids of questions whose reference answer is not marked reviewed."""
    return [i.id for i in items if i.reference_answer and not i.reference_reviewed]


@dataclass
class CheckResult:
    question_id: str
    answer: dict[str, Any]
    judge: dict[str, Any]
    answer_cost_usd: float | None
    judge_cost_usd: float | None
    problems: list[str] = field(default_factory=list)


def single_check(atlas, items: list[EvalItem], cfg, question_id: str | None = None) -> CheckResult:
    """Answer one answerable question with cfg and judge it: the two LLM calls a judged run makes per question.
    Questions refused on retrieval confidence (no LLM call) are skipped, up to the first one answered."""
    candidates = [i for i in items if i.answerable and (question_id is None or i.id == question_id)]
    if not candidates:
        raise ValueError(f"no answerable question{f' with id {question_id!r}' if question_id else ''} in the set")
    settings = atlas.settings
    for item in candidates:
        ans = atlas.engine.ask(item.question, cfg)
        if not ans.refused:
            break
    else:
        raise ValueError("every candidate question was refused on retrieval confidence; pass another --question")
    reference = item.reference_answer if item.reference_reviewed else None
    verdict = judge_answer(
        atlas.llm, item.question, ans.answer, ans.context, reference, thinking=thinking_for(settings, "judge")
    )
    usage = verdict["judge_usage"]
    answer = {
        "stop_reason": ans.stop_reason,
        "input_tokens": ans.usage.get("input_tokens", 0),
        "output_tokens": ans.usage.get("output_tokens", 0),
        "truncated": ans.truncated,
        "citations": [c.index for c in ans.citations],
    }
    judge = {
        "stop_reason": verdict["judge_stop_reason"],
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "truncated": verdict["judge_error"] == "judge_truncated",
        "judge_error": verdict["judge_error"],
        "faithfulness": verdict["faithfulness"],
        "correct": verdict["correct"],
        "reference_used": reference is not None,
    }
    problems = []
    if ans.truncated:
        problems.append("the answer was truncated at the output limit")
    if verdict["judge_error"]:
        problems.append(f"the judge verdict failed: {verdict['judge_error']}")
    return CheckResult(
        question_id=item.id,
        answer=answer,
        judge=judge,
        answer_cost_usd=llm_cost_usd(settings, answer["input_tokens"], answer["output_tokens"]),
        judge_cost_usd=llm_cost_usd(settings, judge["input_tokens"], judge["output_tokens"]),
        problems=problems,
    )


def estimate_cost(check: CheckResult, items: list[EvalItem], passes: int) -> float | None:
    """Estimated cost of `passes` judged runs: an answer per question and a verdict per answerable one,
    each priced like the checked question, times ESTIMATE_MARGIN. None when the model is unpriced."""
    if check.answer_cost_usd is None or check.judge_cost_usd is None:
        return None
    answerable = sum(1 for i in items if i.answerable)
    per_pass = len(items) * check.answer_cost_usd + answerable * check.judge_cost_usd
    return round(per_pass * passes * ESTIMATE_MARGIN, 4)


def _mean_sd(values: list[float]) -> str:
    if not values:
        return "–"
    mean = statistics.fmean(values)
    return f"{mean:.3f}" if len(values) == 1 else f"{mean:.3f} ± {statistics.stdev(values):.3f}"


def summarize(paths: list[Path]) -> dict[str, Any]:
    """Answer quality per configuration over judged eval reports (JSON files written by `codeatlas eval`)."""
    rows: dict[str, list[dict[str, Any]]] = {}
    total_cost: float | None = 0.0
    for path in paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for report in data["reports"]:
            summary = report["summary"]
            if "faithfulness" not in summary:
                raise ValueError(f"{path} has no judged answers (run codeatlas eval with --judge)")
            rows.setdefault(summary["config"], []).append(summary)
            cost = summary.get("llm_cost_usd")
            total_cost = None if total_cost is None or not isinstance(cost, (int, float)) else total_cost + cost
    return {"rows": rows, "total_cost_usd": None if total_cost is None else round(total_cost, 4)}


METRICS = [
    ("faithfulness", "Faithfulness"),
    ("citation_precision", "Citation precision"),
    ("refusal_accuracy", "Refusal accuracy"),
    ("answer_accuracy", "Answer accuracy (vs reviewed references)"),
]


def summary_markdown(summary: dict[str, Any]) -> str:
    """The README answer-quality table: mean ± standard deviation over the judged runs."""
    lines = [
        "| Setup | Runs | " + " | ".join(label for _, label in METRICS) + " | Judge errors | Truncated answers |",
        "|---|---|" + "---|" * len(METRICS) + "---|---|",
    ]
    for config, runs in summary["rows"].items():
        cells = [_mean_sd([r[key] for r in runs if r.get(key) is not None]) for key, _ in METRICS]
        errors = sum((r.get("judge_truncated") or 0) + (r.get("judge_unparseable") or 0) for r in runs)
        truncated = sum(r.get("answers_truncated") or 0 for r in runs)
        lines.append(f"| {config} | {len(runs)} | " + " | ".join(cells) + f" | {errors} | {truncated} |")
    cost = summary["total_cost_usd"]
    lines.append("")
    lines.append(f"Total LLM cost of these runs: {'unpriced' if cost is None else f'${cost:.4f}'}")
    return "\n".join(lines)
