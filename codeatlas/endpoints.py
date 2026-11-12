"""Helpers for matching HTTP paths across Spring mappings, Feign clients and string literals."""

from __future__ import annotations

import re

# A path mentioned in a natural-language question, e.g. "/orders/{id}".
QUERY_PATH = re.compile(r"(?<![\w/])/[A-Za-z0-9_\-{}/.]*[A-Za-z0-9_}]")
_URL_PATH = re.compile(r"^(?:https?://[^/\s]+)?(/[^\s?#\"]*)")
_VIEW_PREFIXES = ("redirect:", "forward:")


def is_param(segment: str) -> bool:
    return (
        (segment.startswith("{") and segment.endswith("}"))
        or segment.startswith(":")
        or segment.startswith("${")
    )


def segments(path: str) -> list[str]:
    path = path.split("?", 1)[0]
    return ["{}" if is_param(s) else s.lower() for s in path.strip("/").split("/") if s]


def paths_match(pattern: str, candidate: str, prefix: bool = False) -> bool:
    """True when two paths match segment by segment, treating {vars} as wildcards.

    With prefix=True the candidate only has to match the start of the pattern, which is how
    string literals such as "/owners/" + id are used in code.
    """
    a, b = segments(pattern), segments(candidate)
    if not a or not b:
        return False
    if prefix:
        if len(b) > len(a):
            return False
    elif len(a) != len(b):
        return False
    return all(x == y or x == "{}" or y == "{}" for x, y in zip(a, b))


def endpoint_path(endpoint: str) -> str:
    """'GET /orders/{id}' -> '/orders/{id}'."""
    return endpoint.split(" ", 1)[-1]


def join_paths(base: str, sub: str) -> str:
    parts = [p.strip("/") for p in (base, sub) if p and p.strip("/")]
    return "/" + "/".join(parts) if parts else "/"


def literal_path(value: str) -> str | None:
    """Extract an HTTP path from a string literal, or None if it does not look like one."""
    value = value.strip()
    for prefix in _VIEW_PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix) :]
    match = _URL_PATH.match(value)
    if not match or len(match.group(1)) < 2:
        return None
    return match.group(1)
