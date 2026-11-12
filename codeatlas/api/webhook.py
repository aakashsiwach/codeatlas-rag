"""GitHub push webhook helpers."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

# GitHub includes at most 20 commits in a push payload; beyond that the file list is incomplete.
_MAX_PAYLOAD_COMMITS = 20


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def changed_paths(payload: dict[str, Any]) -> list[str] | None:
    """Files added, modified or removed by a push. None means "unknown, rescan everything"
    (the incremental hash check still skips unchanged files)."""
    commits = payload.get("commits") or []
    if len(commits) >= _MAX_PAYLOAD_COMMITS or payload.get("forced"):
        return None
    paths: set[str] = set()
    for commit in commits:
        for key in ("added", "modified", "removed"):
            paths.update(commit.get(key) or [])
    return sorted(paths)
