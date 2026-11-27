# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`confidence_after_demotion` ranking option** (default off; `-o confidence_after_demotion=on`). With
  `rerank` on, the refusal confidence is normally read from the top reranked chunk before test code is
  demoted, so it can describe a chunk that is no longer first. The option reports the score of the chunk
  that is first in the final results instead. It does not touch the vector and bm25 confidence, and the
  value is the same as before whenever neither test demotion nor the class-outline reorder changes which
  chunk is first (for example with `demote_tests` off, or for a query about tests). The frozen 0.868
  threshold and the README's numbers are unchanged; making the option the default is a separate change that needs a new held-out
  split.

## [1.2.0] - 2026-11-20

Azure support, mirroring the AWS work: an Azure OpenAI provider and infrastructure for Azure Container
Apps. The template is validated in CI but has **not been deployed**, and the provider has been tested
against mocked HTTP responses only, not a live Azure OpenAI deployment. Retrieval, the evaluation sets, the
frozen setups and the README's numbers are unchanged.

### Added

- **Azure OpenAI provider** (`CODEATLAS_LLM_PROVIDER=azure_openai`), through the `openai` SDK's
  `AzureOpenAI` client:
  - settings: `CODEATLAS_AZURE_OPENAI_ENDPOINT`, `CODEATLAS_AZURE_OPENAI_DEPLOYMENT` (the deployment
    name, used instead of `CODEATLAS_LLM_MODEL`) and `CODEATLAS_AZURE_OPENAI_API_VERSION` (default
    `2024-10-21`)
  - auth: Microsoft Entra ID first (`DefaultAzureCredential`: a managed identity in Azure, `az login`
    locally), with the `AZURE_OPENAI_API_KEY` environment variable as a fallback when no token can be
    obtained. `CODEATLAS_AZURE_OPENAI_AUTH=entra` or `key` forces one
  - streaming; retries with backoff on throttling (429), honouring Azure's retry-after headers;
    `max_completion_tokens`, with truncation reported as `max_tokens`
  - a new error kind, `content_filter`, for prompts blocked or answers stopped by Azure's content
    filter. It names the filtered categories, never the prompt
  - prices keyed `azure_openai:<deployment>`
  - `codeatlas doctor` shows the endpoint host, API version and auth path, never the key. `doctor --llm`
    reports which auth was used
- New `azure` extra (`openai`, `azure-identity`), included in `all` and in the Docker image.
  `requirements-lock.txt` pins its 8 new packages.
- **Azure Container Apps infrastructure** in Bicep (`infra/azure`), resource-group scoped:
  - Log Analytics (with a daily ingestion cap)
  - Azure Container Registry, without an admin user
  - a Container Apps environment and one Container App: the existing image, the `fast` setup by default,
    HTTPS-only ingress, scale to zero allowed
  - a user-assigned managed identity with AcrPull, Key Vault Secrets User (on the one secret) and
    Cognitive Services OpenAI User (on an existing Azure OpenAI resource passed as a parameter)
  - Key Vault holding the API token, referenced as a Container App secret
  - a $10 monthly Consumption budget alert

  The token is a secure parameter read from an environment variable, so no secret is in a tracked file.
- `docs/deploy-azure.md`: the `az` commands to deploy, smoke test, read logs and tear down; how the ingress
  treats long requests; and cost estimates for a 3-hour demo and for running 24/7.
- CI: an `infra-azure` job that builds and lints the Bicep with a pinned Bicep CLI (every linter rule an
  error, any diagnostic fails). The test job installs the `azure` extra, so the 30 provider tests run
  against the real SDK over a mocked transport.

### Changed

- Provider errors from the `openai` SDK now carry the API's own error message instead of the full
  response dump.
- UI test dependencies: jsdom 25.0.1 → 31.0.0, TypeScript 5.6.3 → 5.9.3 (Dependabot).
- README: the Project status, providers, configuration and documentation tables cover Azure. The
  architecture doc covers both clouds, and the troubleshooting doc covers Azure's errors.

## [1.1.0] - 2026-11-14

The final planned release: CodeAtlas is feature-complete and maintained on a best-effort basis. This release
raises the Node requirement, makes the Docker image work with a plain `docker run`, adds CI checks for the
lock file and the image, updates dependencies, and documents the open work for contributors. Retrieval,
the evaluation sets, the frozen setups and the README's numbers are unchanged.

### Added

- **Project status** in the README. It states that answer-quality (LLM-judge) evaluation and a live
  deploy were not run, and that the judge harness and the Terraform are ready for anyone with model access.
- `ROADMAP.md` with the open work, each item labelled *good first issue*, *help wanted* or *advanced*, and
  `docs/ISSUES_TO_OPEN.md` with a ready-to-paste issue for each.
- CONTRIBUTING: the evaluation rules for contributors (tune on dev only, never edit the test split to
  improve numbers, `--final` only for pre-declared setups, results with commit hashes), and a guide to a
  first pull request.
- CI checks `requirements-lock.txt` on Python 3.11 and 3.12. A dry-run install with every extra must
  resolve, and `scripts/check_lock.py` fails on any package the install would add without a pin.
- A `docker` workflow builds the API + UI image, starts it with a plain `docker run`, and checks
  `/health`, the UI and that auth is on. It runs when the Dockerfile, `pyproject.toml`, the lock or
  `ui/package*.json` change, and weekly.
- CI runs the Playwright smoke test on Node 24 and 26 (the API on the fixture repository with the offline
  LLM), so dependency upgrades that break the dev server or the browser fail CI.

### Changed

- **The web UI needs Node 24 or newer** (`engines` in `ui/package.json`). CI tests the UI on Node 24 and
  26, and the Docker build stage uses Node 26 (the current LTS).
- UI toolchain: Vite 6.4.6 → 8.3.7, Vitest 2 → 5.1.0, `@vitejs/plugin-react` 4.7.0 → 6.3.0 and Playwright
  1.61.1 → 1.65.0, with no config changes. `npm audit` now reports 0 vulnerabilities: the six
  advisories left in 1.0.0 were all inside Vitest 2's own dependencies (an old Vite 5 and esbuild).
- The Vitest workers run with `--no-experimental-webstorage`: Node 25+ defines its own `localStorage`
  global, which hides jsdom's in tests.
- The Docker image runs Python 3.12 (was 3.11). `requirements-lock.txt` is resolved on 3.12: the same
  pins plus `setuptools`, which torch needs on Python 3.12+.
- The build needs hatchling 1.33 or newer (was 1.21). Other updates: GitHub Actions (`checkout` 7,
  `setup-python` 7, `setup-node` 7, `setup-terraform` 4) and the testing-library packages.
- Dependabot runs monthly instead of weekly, with minor and patch updates grouped per ecosystem. It ignores
  updates that cannot be used yet:
  - python-dateutil 3 and uuid-utils 1 (botocore and langsmith require older majors)
  - mpmath 1.4 (sympy, via torch, requires <1.4)
  - Python base images newer than 3.12
  - TypeScript 7 (deferred)
- Answer quality is described as "not run" instead of "pending" in the README and `docs/evaluation.md`.

### Fixed

- **The Docker image now starts with a plain `docker run`.** Its default index directory was `.codeatlas`
  under `/app`, which the non-root user cannot write, so it exited with "Permission denied" unless
  `CODEATLAS_INDEX_DIR` was set (docker compose sets it). The image now defaults to `/data/index` and
  declares `/data` as a volume owned by the app user.
- The lock-check command in CONTRIBUTING had a broken line continuation.

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

[Unreleased]: https://github.com/aakashsiwach/codeatlas-rag/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/aakashsiwach/codeatlas-rag/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/aakashsiwach/codeatlas-rag/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/aakashsiwach/codeatlas-rag/releases/tag/v1.0.0
