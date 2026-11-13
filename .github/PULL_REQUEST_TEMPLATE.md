## What and why

<!-- One logical change. What does it change, and why? Link the issue if there is one. -->

## How it was tested

<!-- Commands run and their results. For retrieval changes: dev-split numbers before and after. -->

## Checklist

- [ ] `ruff check .` and `pytest -q` pass
- [ ] `cd ui && npm run typecheck && npm test` pass (if the UI changed)
- [ ] Tests added or updated for the change
- [ ] Docs and `CHANGELOG.md` (`[Unreleased]`) updated if behaviour changed
- [ ] Generated files regenerated if their source changed (`docs/configuration.md`, `docs/openapi.json`)
- [ ] No eval questions, expected sources, frozen setups or thresholds changed after seeing test results
- [ ] No secrets, `.env` contents or personal data in the diff
