"""Code-aware tokenizer: keeps identifiers whole and also splits camelCase/snake_case parts."""

from __future__ import annotations

import re

_WORD = re.compile(r"/[A-Za-z0-9_\-{}/.]+|[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_PATH_SPLIT = re.compile(r"[/{}\-.]+")

STOPWORDS = frozenset(
    """
    a an the is are was were be been of to in on at for from by with and or not no if then
    it its this that these those what which where when who whom how why does do did can could
    should would will shall may might i we you he she they me my our your there here as into
    about any all some only also just than so such very
    public private protected static final void return new import package class interface
    extends implements this null true false var
    """.split()
)


def code_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _WORD.finditer(text):
        word = match.group()
        if word.startswith("/"):
            tokens.append(word.lower())
            tokens.extend(p.lower() for p in _PATH_SPLIT.split(word) if len(p) > 1)
            continue
        lowered = word.lower()
        if lowered not in STOPWORDS:
            tokens.append(lowered)
        parts = [p.lower() for piece in word.split("_") for p in _CAMEL.findall(piece)]
        if len(parts) > 1:
            tokens.extend(p for p in parts if len(p) > 1 and p not in STOPWORDS)
    return tokens
