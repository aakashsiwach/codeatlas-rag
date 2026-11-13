# HTTP API

`codeatlas serve` starts a FastAPI app on `http://127.0.0.1:8000`. The machine-readable schema is
[openapi.json](openapi.json), exported by `scripts/export_openapi.py` (a test keeps it current). A running
server also serves interactive docs at `/docs`.

## Authentication

- **With `CODEATLAS_API_TOKEN` set**, every `/api/*` route requires `Authorization: Bearer <token>`. A
  missing or wrong token gets `401 {"detail": "Missing or invalid API token"}`. The comparison runs in
  constant time.
- **Exceptions:**
  - `POST /api/webhook/github` authenticates with its HMAC signature instead.
  - `/health` and the static UI (when `CODEATLAS_UI_DIR` is set) are public.
- **Without a token**, `codeatlas serve` only listens on loopback. A public `--host` is refused unless
  `CODEATLAS_ALLOW_UNAUTHENTICATED=true` is set, for example in a container whose port is published on
  `127.0.0.1` only.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness: `{"status": "ok", "version": "…"}` |
| `GET` | `/api/config` | The active setup, for the UI: repository, named setups, default mode and k, embedder, chunker, reranker, LLM (`provider:model`), refusal thresholds, ranking options, whether a token is required. No secrets |
| `POST` | `/api/ask` | Answer a question (JSON response) |
| `POST` | `/api/ask/stream` | Answer a question as server-sent events |
| `GET` | `/api/impact?symbol=Owner&depth=3` | Dependents of a class or method from the code graph (`depth` 1–6), plus a text report. 404 if the symbol is unknown |
| `GET` | `/api/file?path=…&start=&end=` | Lines of an **indexed** file, for the citation viewer. Paths outside the repository, and files that were not indexed (`.env`, `.git/config`…), are 404. `end < start` is 400 |
| `POST` | `/api/index` | `{"repo_path"?, "force"?}`: start an index run in the background (202). Only the configured repository, unless `CODEATLAS_ALLOW_INDEX_ANY_PATH=true` (otherwise 403) |
| `GET` | `/api/index/runs` | Index run history: files changed, chunks, tokens, cost, duration |
| `GET` | `/api/stats` | Index and graph statistics |
| `POST` | `/api/webhook/github` | GitHub push webhook. Verifies `X-Hub-Signature-256` against `CODEATLAS_WEBHOOK_SECRET` (503 when unset, 401 when wrong), then re-indexes only the files the push touched |

### Asking a question

The request body:

```json
{"question": "How does searching for owners by last name work?", "setup": "fast"}
```

- **`question`:** required, 1–4000 characters.
- **`setup`:** a named setup (`fast` or `quality`, from `CODEATLAS_SETUPS`). It sets the mode and k together.
- **`mode` and `k` (1–30):** an explicit alternative to `setup`, for example
  `{"question": "…", "mode": "hybrid+rerank", "k": 8}`. Sending `setup` together with `mode` or `k` is
  a 400, and so is an unknown setup or mode.
- **Neither:** the server defaults (`CODEATLAS_DEFAULT_MODE`, `CODEATLAS_DEFAULT_K`) apply.

`POST /api/ask` returns:

| Field | Meaning |
|---|---|
| `answer` | The answer text, with `[n]` citations, or the refusal "I don't know based on the indexed code." |
| `citations` | The cited blocks: index, path, lines, symbol |
| `sources` | Every retrieved block, numbered as in the prompt |
| `confidence`, `confidence_source` | Retrieval confidence and where it came from (`vector` or `rerank`) |
| `refused` | True when the answer is a refusal (below the threshold, or the LLM said it does not know) |
| `invalid_citations` | Cited numbers that were not among the retrieved blocks |
| `uncited_sentences` | Sentences without a citation |
| `truncated`, `stop_reason` | Whether the model hit the output limit, and its stop reason |
| `usage` | Input and output tokens, and `cost_usd` (`null` when the model has no price) |
| `setup`, `config`, `timings_ms` | The setup used (`null` for mode/k requests), the resolved retrieval config, and `retrieval_ms` / `total_ms` |
| `trace_id` | The query's trace in `traces.jsonl` |

LLM errors map to `503` when they are throttling, unavailable, timeout or network errors, and to `502`
otherwise. The body carries `kind`, `error_class` and `provider`.

### Server-sent events (`/api/ask/stream`)

The response is `text/event-stream`, with `Cache-Control: no-cache` and `X-Accel-Buffering: no`. Events
arrive in this order:

| Event | Data | When |
|---|---|---|
| `progress` | `{"stage": "retrieving" \| "generating", "elapsed_ms": n}` | At once, then every `CODEATLAS_SSE_HEARTBEAT_S` (5 s) while nothing else is sent. Keeps proxies with a first-byte timeout (CloudFront: 60 s) from cutting off slow retrievals |
| `sources` | `{"sources": […], "confidence", "confidence_source", "retrieval_ms", "setup"}` | Once retrieval is done |
| `token` | A text fragment (JSON string) | While the answer streams; none for a refusal |
| `done` | The same object as `POST /api/ask` | At the end |
| `error` | `{"detail", "kind"?, "error_class"?}` | Instead of `done`, if generation fails (`kind: "internal"` for unexpected errors) |

Example:

```bash
curl -N -X POST http://127.0.0.1:8000/api/ask/stream -H "Content-Type: application/json" \
  -d '{"question": "What breaks if I change the Owner entity?", "setup": "quality"}'
```

```
event: progress
data: {"stage": "retrieving", "elapsed_ms": 3}

event: progress
data: {"stage": "retrieving", "elapsed_ms": 5005}

event: sources
data: {"sources": [...], "confidence": 0.93, "confidence_source": "rerank", "retrieval_ms": 18169.2, "setup": "quality"}

event: token
data: "Changing "
...
event: done
data: {"answer": "...", "setup": "quality", "config": "hybrid+rerank+graph", "timings_ms": {...}, ...}
```
