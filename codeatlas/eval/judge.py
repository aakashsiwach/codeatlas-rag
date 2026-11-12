"""LLM-as-judge for answer faithfulness (are claims supported by the retrieved context?)."""

from __future__ import annotations

import json
import re
from typing import Any

from codeatlas.answer import build_prompt
from codeatlas.llm import LLM
from codeatlas.models import Chunk

JUDGE_SYSTEM = "You are a strict evaluator of answers about a software codebase. Respond with JSON only."
JUDGE_PROMPT = """{context}

Answer to evaluate:
<answer>
{answer}
</answer>
{reference}
Split the answer into atomic factual claims (ignore citations, hedges and suggestions).
For each claim decide whether it is fully supported by the context blocks.
Respond with only this JSON object:
{{"claims": <number of claims>, "supported": <number of supported claims>, "correct": <true, false or null>}}
"correct" says whether the answer agrees with the reference answer; use null when there is no reference."""

_JSON = re.compile(r"\{.*\}", re.DOTALL)


def judge_answer(
    llm: LLM, question: str, answer: str, context: list[Chunk], reference: str | None = None
) -> dict[str, Any]:
    prompt = JUDGE_PROMPT.format(
        context=build_prompt(question, context),
        answer=answer,
        reference=f"\nReference answer:\n<reference>\n{reference}\n</reference>\n" if reference else "",
    )
    resp = llm.complete(JUDGE_SYSTEM, prompt, 300)
    match = _JSON.search(resp.text)
    try:
        data = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        data = {}
    claims = int(data.get("claims") or 0)
    supported = int(data.get("supported") or 0)
    return {
        "faithfulness": 1.0 if claims == 0 else min(1.0, supported / claims),
        "correct": data.get("correct"),
        "judge_tokens": resp.usage.input_tokens + resp.usage.output_tokens,
    }
