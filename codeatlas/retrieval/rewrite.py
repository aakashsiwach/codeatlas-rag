"""LLM query rewriting: turn a natural-language question into identifier-rich search queries."""

from __future__ import annotations

import re
from collections.abc import Callable

from codeatlas.llm import LLM

REWRITE_SYSTEM = "You turn developer questions about a Java/Spring Boot codebase into search queries."
REWRITE_PROMPT = """Question: {question}

Write up to 3 alternative search queries that would find the relevant code or documentation.
Use likely Java class, method, annotation and endpoint names, for example
"PaymentService charge @Retryable backoff". One query per line, no numbering, no other text."""

_BULLET = re.compile(r"^\s*(?:[-*]|\d+[.)])\s*")


class QueryRewriter:
    def __init__(self, llm_factory: Callable[[], LLM], max_queries: int = 3):
        self._llm_factory = llm_factory
        self.max_queries = max_queries

    def rewrite(self, question: str) -> list[str]:
        resp = self._llm_factory().complete(REWRITE_SYSTEM, REWRITE_PROMPT.format(question=question), 200)
        queries: list[str] = []
        for line in resp.text.splitlines():
            q = _BULLET.sub("", line).strip().strip('"')
            if q and q.lower() != question.lower() and q not in queries:
                queries.append(q)
        return queries[: self.max_queries]
