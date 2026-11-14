# Contributing to CodeAtlas

Thanks for your interest. Bug reports, evaluation questions and pull requests are welcome.

## Development setup

You need Python 3.11+, Git, and Node 24 or newer for the UI.

```bash
git clone https://github.com/aakashsiwach/codeatlas-rag
cd codeatlas-rag
python -m venv .venv
source .venv/bin/activate                     # Windows: .venv\Scripts\Activate.ps1
pip install -c requirements-lock.txt -e ".[local,openai,bedrock,dev]"
cd ui && npm ci && cd ..
```

`requirements-lock.txt` pins the versions CI and the Docker image use. Installing without `-c` also
works, with the newest compatible versions.

## Checks

Run all of these before opening a pull request. CI runs the same ones.

```bash
ruff check .                                  # lint
pytest -q                                     # Python tests: offline, no models or API keys needed
cd ui && npm run typecheck && npm test        # UI type check and unit tests (Vitest)
npx playwright install chromium && npm run e2e  # end-to-end smoke test (optional locally; CI runs it)
```

`make check` (or `./tasks.ps1 check` on Windows) runs the lint, Python tests, UI type check and UI unit
tests in one go.

The Python tests use the offline `hashing` embedder, an in-memory or temporary index, and the `fake` LLM.
They never download models or call a provider.

## Generated files

Regenerate these after changing the code they come from. Tests fail when they are stale.

| File | Source | Command |
|---|---|---|
| `docs/configuration.md` | `codeatlas/config.py` (comments above each setting) | `python scripts/gen_config_docs.py` |
| `docs/openapi.json` | the FastAPI app | `python scripts/export_openapi.py` |
| `requirements-lock.txt` | `pyproject.toml` | see below |

To regenerate the lock, resolve it on Linux with Python 3.12, like the Docker image:

```bash
docker run --rm -v "$PWD:/src:ro" -v "$PWD:/out" python:3.12-slim sh -c \
  "cp -r /src/codeatlas /src/pyproject.toml /src/README.md /src/LICENSE /tmp && cd /tmp && \
   pip install --extra-index-url https://download.pytorch.org/whl/cpu '.[local,anthropic,openai,bedrock,voyage,pdf,dev]' && \
   pip freeze --exclude codeatlas > /out/requirements-lock.new"
```

Then keep the header comment of `requirements-lock.txt` and replace the pins below it.

CI checks the lock on Python 3.11 and 3.12 (the `lock` jobs): it must install with every extra, and every
package that install pulls in must be pinned. To run the same check locally:

```bash
pip install --dry-run --quiet --report pip-report.json --extra-index-url https://download.pytorch.org/whl/cpu \
  -c requirements-lock.txt ".[all,dev]"
python scripts/check_lock.py pip-report.json
```

## Evaluation rules

The README's numbers are only worth something if nobody could have tuned them on the data they are
reported on. These rules apply to every change that touches retrieval, ranking, chunking, prompts or the
evaluation sets:

- **Tune on dev only.** Try ideas, thresholds and model choices with `eval/petclinic-dev.yaml`. Change one
  thing at a time and give each its own dev row.
- **Never edit the test split to improve numbers.** Questions, expected sources and categories in
  `eval/petclinic-test.yaml` stay as they are. The same goes for frozen setups and thresholds once test
  results exist. If a test question is genuinely wrong (for example the cited code moved), open an issue
  first and explain it. The fix gets its own commit, and the old and new numbers are both reported.
- **Run the test split only for pre-declared setups.** `codeatlas eval` refuses `*-test.yaml` without
  `--final`. Pass it only for a setup you froze on dev and wrote down before the run (in the pull request
  or an issue). Run each frozen setup on test once, and report the result even if it is worse.
- **Report results with commit hashes.** Each number comes from a report in `results/` that names the
  CodeAtlas commit, the evaluated repository revision and the models. Commit the report together with the
  README change, and say in the pull request which command produced it. See
  [docs/evaluation.md](docs/evaluation.md) for the existing runs.
- **Reference answers** (`reference_answer`, used by the LLM judge) change only to correct them, and a
  reviewer sets `reviewed: true`. The judge scripts refuse to run while any answer is unreviewed.
- **Keep the honest findings.** Results that did not go the way we hoped stay in the README.

## Your first pull request

1. **Pick something.** Issues labelled *good first issue* are small and well defined, and
   [ROADMAP.md](ROADMAP.md) lists all the open work. Comment on the issue so nobody duplicates it. For
   anything bigger than a fix, open an issue first to agree on the approach.
2. **Fork and branch.** Fork the repository on GitHub, clone your fork, and create a branch from `main`
   named after the change (`fix/constant-mappings`, `docs/ollama-setup`).
3. **Make the change** with a test, and update the docs and `CHANGELOG.md` under `[Unreleased]`.
4. **Run the checks** above locally (`make check`, or `./tasks.ps1 check` on Windows).
5. **Open a pull request** against `main` and fill in the template. CI runs the lint, the Python tests on
   3.11 and 3.12, the lock check, the UI tests and the Playwright smoke test on Node 24 and 26, and
   Terraform validation. Changes to the Dockerfile or the dependencies also build and start the Docker
   image. **CI must be green** before a pull request is reviewed and merged.
6. **Review.** Expect questions and requested changes. Push new commits to the same branch rather than
   force-pushing, so the review history stays readable.

## Commits and pull requests

- **One logical change per commit**, with a message that says what changed and why. Write the subject in
  the imperative, about 72 characters.
- **Tests:** add one with every fix or feature.
- **Docs:** update them when behaviour changes, including `CHANGELOG.md` under `[Unreleased]`.
- **Secrets:** never commit `.env`, keys, tokens or `~/.aws` content. Credentials belong in the provider
  SDKs' standard sources.
- **Security problems:** report them privately (see [SECURITY.md](SECURITY.md)), not as issues.

By contributing, you agree that your contributions are licensed under the MIT License.
