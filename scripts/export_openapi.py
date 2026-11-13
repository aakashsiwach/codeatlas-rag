"""Export the HTTP API's OpenAPI schema to docs/openapi.json (offline: no models, no index, no keys).

    python scripts/export_openapi.py          # write docs/openapi.json
    python scripts/export_openapi.py --check  # exit 1 if it is out of date (tests run this)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "openapi.json"


def schema() -> dict:
    from codeatlas.api.app import create_app
    from codeatlas.atlas import CodeAtlas
    from codeatlas.config import Settings

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # Windows keeps the SQLite file open
        settings = Settings(
            _env_file=None,
            index_dir=Path(tmp),
            qdrant_url=":memory:",
            embedding_provider="hashing",
            reranker_model="",
            llm_provider="fake",
            trace_enabled=False,
        )
        return create_app(atlas=CodeAtlas(settings)).openapi()


def render() -> str:
    return json.dumps(schema(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8").replace("\r\n", "\n") if OUT.exists() else ""
        if current != text:
            print(f"{OUT.relative_to(ROOT)} is out of date: run python scripts/export_openapi.py")
            return 1
        print(f"{OUT.relative_to(ROOT)} is up to date")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
