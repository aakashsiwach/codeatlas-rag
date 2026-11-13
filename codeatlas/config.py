"""Runtime configuration, loaded from CODEATLAS_* environment variables or a .env file."""

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

    # Storage
    index_dir: Path = Path(".codeatlas")
    repo_path: Path | None = None
    qdrant_url: str | None = None  # None = embedded Qdrant under index_dir; ":memory:" for tests
    qdrant_api_key: str | None = None
    collection: str = "codeatlas"

    # Ingestion
    exclude_dirs: list[str] = DEFAULT_EXCLUDES
    max_file_bytes: int = 1_000_000
    max_chunk_lines: int = 120
    doc_chunk_chars: int = 2400
    naive_chunk_chars: int = 1500

    # Embeddings: local | openai | voyage | hashing
    embedding_provider: str = "local"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_batch_size: int = 64
    embedding_cost_per_mtok: float = 0.0

    # Reranking (cross-encoder, requires the "local" extra). Empty string disables it.
    reranker_model: str = "Alibaba-NLP/gte-reranker-modernbert-base"

    # Generation: anthropic | openai | bedrock | none
    llm_provider: str = "anthropic"
    llm_model: str | None = None
    llm_base_url: str | None = None  # OpenAI-compatible servers (vLLM, Ollama, LM Studio)
    llm_max_tokens: int = 1200
    llm_timeout_s: float = 120.0  # per request
    llm_max_retries: int = 4  # with backoff, on throttling and transient errors
    # USD prices per million tokens, keyed by "<provider>:<model>" so the same model on Bedrock and on the
    # Anthropic API are separate entries. JSON, e.g.
    # {"bedrock:<model id>": {"input_per_mtok": <usd>, "output_per_mtok": <usd>}}. There are no built-in
    # prices: a model without an entry is reported as "unpriced", never as $0.
    llm_prices: dict[str, dict[str, float]] = {}
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

    # Retrieval defaults and hallucination controls
    # Frozen on the dev split (README, "Frozen setups"): `fast` (vector) is the default;
    # `quality` is hybrid+rerank+graph with the reranker above.
    default_mode: str = "vector"
    default_k: int = 5
    min_vector_similarity: float = 0.30
    min_rerank_confidence: float = 0.05
    # Per-model refusal thresholds, as JSON {"<embedder name or reranker model>": <threshold>}; they override
    # the two above for that model. Calibrate them on a dev split with `codeatlas eval --calibrate`.
    # Defaults calibrated on dev for the two frozen setups.
    # Named setups the API and UI offer per question; the two frozen on dev (README, "Frozen setups").
    setups: dict[str, dict[str, str | int]] = {
        "fast": {"mode": "vector", "k": 5},
        "quality": {"mode": "hybrid+rerank+graph", "k": 5},
    }
    default_setup: str = "fast"
    min_confidence_by_model: dict[str, float] = {
        "local:BAAI/bge-small-en-v1.5": 0.6177,
        "Alibaba-NLP/gte-reranker-modernbert-base": 0.868,
    }

    # API, webhook and tracing
    cors_origins: list[str] = ["http://localhost:5173"]
    # Bearer token required on every /api/* route when set (the GitHub webhook uses its HMAC instead).
    api_token: str | None = None
    # `codeatlas serve` refuses a non-loopback host without api_token unless this is set, e.g. in a
    # container whose published port is bound to 127.0.0.1 on the host.
    allow_unauthenticated: bool = False
    # POST /api/index may only re-index the configured repository unless this is set.
    allow_index_any_path: bool = False
    webhook_secret: str | None = None
    webhook_branch: str | None = None
    git_pull_on_webhook: bool = False
    trace_enabled: bool = True
