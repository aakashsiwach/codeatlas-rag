# CodeAtlas v1.2.0

**Azure support**, mirroring the AWS work: an Azure OpenAI provider and infrastructure for Azure Container
Apps. Retrieval, the evaluation sets, the frozen setups and the README's numbers are unchanged from v1.1.0.

## Highlights

- **Azure OpenAI provider** (`CODEATLAS_LLM_PROVIDER=azure_openai`):
  - **Sign-in:** Microsoft Entra ID first, through a managed identity in Azure or `az login` locally. The
    `AZURE_OPENAI_API_KEY` environment variable is a fallback, used only when no token can be obtained.
  - **Streaming and retries:** answers stream, and throttling (429) is retried with backoff.
  - **Content filter:** a prompt or answer blocked by Azure's content filter fails with the error kind
    `content_filter`, which names the filtered categories but not the prompt.
  - **Prices** are keyed `azure_openai:<deployment>`.
  - **`codeatlas doctor --llm`** reports which auth it used.

  Quickstart: [Real answers with Azure OpenAI](../README.md#real-answers-with-azure-openai).
- **Azure Container Apps infrastructure** in Bicep ([`infra/azure`](../infra/azure)):
  - the same Docker image, with the `fast` setup by default
  - HTTPS ingress, and scale to zero
  - a user-assigned managed identity: no API key or registry password exists in the deployment
  - the API token in Key Vault
  - Log Analytics with a daily cap
  - a $10 budget alert

  Guide: [docs/deploy-azure.md](deploy-azure.md), with a demo session, cost estimates and teardown.
- **CI:**
  - a new `infra-azure` job builds and lints the Bicep with a pinned Bicep CLI, and fails on any linter
    finding
  - the Python test job installs the `azure` extra, so the provider's 30 tests run against the real
    `openai` SDK client over a mocked HTTP transport

## Upgrading

- `pip install -e ".[azure]"` (or `".[all]"`) for the Azure provider. The Docker image includes it.
- Nothing changes for existing providers. One difference: provider errors from the `openai` SDK now show
  the API's own error message instead of the full response dump.
- `requirements-lock.txt` adds pins for `azure-identity`, `azure-core`, `msal`, `msal-extensions`,
  `cryptography`, `cffi`, `PyJWT` and `pycparser`. Existing pins are unchanged.

## Validated, not deployed

- **Azure:** the Bicep template compiles and passes the linter in CI, but it has **never been deployed**.
  The Azure OpenAI provider is tested against mocked HTTP responses only and has **not called a live Azure
  OpenAI deployment**. A live deploy session is [roadmap item 11](../ROADMAP.md#11-a-live-azure-deploy-session).
- **AWS:** as in v1.1.0, the Terraform is validated in CI but has never been applied.
- **Answer-quality evaluation** (LLM-judged) has still not been run; see roadmap item 1.

Full list of changes: [CHANGELOG.md](../CHANGELOG.md#120---2026-11-20).
