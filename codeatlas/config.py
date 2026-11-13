"""Runtime configuration, loaded from CODEATLAS_* environment variables or a .env file.

Every field below is documented by the comment directly above it (or at the end of its line). The
`# == Section ==` lines group them. docs/configuration.md is generated from this file by
scripts/gen_config_docs.py; a test fails when it is out of date.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_EXCLUDES = [
    ".git",
    ".idea",
    ".vscode",
    ".gradle",
    ".mvn",
    ".codeatlas",
    "target",
    "build",
    "out",
    "bin",
    "dist",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODEATLAS_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # == Storage ==
    # Directory for the SQLite store, the embedded Qdrant data and traces.jsonl.
    index_dir: Path = Path(".codeatlas")
    # The repository the API indexes by default (POST /api/index and the GitHub webhook).
    repo_path: Path | None = None
    # Qdrant server URL. Unset: embedded Qdrant under index_dir. ":memory:" keeps vectors in memory (tests).
    qdrant_url: str | None = None
    # API key for a Qdrant server.
    qdrant_api_key: str | None = None
    # Qdrant collection name.
    collection: str = "codeatlas"

    # == Ingestion ==
    # Directory names skipped while walking the repository (JSON list).
    exclude_dirs: list[str] = DEFAULT_EXCLUDES
    # Files larger than this many bytes are skipped.
    max_file_bytes: int = 1_000_000
    # Class outlines and methods longer than this many lines are split into overlapping windows.
    max_chunk_lines: int = 120
    # Maximum characters per Markdown/AsciiDoc/PDF section chunk.
    doc_chunk_chars: int = 2400
    # Window size in characters for the naive fixed-size chunker (`index --chunker naive`, the baseline).
    naive_chunk_chars: int = 1500

    # == Embeddings ==
    # local (sentence-transformers) | openai | voyage | hashing (offline, for tests and demos).
    embedding_provider: str = "local"
    # Embedding model name for the provider.
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    # Texts per embedding batch.
    embedding_batch_size: int = 64
    # USD per million embedding tokens, for the index-run cost estimate.
    embedding_cost_per_mtok: float = 0.0

    # == Reranking ==
    # Cross-encoder used by `+rerank` modes (needs the "local" extra). Empty string disables reranking.
    reranker_model: str = "Alibaba-NLP/gte-reranker-modernbert-base"

    # == Generation ==
    # anthropic | openai (also OpenAI-compatible servers such as Ollama) | bedrock | fake (offline) | none.
    llm_provider: str = "anthropic"
    # Model id for the provider (Bedrock: the inference profile id). The fake provider defaults to "offline".
    llm_model: str | None = None
    # Base URL for OpenAI-compatible servers (vLLM, Ollama, LM Studio).
    llm_base_url: str | None = None
    # Output token limit per answer.
    llm_max_tokens: int = 1200
    # Timeout per LLM request, in seconds.
    llm_timeout_s: float = 120.0
    # Retries with backoff on throttling and transient errors.
    llm_max_retries: int = 4
    # USD prices per million tokens, keyed by "<provider>:<model>" so the same model on Bedrock and on the
    # Anthropic API are separate entries. JSON, e.g.
    # {"bedrock:<model id>": {"input_per_mtok": <usd>, "output_per_mtok": <usd>}}. There are no built-in
    # prices: a model without an entry is reported as "unpriced", never as $0.
    llm_prices: dict[str, dict[str, float]] = {}
    # AWS region for Bedrock. Unset uses the SDK default.
    aws_region: str | None = None
    # Named profile from ~/.aws for Bedrock. Unset uses the SDK default chain (AWS_PROFILE, then default).
    aws_profile: str | None = None
    # Thinking type sent with LLM-judge calls so reasoning cannot eat the judge's small output budget.
    # "disabled" works for most models; some newer ones reject it and turn thinking off with another
    # value (check the provider docs). Empty string sends nothing and keeps the model default.
    judge_thinking: str = "disabled"
    # Thinking type for answer generation. Empty keeps the model default; thinking tokens count against
    # llm_max_tokens, so a long think can truncate the visible answer (reported as `truncated`).
    answer_thinking: str = ""
    # Per-model overrides, as JSON: {"<model id>": {"answer": "<type>", "judge": "<type>"}}. They win over
    # answer_thinking / judge_thinking for that model, since valid values differ between models.
    thinking_by_model: dict[str, dict[str, str]] = {}

    # == Retrieval and refusal ==
    # Mode when a request names no mode or setup. Frozen on the dev split (README, "Frozen setups"):
    # `vector` is the `fast` setup.
    default_mode: str = "vector"
    # Chunks retrieved when a request names no k or setup.
    default_k: int = 5
    # Named setups the API and UI offer per question; the two frozen on dev (README, "Frozen setups").
    setups: dict[str, dict[str, str | int]] = {
        "fast": {"mode": "vector", "k": 5},
        "quality": {"mode": "hybrid+rerank+graph", "k": 5},
    }
    # Setup the UI selects by default.
    default_setup: str = "fast"
    # Refusal threshold on vector similarity (no reranker) for embedders without a per-model value.
    min_vector_similarity: float = 0.30
    # Refusal threshold on the reranker score for rerankers without a per-model value.
    min_rerank_confidence: float = 0.05
    # Per-model refusal thresholds, as JSON {"<embedder name or reranker model>": <threshold>}; they override
    # the two above for that model. The defaults were calibrated on the dev split for the two frozen setups
    # (`codeatlas eval --calibrate`).
    min_confidence_by_model: dict[str, float] = {
        "local:BAAI/bge-small-en-v1.5": 0.6177,
        "Alibaba-NLP/gte-reranker-modernbert-base": 0.868,
    }

    # == API, webhook and tracing ==
    # Browser origins allowed by CORS (JSON list). The Vite dev server's origin by default.
    cors_origins: list[str] = ["http://localhost:5173"]
    # Directory with the built web UI (ui/dist). When set, the API serves it at / (one container, one origin).
    ui_dir: Path | None = None
    # Seconds between `progress` events on /api/ask/stream while nothing else is sent. Keeps proxies with a
    # first-byte or idle timeout (CloudFront: 60 s) from cutting off slow (quality-setup) retrievals.
    sse_heartbeat_s: float = 5.0
    # Bearer token required on every /api/* route when set (the GitHub webhook uses its HMAC instead).
    api_token: str | None = None
    # `codeatlas serve` refuses a non-loopback host without api_token unless this is set, e.g. in a
    # container whose published port is bound to 127.0.0.1 on the host.
    allow_unauthenticated: bool = False
    # POST /api/index may only re-index the configured repository unless this is set.
    allow_index_any_path: bool = False
    # Secret for verifying GitHub webhook signatures (X-Hub-Signature-256). Unset disables the webhook.
    webhook_secret: str | None = None
    # Only pushes to this branch trigger a re-index. Unset: every branch.
    webhook_branch: str | None = None
    # Run `git pull` in repo_path before a webhook-triggered re-index.
    git_pull_on_webhook: bool = False
    # Write per-query traces to index_dir/traces.jsonl.
    trace_enabled: bool = True
