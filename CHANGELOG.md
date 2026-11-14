# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- The web UI needs Node 24 or newer (`engines` in `ui/package.json`). CI tests the UI on Node 24 and 26,
  and the Docker build stage uses Node 26 (the current LTS).
- UI toolchain: Vite 6.4.6 → 8.3.7, Vitest 2 → 5.1.0, `@vitejs/plugin-react` 4.7.0 → 6.3.0 and Playwright
  1.61.1 → 1.65.0, with no config changes. `npm audit` now reports 0 vulnerabilities: the six
  advisories left in 1.0.0 were all inside Vitest 2's own dependencies (an old Vite 5 and esbuild).
- The Vitest workers run with `--no-experimental-webstorage`: Node 25+ defines its own `localStorage`
  global, which hides jsdom's in tests.
- CI runs the Playwright smoke test on Node 24 and 26 (the API on the fixture repository with the offline
  LLM), so dependency upgrades that break the dev server or the browser fail CI.

## [1.0.0] - 2026-11-14

First public release.

### Added

- **Code-aware indexing.**
  - tree-sitter parses Java into class outlines and method chunks with contextual headers: package,
    Spring stereotype, annotations, resolved HTTP endpoints and outgoing calls.
  - Heading-aware chunks for Markdown, AsciiDoc, config files and PDFs.
  - A naive fixed-size baseline chunker, for comparison.
- **Incremental indexing:** SHA-256 change detection, run history with tokens and cost, and a GitHub push
  webhook (HMAC-verified) that re-indexes only the files a push touched.
- **Retrieval:**
  - BM25 with a code-aware tokenizer, dense vectors in Qdrant, Reciprocal Rank Fusion
  - optional LLM query rewriting and a cross-encoder reranker
  - test code ranked below main code, and class outlines below their methods
- **Code graph** (NetworkX): calls resolved through field and local-variable types, interface →
  implementation dispatch, type references and endpoint callers. It answers "what breaks if I change X" and
  "who calls Y", and reserves top-k slots for graph neighbours.
- **Grounded answers:** numbered context blocks, mandatory `[n]` citations, invalid-citation detection, and
  refusal ("I don't know based on the indexed code.") below a per-model confidence threshold calibrated on
  dev.
- **Two frozen setups:** `fast` (AST chunks + vector search, the default) and `quality` (hybrid + ModernBERT
  reranker over 20 candidates + code graph). Requests choose one with `setup`.
- **LLM providers:** Anthropic API, OpenAI-compatible (including Ollama), Amazon Bedrock (Converse API,
  named AWS profiles) and an offline `fake` provider. They share typed errors, retries, timeouts, stop
  reasons, per-model thinking settings and a price table (models without a price are reported as
  "unpriced", never $0).
- **HTTP API:** FastAPI, with JSON and server-sent-event answers. The stream sends `progress` heartbeats so
  proxies never wait 60 s for a first byte. Also: impact analysis, a file viewer for indexed files only, the
  active configuration, and the OpenAPI schema in `docs/openapi.json`.
- **Web UI** (React + TypeScript, Vite):
  - streaming answers
  - citations that open a source viewer with the cited lines highlighted
  - a "don't know" card
  - a per-question `fast` / `quality` switch with server progress
  - a settings panel
- **Evaluation harness:**
  - YAML question sets with symbol-level expected sources, scored by declaration-line overlap
  - recall@k, hit@k, MRR, latency, refusal accuracy and citation precision
  - LLM-judged faithfulness against reviewed reference answers
  - per-category tables, refusal calibration and run metadata
  - a held-out test split that runs only with `--final`
- **PetClinic evaluation set:** 64 questions verified against `500158f`, split into dev (40) and test (24),
  with drafted reference answers awaiting review.
- **Judged-run tooling:** `check-reviewed`, `judge-check` (one answer and one verdict, with a cost estimate
  against a budget) and `judge-summary`, wrapped by `scripts/run-judge.ps1` / `.sh`.
- **Getting started:**
  - `codeatlas demo`: clone and index PetClinic, then serve the API and UI with the offline LLM, in one
    command
  - `codeatlas doctor [--llm]` to check the environment
  - a `Makefile` and `tasks.ps1` for common tasks
- **Deployment:**
  - a Docker image that serves the API and UI
  - a `deploy` target with the index and models baked in
  - Terraform for ECS Fargate behind an ALB and CloudFront, with an SSM API token, a least-privilege Bedrock
    task role, CloudWatch logs and a budget alert
  - `docs/deploy.md`
- **Documentation:** architecture, evaluation, configuration (generated from the settings), API,
  troubleshooting, contributing and security policy.

### Fixed

- Targeted (webhook) indexing skips hidden directories, as full scans do.
- Replayed GitHub webhook deliveries (the same `X-GitHub-Delivery`) are not re-indexed twice.
- `traces.jsonl` is rotated by size (`CODEATLAS_TRACE_MAX_BYTES`, default 10 MB).
- Eval reports record the eval set's SHA-256 and the versions of the libraries that affect results.
- Index runs report chunks longer than the embedding model reads.
- The local embedder uses the current sentence-transformers API and explains models without a fixed
  dimension.
- `doctor` accepts the offline `fake` provider without a model id, and `/api/config` names it `fake:offline`.
- UI: scroll effects no longer crash React in newer browsers, where `scrollIntoView` returns a Promise.

### Security

- API bearer token (`CODEATLAS_API_TOKEN`) with a constant-time comparison, and `codeatlas serve` refuses a
  public host without one.
- `/api/file` serves only indexed files. Indexing skips links that resolve outside the repository, and
  `/api/index` only re-indexes the configured repository.
- Repository text reaches the LLM as escaped `<block>` elements that the prompt treats as data.
- Docker Compose publishes the API on loopback only and does not publish Qdrant.
- Pinned Python dependencies (`requirements-lock.txt`) for CI and the image. Dependabot runs weekly for pip,
  npm, Actions, Docker and Terraform.

### Known limitations

- The call graph is static and name-based (no reflection, events, AOP, or beans wired only in
  configuration), and overloads share a node.
- The confidence ranges of answerable and unanswerable questions overlap, so the LLM's grounded "I don't
  know" is the second safeguard.
- On CPU, the `quality` setup takes about 20–30 s per question.
- Answer-quality (LLM-judge) results are pending the review of the reference answers.

[Unreleased]: https://github.com/aakashsiwach/codeatlas-rag/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/aakashsiwach/codeatlas-rag/releases/tag/v1.0.0
