"""Evaluation sets: questions with the source files/symbols that a correct answer must use.

YAML format:

    questions:
      - id: owner-search
        question: How does searching owners by last name work?
        category: flow
        expected_sources:
          - owner/OwnerController.java::OwnerController.processFindForm
          - owner/OwnerRepository.java
        reference_answer: optional free text used by the LLM judge
      - id: no-graphql
        question: Where is the GraphQL schema defined?
        answerable: false        # the system should refuse
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from codeatlas.models import Chunk


@dataclass(frozen=True)
class ExpectedSource:
    path: str  # path suffix, e.g. "owner/OwnerController.java"
    symbol: str | None = None  # "OwnerController" or "OwnerController.processFindForm"

    @classmethod
    def parse(cls, spec: str) -> ExpectedSource:
        path, _, symbol = spec.partition("::")
        return cls(path.strip().replace("\\", "/"), symbol.strip() or None)

    def matches(self, chunk: Chunk) -> bool:
        if not ("/" + chunk.path).endswith("/" + self.path.lstrip("/")):
            return False
        if not self.symbol:
            return True
        if chunk.symbol:
            return f".{self.symbol}." in "." + chunk.symbol.replace("#", ".") + "."
        name = self.symbol.split(".")[-1]  # chunks without symbols (naive baseline): match by text
        return re.search(rf"\b{re.escape(name)}\b", chunk.text) is not None


@dataclass
class EvalItem:
    id: str
    question: str
    expected_sources: list[ExpectedSource] = field(default_factory=list)
    category: str = "general"
    reference_answer: str | None = None
    answerable: bool = True


def load_eval_set(path: Path | str) -> list[EvalItem]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    items: list[EvalItem] = []
    for i, raw in enumerate(data.get("questions", []), start=1):
        items.append(
            EvalItem(
                id=str(raw.get("id", f"q{i}")),
                question=raw["question"],
                expected_sources=[ExpectedSource.parse(s) for s in raw.get("expected_sources", [])],
                category=raw.get("category", "general"),
                reference_answer=raw.get("reference_answer"),
                answerable=bool(raw.get("answerable", True)),
            )
        )
    return items
