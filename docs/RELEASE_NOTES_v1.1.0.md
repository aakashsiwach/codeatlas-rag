# CodeAtlas v1.1.0

The final planned release. CodeAtlas is **feature-complete** and **maintained on a best-effort basis**, and
contributions are welcome. Retrieval, the evaluation sets, the frozen setups and the README's numbers are
unchanged from v1.0.0.

## Highlights

- **Node 24 or newer** for the web UI. The UI toolchain moves to Vite 8, Vitest 5 and
  `@vitejs/plugin-react` 6, and `npm audit` reports 0 vulnerabilities.
- **The Docker image works with a plain `docker run`.** It now defaults to `/data/index` and declares `/data`
  as a volume owned by the non-root user. Before, it exited with "Permission denied" unless
  `CODEATLAS_INDEX_DIR` was set. The image runs Python 3.12.
- **New CI checks:**
  - `requirements-lock.txt` must install with every extra on Python 3.11 and 3.12, with every package
    pinned. This catches a dependency bump that conflicts with torch, botocore or langsmith.
  - The Docker image is built and started on every change to its inputs, and weekly.
  - The Playwright smoke test runs on Node 24 and 26.
- **Dependabot** runs monthly with grouped minor and patch updates, and ignores updates that cannot be used
  yet.
- **For contributors:**
  - a Project status section in the README
  - [ROADMAP.md](../ROADMAP.md), with good-first-issue, help-wanted and advanced items
  - the evaluation rules and a first-pull-request guide in [CONTRIBUTING.md](../CONTRIBUTING.md)

## Upgrading

- Use Node 24 or newer to build the UI (`ui/package.json` `engines`).
- Docker: `docker run -e CODEATLAS_API_TOKEN=… -p 8000:8000 <image>` now works without extra settings. Mount
  a volume at `/data` (`-v codeatlas-data:/data`) to keep the index between containers. Docker compose
  setups need no change.
- Python 3.11 and 3.12 are both still supported.

## Not run

- **Answer-quality evaluation** (LLM-judged faithfulness, citation precision, refusal accuracy). The 64
  reference answers have not been reviewed, and no judged run was made. The harness
  (`scripts/run-judge.ps1` / `.sh`) is ready for anyone with model access; see roadmap item 1.
- **A live AWS deployment.** The Terraform for ECS Fargate behind CloudFront is validated in CI but has
  never been applied. [docs/deploy.md](deploy.md) describes a short demo session and its teardown.

Full list of changes: [CHANGELOG.md](../CHANGELOG.md#110---2026-11-14).
