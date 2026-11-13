# Common tasks. Run them with the virtualenv active (source .venv/bin/activate).
# Windows: ./tasks.ps1 <task> does the same.

PYTHON ?= python
UI := ui

.PHONY: help install lint test ui-test e2e check demo docs docker eval-dev

help:  ## List the tasks
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

install:  ## Install CodeAtlas (pinned versions) and the UI dependencies
	$(PYTHON) -m pip install -c requirements-lock.txt -e ".[local,openai,bedrock,dev]"
	cd $(UI) && npm ci

lint:  ## Ruff
	ruff check .

test:  ## Python tests (offline)
	$(PYTHON) -m pytest -q

ui-test:  ## UI type check and unit tests
	cd $(UI) && npm run typecheck && npm test

e2e:  ## UI end-to-end smoke test (Playwright; offline API with the fake LLM)
	cd $(UI) && npx playwright install chromium && npm run e2e

check: lint test ui-test  ## Everything CI checks before a commit (except e2e)

demo:  ## Clone and index PetClinic if needed, then serve the API and UI on http://127.0.0.1:8000 (fake LLM)
	codeatlas demo

docs:  ## Regenerate docs/configuration.md and docs/openapi.json
	$(PYTHON) scripts/gen_config_docs.py
	$(PYTHON) scripts/export_openapi.py

docker:  ## Build the deploy image locally (PetClinic index and models baked in)
	docker build --target deploy -t codeatlas:local-deploy .

eval-dev:  ## Retrieval eval of all setups on the dev split (local models, no LLM calls)
	codeatlas eval eval/petclinic-dev.yaml -k 5 --deep-k 10 --configs vector,hybrid,hybrid+graph,hybrid+rerank,hybrid+rerank+graph
