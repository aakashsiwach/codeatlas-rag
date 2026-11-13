"""Minimal per-query tracing: spans with latency, tokens and cost, appended to a JSONL file."""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class Trace:
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    spans: list[dict[str, Any]] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    _t0: float = field(default_factory=time.perf_counter, repr=False)

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[dict[str, Any]]:
        record: dict[str, Any] = {"name": name, **attributes}
        start = time.perf_counter()
        try:
            yield record
        finally:
            record["ms"] = round((time.perf_counter() - start) * 1000, 1)
            self.spans.append(record)


class Tracer:
    """Appends one JSON line per query to `path`. When the file reaches `max_bytes`, it is moved to
    `<name>.1` (replacing an older one) and a new file is started, so traces never grow without bound."""

    def __init__(self, path: Path | None, max_bytes: int = 10_000_000):
        self.path = path
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def start(self, name: str, **attributes: Any) -> Trace:
        return Trace(name=name, attributes=dict(attributes))

    def finish(self, trace: Trace, **attributes: Any) -> None:
        trace.attributes.update(attributes)
        trace.attributes["total_ms"] = round((time.perf_counter() - trace._t0) * 1000, 1)
        if self.path is None:
            return
        record = {
            "trace_id": trace.id,
            "name": trace.name,
            "timestamp": trace.timestamp,
            **trace.attributes,
            "spans": trace.spans,
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.max_bytes > 0 and self.path.exists() and self.path.stat().st_size >= self.max_bytes:
                self.path.replace(self.path.with_name(self.path.name + ".1"))
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
