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

To regenerate the lock, resolve it on Linux with Python 3.11, like the Docker image:

```bash
docker run --rm -v "$PWD:/src:ro" -v "$PWD:/out" python:3.11-slim sh -c \
  "cp -r /src/codeatlas /src/pyproject.toml /src/README.md /src/LICENSE /tmp && cd /tmp && \
   pip install --extra-index-url https://download.pytorch.org/whl/cpu '.[local,anthropic,openai,bedrock,voyage,pdf,dev]' && \
   pip freeze --exclude codeatlas > /out/requirements-lock.new"
```

Then keep the header comment of `requirements-lock.txt` and replace the pins below it.

## Evaluation rules

The evaluation numbers in the README must stay honest and reproducible:

- Tune only on `eval/petclinic-dev.yaml`. The held-out `eval/petclinic-test.yaml` runs only with `--final`,
  for setups that were frozen on dev before the run.
- Do not change questions, expected sources, frozen setups or thresholds after seeing test results.
- Numbers in the README come from committed reports in `results/`. See [docs/evaluation.md](docs/evaluation.md).
- A new technique gets its own dev-split row first: change one thing at a time.

## Commits and pull requests

- **One logical change per commit**, with a message that says what changed and why. Write the subject in
  the imperative, about 72 characters.
- **Tests:** add one with every fix or feature.
- **Docs:** update them when behaviour changes, including `CHANGELOG.md` under `[Unreleased]`.
- **Secrets:** never commit `.env`, keys, tokens or `~/.aws` content. Credentials belong in the provider
  SDKs' standard sources.
- **Security problems:** report them privately (see [SECURITY.md](SECURITY.md)), not as issues.

By contributing, you agree that your contributions are licensed under the MIT License.
