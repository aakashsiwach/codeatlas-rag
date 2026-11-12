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

from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from codeatlas.models import Chunk

# A resolved definition: (repo-relative path, first line, last line), 1-based and inclusive.
Definition = tuple[str, int, int]


@dataclass(frozen=True)
class ExpectedSource:
    """A file, class or method that a correct answer must use.

    Symbol matching is by location, never by name: a chunk matches a symbol only if it comes from the
    same file and its line range overlaps the symbol's definition. Every chunker (AST, naive) is
    scored by the same rule. Class-outline chunks hold only member signatures, so they cover their
    class but none of its methods.
    """

    path: str  # path suffix, e.g. "owner/OwnerController.java"
    symbol: str | None = None  # "OwnerController" or "OwnerController.processFindForm"
    definitions: tuple[Definition, ...] | None = None  # set by resolve_expected_sources
    is_method: bool = False

    @classmethod
    def parse(cls, spec: str) -> ExpectedSource:
        path, _, symbol = spec.partition("::")
        return cls(path.strip().replace("\\", "/"), symbol.strip() or None)

    def matches(self, chunk: Chunk) -> bool:
        if not ("/" + chunk.path).endswith("/" + self.path.lstrip("/")):
            return False
        if not self.symbol:
            return True
        if self.definitions is None:
            raise ValueError(f"{self.path}::{self.symbol} is unresolved; call resolve_expected_sources first")
        if self.is_method and chunk.kind == "class":
            return False
        return any(
            chunk.path == path and chunk.start_line <= end and start <= chunk.end_line
            for path, start, end in self.definitions
        )


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


_Defs = dict[str, list[Definition]]


def _java_definitions(rel_path: str, source: str) -> tuple[_Defs, _Defs]:
    """Definition line ranges in one Java file: types ('Type', 'Outer.Inner') and methods ('Type.method')."""
    from codeatlas.ingest.java import parse_java

    facts, _ = parse_java(rel_path, source)
    prefix = facts.package + "." if facts.package else ""
    types: _Defs = {}
    methods: _Defs = {}
    for t in facts.types:
        local = t.qualified_name[len(prefix) :] if t.qualified_name.startswith(prefix) else t.qualified_name
        types.setdefault(local, []).append((rel_path, t.start_line, t.end_line))
        for m in t.methods:  # overloads share a name: any of them counts
            methods.setdefault(f"{local}.{m.name}", []).append((rel_path, m.start_line, m.end_line))
    return types, methods


def _lookup(defs: _Defs, symbol: str) -> list[Definition]:
    """Exact name, or a suffix of a nested name ('Inner.method' finds 'Outer.Inner.method')."""
    return [d for name, found in defs.items() if name == symbol or name.endswith("." + symbol) for d in found]


def resolve_expected_sources(items: list[EvalItem], repo: Path | str) -> list[EvalItem]:
    """Attach definition line ranges to every expected symbol by parsing the repository's source.

    Raises ValueError listing every symbol that cannot be found, so a stale eval set fails loudly.
    """
    repo = Path(repo)
    files = [p.relative_to(repo).as_posix() for p in repo.rglob("*.java") if p.is_file()]
    cache: dict[str, tuple[_Defs, _Defs]] = {}
    missing: list[str] = []
    resolved: list[EvalItem] = []
    for item in items:
        sources: list[ExpectedSource] = []
        for es in item.expected_sources:
            if not es.symbol or es.definitions is not None:
                sources.append(es)
                continue
            method_defs: list[Definition] = []
            type_defs: list[Definition] = []
            for rel in (f for f in files if ("/" + f).endswith("/" + es.path.lstrip("/"))):
                if rel not in cache:
                    cache[rel] = _java_definitions(rel, (repo / rel).read_text(encoding="utf-8", errors="replace"))
                types, methods = cache[rel]
                if "." in es.symbol:  # a bare name is a type, never a constructor
                    method_defs += _lookup(methods, es.symbol)
                type_defs += _lookup(types, es.symbol)
            defs = method_defs or type_defs
            if not defs:
                missing.append(f"{item.id}: {es.path}::{es.symbol}")
            sources.append(replace(es, definitions=tuple(sorted(set(defs))), is_method=bool(method_defs)))
        resolved.append(replace(item, expected_sources=sources))
    if missing:
        raise ValueError("Expected sources not found in the repository:\n  " + "\n  ".join(missing))
    return resolved
