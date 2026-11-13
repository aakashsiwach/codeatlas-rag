"""Serve the CodeAtlas API over the test fixture repository with the offline LLM, for UI end-to-end tests.

No network, no model downloads and no API keys: hashing embeddings, no reranker, the "fake" LLM provider
and an in-memory vector store. Usage: python ui/e2e/serve_fixture.py [port]
"""

import shutil
import sys
import tempfile
from pathlib import Path

import uvicorn

from codeatlas.api.app import create_app
from codeatlas.atlas import CodeAtlas
from codeatlas.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    work = Path(tempfile.mkdtemp(prefix="codeatlas-e2e-"))
    repo = work / "shop"
    shutil.copytree(ROOT / "tests" / "fixtures" / "shop", repo)
    settings = Settings(
        _env_file=None,
        index_dir=work / "index",
        qdrant_url=":memory:",
        embedding_provider="hashing",
        reranker_model="",
        llm_provider="fake",
        llm_model="offline",
        default_mode="hybrid+graph",
        # The frozen setups without the reranker (no model download): fast = vector, quality = hybrid+graph.
        setups={"fast": {"mode": "vector", "k": 5}, "quality": {"mode": "hybrid+graph", "k": 5}},
        default_k=5,
        trace_enabled=False,
    )
    atlas = CodeAtlas(settings)
    atlas.index(repo)
    uvicorn.run(create_app(atlas=atlas), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
