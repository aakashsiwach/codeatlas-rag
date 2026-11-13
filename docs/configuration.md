# Configuration

Every setting is an environment variable with the `CODEATLAS_` prefix, or a line in a `.env` file in the
working directory (`.env.example` is a starting point). Lists and dicts are JSON, e.g.
`CODEATLAS_MIN_CONFIDENCE_BY_MODEL='{"local:BAAI/bge-small-en-v1.5": 0.6}'`. Process environment variables
win over `.env`.

Provider credentials are not CodeAtlas settings: they come from each SDK's standard sources
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `~/.aws` profiles), never from files in the repository.

This page is generated from `codeatlas/config.py` by `scripts/gen_config_docs.py`. Do not edit it by hand.


## Storage

| Variable | Default | Meaning |
|---|---|---|
| `CODEATLAS_INDEX_DIR` | `".codeatlas"` | Directory for the SQLite store, the embedded Qdrant data and traces.jsonl. |
| `CODEATLAS_REPO_PATH` | unset | The repository the API indexes by default (POST /api/index and the GitHub webhook). |
| `CODEATLAS_QDRANT_URL` | unset | Qdrant server URL. Unset: embedded Qdrant under index_dir. ":memory:" keeps vectors in memory (tests). |
| `CODEATLAS_QDRANT_API_KEY` | unset | API key for a Qdrant server. |
| `CODEATLAS_COLLECTION` | `"codeatlas"` | Qdrant collection name. |

## Ingestion

| Variable | Default | Meaning |
|---|---|---|
| `CODEATLAS_EXCLUDE_DIRS` | `[".git", ".idea", ".vscode", ".gradle", ".mvn", ".codeatlas", "target", "build", "out", "bin", "dist", "node_modules", "__pycache__", ".venv", "venv"]` | Directory names skipped while walking the repository (JSON list). |
| `CODEATLAS_MAX_FILE_BYTES` | `1000000` | Files larger than this many bytes are skipped. |
| `CODEATLAS_MAX_CHUNK_LINES` | `120` | Class outlines and methods longer than this many lines are split into overlapping windows. |
| `CODEATLAS_DOC_CHUNK_CHARS` | `2400` | Maximum characters per Markdown/AsciiDoc/PDF section chunk. |
| `CODEATLAS_NAIVE_CHUNK_CHARS` | `1500` | Window size in characters for the naive fixed-size chunker (`index --chunker naive`, the baseline). |

## Embeddings

| Variable | Default | Meaning |
|---|---|---|
| `CODEATLAS_EMBEDDING_PROVIDER` | `"local"` | local (sentence-transformers) \| openai \| voyage \| hashing (offline, for tests and demos). |
| `CODEATLAS_EMBEDDING_MODEL` | `"BAAI/bge-small-en-v1.5"` | Embedding model name for the provider. |
| `CODEATLAS_EMBEDDING_BATCH_SIZE` | `64` | Texts per embedding batch. |
| `CODEATLAS_EMBEDDING_COST_PER_MTOK` | `0.0` | USD per million embedding tokens, for the index-run cost estimate. |

## Reranking

| Variable | Default | Meaning |
|---|---|---|
| `CODEATLAS_RERANKER_MODEL` | `"Alibaba-NLP/gte-reranker-modernbert-base"` | Cross-encoder used by `+rerank` modes (needs the "local" extra). Empty string disables reranking. |

## Generation

| Variable | Default | Meaning |
|---|---|---|
| `CODEATLAS_LLM_PROVIDER` | `"anthropic"` | anthropic \| openai (also OpenAI-compatible servers such as Ollama) \| bedrock \| fake (offline) \| none. |
| `CODEATLAS_LLM_MODEL` | unset | Model id for the provider (Bedrock: the inference profile id). The fake provider defaults to "offline". |
| `CODEATLAS_LLM_BASE_URL` | unset | Base URL for OpenAI-compatible servers (vLLM, Ollama, LM Studio). |
| `CODEATLAS_LLM_MAX_TOKENS` | `1200` | Output token limit per answer. |
| `CODEATLAS_LLM_TIMEOUT_S` | `120.0` | Timeout per LLM request, in seconds. |
| `CODEATLAS_LLM_MAX_RETRIES` | `4` | Retries with backoff on throttling and transient errors. |
| `CODEATLAS_LLM_PRICES` | `{}` | USD prices per million tokens, keyed by "<provider>:<model>" so the same model on Bedrock and on the Anthropic API are separate entries. JSON, e.g. {"bedrock:<model id>": {"input_per_mtok": <usd>, "output_per_mtok": <usd>}}. There are no built-in prices: a model without an entry is reported as "unpriced", never as $0. |
| `CODEATLAS_AWS_REGION` | unset | AWS region for Bedrock. Unset uses the SDK default. |
| `CODEATLAS_AWS_PROFILE` | unset | Named profile from ~/.aws for Bedrock. Unset uses the SDK default chain (AWS_PROFILE, then default). |
| `CODEATLAS_JUDGE_THINKING` | `"disabled"` | Thinking type sent with LLM-judge calls so reasoning cannot eat the judge's small output budget. "disabled" works for most models; some newer ones reject it and turn thinking off with another value (check the provider docs). Empty string sends nothing and keeps the model default. |
| `CODEATLAS_ANSWER_THINKING` | `""` | Thinking type for answer generation. Empty keeps the model default; thinking tokens count against llm_max_tokens, so a long think can truncate the visible answer (reported as `truncated`). |
| `CODEATLAS_THINKING_BY_MODEL` | `{}` | Per-model overrides, as JSON: {"<model id>": {"answer": "<type>", "judge": "<type>"}}. They win over answer_thinking / judge_thinking for that model, since valid values differ between models. |

## Retrieval and refusal

| Variable | Default | Meaning |
|---|---|---|
| `CODEATLAS_DEFAULT_MODE` | `"vector"` | Mode when a request names no mode or setup. Frozen on the dev split (README, "Frozen setups"): `vector` is the `fast` setup. |
| `CODEATLAS_DEFAULT_K` | `5` | Chunks retrieved when a request names no k or setup. |
| `CODEATLAS_SETUPS` | `{"fast": {"mode": "vector", "k": 5}, "quality": {"mode": "hybrid+rerank+graph", "k": 5}}` | Named setups the API and UI offer per question; the two frozen on dev (README, "Frozen setups"). |
| `CODEATLAS_DEFAULT_SETUP` | `"fast"` | Setup the UI selects by default. |
| `CODEATLAS_MIN_VECTOR_SIMILARITY` | `0.30` | Refusal threshold on vector similarity (no reranker) for embedders without a per-model value. |
| `CODEATLAS_MIN_RERANK_CONFIDENCE` | `0.05` | Refusal threshold on the reranker score for rerankers without a per-model value. |
| `CODEATLAS_MIN_CONFIDENCE_BY_MODEL` | `{"local:BAAI/bge-small-en-v1.5": 0.6177, "Alibaba-NLP/gte-reranker-modernbert-base": 0.868}` | Per-model refusal thresholds, as JSON {"<embedder name or reranker model>": <threshold>}; they override the two above for that model. The defaults were calibrated on the dev split for the two frozen setups (`codeatlas eval --calibrate`). |

## API, webhook and tracing

| Variable | Default | Meaning |
|---|---|---|
| `CODEATLAS_CORS_ORIGINS` | `["http://localhost:5173"]` | Browser origins allowed by CORS (JSON list). The Vite dev server's origin by default. |
| `CODEATLAS_UI_DIR` | unset | Directory with the built web UI (ui/dist). When set, the API serves it at / (one container, one origin). |
| `CODEATLAS_SSE_HEARTBEAT_S` | `5.0` | Seconds between `progress` events on /api/ask/stream while nothing else is sent. Keeps proxies with a first-byte or idle timeout (CloudFront: 60 s) from cutting off slow (quality-setup) retrievals. |
| `CODEATLAS_API_TOKEN` | unset | Bearer token required on every /api/* route when set (the GitHub webhook uses its HMAC instead). |
| `CODEATLAS_ALLOW_UNAUTHENTICATED` | `False` | `codeatlas serve` refuses a non-loopback host without api_token unless this is set, e.g. in a container whose published port is bound to 127.0.0.1 on the host. |
| `CODEATLAS_ALLOW_INDEX_ANY_PATH` | `False` | POST /api/index may only re-index the configured repository unless this is set. |
| `CODEATLAS_WEBHOOK_SECRET` | unset | Secret for verifying GitHub webhook signatures (X-Hub-Signature-256). Unset disables the webhook. |
| `CODEATLAS_WEBHOOK_BRANCH` | unset | Only pushes to this branch trigger a re-index. Unset: every branch. |
| `CODEATLAS_GIT_PULL_ON_WEBHOOK` | `False` | Run `git pull` in repo_path before a webhook-triggered re-index. |
| `CODEATLAS_TRACE_ENABLED` | `True` | Write per-query traces to index_dir/traces.jsonl. |
