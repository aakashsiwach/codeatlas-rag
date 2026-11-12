"""SQLite store for chunks, file hashes, parsed Java facts and index-run history.

This is the source of truth for incremental indexing (file hashes) and for the in-memory BM25
index and code graph, which are rebuilt from it on load.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from codeatlas.models import Chunk

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    category TEXT NOT NULL,
    indexed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    symbol TEXT,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
CREATE INDEX IF NOT EXISTS idx_chunks_symbol ON chunks(symbol);
CREATE TABLE IF NOT EXISTS java_facts (path TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS index_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class ChunkStore:
    def __init__(self, db_path: Path | str):
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- files -----------------------------------------------------------------------------

    def file_hashes(self) -> dict[str, str]:
        with self._lock:
            return dict(self._conn.execute("SELECT path, sha256 FROM files").fetchall())

    def replace_file(
        self,
        path: str,
        sha256: str,
        category: str,
        chunks: Iterable[Chunk],
        facts: dict[str, Any] | None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM chunks WHERE path = ?", (path,))
            self._conn.executemany(
                "INSERT OR REPLACE INTO chunks (id, path, kind, symbol, data) VALUES (?, ?, ?, ?, ?)",
                [(c.id, c.path, c.kind, c.symbol, json.dumps(c.to_dict())) for c in chunks],
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO files (path, sha256, category, indexed_at) VALUES (?, ?, ?, ?)",
                (path, sha256, category, time.time()),
            )
            if facts is None:
                self._conn.execute("DELETE FROM java_facts WHERE path = ?", (path,))
            else:
                self._conn.execute(
                    "INSERT OR REPLACE INTO java_facts (path, data) VALUES (?, ?)",
                    (path, json.dumps(facts)),
                )

    def remove_file(self, path: str) -> None:
        with self._lock, self._conn:
            for table in ("chunks", "files", "java_facts"):
                self._conn.execute(f"DELETE FROM {table} WHERE path = ?", (path,))

    def clear(self) -> None:
        with self._lock, self._conn:
            for table in ("chunks", "files", "java_facts"):
                self._conn.execute(f"DELETE FROM {table}")

    # --- chunks ----------------------------------------------------------------------------

    def all_chunks(self) -> list[Chunk]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM chunks").fetchall()
        return [Chunk.from_dict(json.loads(r[0])) for r in rows]

    def chunks_for_path(self, path: str) -> list[Chunk]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM chunks WHERE path = ?", (path,)).fetchall()
        return [Chunk.from_dict(json.loads(r[0])) for r in rows]

    def count_chunks(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def all_facts(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM java_facts").fetchall()
        return [json.loads(r[0]) for r in rows]

    # --- runs and metadata -----------------------------------------------------------------

    def record_run(self, stats: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO index_runs (started_at, data) VALUES (?, ?)", (time.time(), json.dumps(stats))
            )

    def runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM index_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
