# Deploying CodeAtlas to Azure

`infra/azure` (Bicep) runs the API and the web UI as one container on **Azure Container Apps**, with HTTPS
ingress on the app's own `*.azurecontainerapps.io` address. Answers come from an **existing Azure OpenAI
deployment**, called with a managed identity, so no API key exists anywhere in the deployment. The image is
the same `deploy` target as on AWS: the PetClinic index and both models are baked in.

> **Status: validated, not deployed.** The template compiles and passes the Bicep linter in CI (the
> `infra-azure` job), with every linter rule raised to an error. It has never been deployed to a
> subscription, so the commands below have not been run end to end. Treat the first deploy as a test.

```
browser ──HTTPS──▶ Container Apps ingress (*.azurecontainerapps.io) ──▶ replica :8000 (0–1 replicas)
                                                                          │ user-assigned managed identity
                              Key Vault secret (API token) ◀── secret ref ┤
                              Azure Container Registry (AcrPull) ◀─ image ┤
                              Azure OpenAI (OpenAI User role) ◀─ Entra ID ┤
                              Log Analytics (stdout/stderr) ◀─────────────┘
```

**Why Bicep rather than Terraform `azurerm`.** Bicep is Azure's own language. It needs no state file or
backend, because the resource group is the state. New resource types and API versions are available
in it on the day they ship, and `az` runs it with nothing else installed. The AWS stack stays in
Terraform.

**Why Container Apps.** It runs a container from a registry with HTTPS ingress and no load balancer, VNet or
certificate to manage. It can also scale to zero, so an idle demo costs (almost) nothing for compute.

## What it creates

| Resource | Notes |
|---|---|
| Log Analytics workspace | Container logs; 30-day retention, **1 GB/day ingestion cap** |
| User-assigned managed identity | The app's only identity: pulls the image, reads the token, calls Azure OpenAI |
| Azure Container Registry (Basic) | Admin user disabled. The identity has **AcrPull**; you push with `az acr login` (Entra ID) |
| Key Vault (RBAC mode) | Holds the CodeAtlas API token. The identity has **Key Vault Secrets User** on that one secret, not on the vault |
| Role assignment on your Azure OpenAI resource | **Cognitive Services OpenAI User** for the identity: call deployments, nothing else. A module, so the resource can be in another resource group |
| Container Apps environment (Consumption profile) + Container App | 2 vCPU / 4 GiB, 0–1 replicas, HTTPS-only external ingress on port 8000, `/health` startup and liveness probes, `fast` setup by default |
| Consumption budget | $10/month for the resource group; email at 80% actual and 100% forecast spend |

The container gets `CODEATLAS_LLM_PROVIDER=azure_openai`, the endpoint (read from your Azure OpenAI
resource), the deployment name, `CODEATLAS_AZURE_OPENAI_AUTH=entra` and `AZURE_CLIENT_ID` (so
`DefaultAzureCredential` uses this identity). The API token reaches it as a Key Vault secret reference
(`CODEATLAS_API_TOKEN`). The token is a `@secure()` parameter, so it is not shown in the deployment history.
[`main.bicepparam`](../infra/azure/main.bicepparam) reads it from an environment variable, so it is never in
a file.

## Prerequisites

- An Azure subscription, and **Owner** (or Contributor plus Role Based Access Control Administrator) on the
  resource group you deploy to **and** on the Azure OpenAI resource. The template creates role assignments.
- Azure CLI 2.60 or newer with Bicep (`az bicep install`), Docker, and Git.
- An **Azure OpenAI** (or Azure AI Services) resource with a chat model deployment, or quota to create one
  (step 2).
- The resource providers registered once per subscription:

  ```bash
  for ns in Microsoft.App Microsoft.OperationalInsights Microsoft.ContainerRegistry Microsoft.KeyVault \
            Microsoft.ManagedIdentity Microsoft.CognitiveServices Microsoft.Consumption; do
    az provider register --namespace "$ns" --wait
  done
  ```

The commands below are for bash (macOS/Linux, WSL, or Git Bash on Windows), run from the repository root.
PowerShell users: set variables with `$env:NAME = "value"` instead of `export`, and see the token step
for a PowerShell way to generate one.

## Deploy

**1. Sign in and choose the subscription.**

```bash
az login
az account set --subscription "<subscription name>"
az account show --query name -o tsv          # check you are in the right one
```

**2. An Azure OpenAI deployment.** Use an existing one, or create one.

```bash
export AZURE_OPENAI_RG=<resource group of the Azure OpenAI resource>
export AZURE_OPENAI_NAME=<Azure OpenAI resource name>
export AZURE_OPENAI_DEPLOYMENT=<deployment name>

# Existing: list the deployments in the resource
az cognitiveservices account deployment list -g "$AZURE_OPENAI_RG" -n "$AZURE_OPENAI_NAME" -o table
```

To create one, pick a model and version your region offers, then deploy it:

```bash
az group create -n "$AZURE_OPENAI_RG" -l <region>
az cognitiveservices account create -g "$AZURE_OPENAI_RG" -n "$AZURE_OPENAI_NAME" -l <region> \
  --kind OpenAI --sku S0 --custom-domain "$AZURE_OPENAI_NAME"
az cognitiveservices account list-models -g "$AZURE_OPENAI_RG" -n "$AZURE_OPENAI_NAME" \
  --query "[].{model:name, version:version}" -o table
az cognitiveservices account deployment create -g "$AZURE_OPENAI_RG" -n "$AZURE_OPENAI_NAME" \
  --deployment-name "$AZURE_OPENAI_DEPLOYMENT" --model-format OpenAI \
  --model-name <model> --model-version <version> --sku-name GlobalStandard --sku-capacity 10
```

`--sku-capacity` is the rate limit in thousands of tokens per minute. 10 is plenty for a demo, and Azure
OpenAI bills per token, not per unit of capacity.

Optionally, check the deployment from your machine before deploying the app. This uses your `az login`
through Entra ID and makes **one 1-token call, which is billed**. Your user needs the same role as the app:

```bash
OPENAI_ID=$(az cognitiveservices account show -g "$AZURE_OPENAI_RG" -n "$AZURE_OPENAI_NAME" --query id -o tsv)
az role assignment create --assignee "$(az ad signed-in-user show --query id -o tsv)" \
  --role "Cognitive Services OpenAI User" --scope "$OPENAI_ID"
export CODEATLAS_LLM_PROVIDER=azure_openai CODEATLAS_AZURE_OPENAI_DEPLOYMENT="$AZURE_OPENAI_DEPLOYMENT"
export CODEATLAS_AZURE_OPENAI_ENDPOINT=$(az cognitiveservices account show -g "$AZURE_OPENAI_RG" \
  -n "$AZURE_OPENAI_NAME" --query properties.endpoint -o tsv)
codeatlas doctor --llm --skip-models          # ONE 1-token call; prints auth=entra id
```

A new role assignment can take a few minutes to apply. Until it does, the call fails with
`PermissionDeniedError (access)`.

**3. The API token, the budget email and the resource group.** Keep a copy of the token: you enter it in the
UI's Settings.

```bash
export RG=codeatlas-rg LOCATION=<region>      # e.g. the Azure OpenAI resource's region
export CODEATLAS_API_TOKEN=$(openssl rand -hex 32)
export BUDGET_ALERT_EMAIL=<your email>
az group create -n "$RG" -l "$LOCATION"
echo "$CODEATLAS_API_TOKEN"
```
```powershell
$bytes = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$env:CODEATLAS_API_TOKEN = -join ($bytes | ForEach-Object { $_.ToString("x2") })
$env:RG = "codeatlas-rg"; $env:LOCATION = "<region>"; $env:BUDGET_ALERT_EMAIL = "<your email>"
az group create -n $env:RG -l $env:LOCATION
```

**4. Deploy everything except the app** (there is no image yet). The billable clock starts here, with the
registry.

```bash
export DEPLOY_APP=false
az deployment group create -g "$RG" -n codeatlas -p infra/azure/main.bicepparam
```

**5. Build the deploy image and push it** (tag it with the commit).

```bash
REGISTRY=$(az deployment group show -g "$RG" -n codeatlas --query properties.outputs.registryLoginServer.value -o tsv)
export IMAGE_TAG=$(git rev-parse --short HEAD)
az acr login -n "${REGISTRY%%.*}"
docker build --target deploy --platform linux/amd64 -t "$REGISTRY/codeatlas:$IMAGE_TAG" .
docker push "$REGISTRY/codeatlas:$IMAGE_TAG"
```

The image is about 2.5 GB, so the push takes a while on a home connection.

**6. Deploy the app.**

```bash
export DEPLOY_APP=true MIN_REPLICAS=1         # 1 keeps a replica warm during the demo; 0 scales to zero
az deployment group create -g "$RG" -n codeatlas -p infra/azure/main.bicepparam
URL=$(az deployment group show -g "$RG" -n codeatlas --query properties.outputs.url.value -o tsv)
echo "$URL"
```

The first start takes a few minutes: pulling the image, then loading the embedding model. Open the URL,
then open **Settings** and enter the token. The default setup is `fast`.

## Smoke test

```bash
curl -s "$URL/health"                                                   # {"status":"ok",...}
curl -s -o /dev/null -w '%{http_code}\n' "$URL/api/config"              # 401: the token is required
AUTH="Authorization: Bearer $CODEATLAS_API_TOKEN"
curl -s -H "$AUTH" "$URL/api/stats" | python -c "import json,sys; print(json.load(sys.stdin)['chunks'])"  # 327
curl -s -X POST "$URL/api/ask" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"question": "How does searching for owners by last name work?", "setup": "fast"}' \
  | python -c "import json,sys; a=json.load(sys.stdin); print(a['answer']); print(len(a['sources']), 'sources')"
```

Then, in the UI, ask one `fast` and one `quality` question. Open a citation, and ask an unanswerable
question (for example about Kafka) to see the "don't know" card.

## Logs

```bash
az containerapp logs show -g "$RG" -n codeatlas --follow --tail 50          # the app's stdout/stderr
az containerapp logs show -g "$RG" -n codeatlas --type system --tail 50     # image pulls, probes, scaling
az containerapp revision list -g "$RG" -n codeatlas -o table                # revision health and replicas
```

**A revision that never becomes healthy** usually means one of three things:
- **The image pull failed.** Check the AcrPull role and the tag.
- **The Key Vault reference could not be read.** Role assignments can take a few minutes to apply on
  the first deploy. Re-run step 6.
- **The startup probe gave up while the models loaded.**

The `--type system` logs say which.

## Long requests and the ingress

Container Apps ingress is an Envoy proxy. Microsoft documents a **240-second request timeout** for HTTP
ingress. This project has not measured whether it counts the whole response or only idle time, so verify
it in the [Container Apps ingress documentation](https://learn.microsoft.com/azure/container-apps/ingress-overview).
The SSE stream (`/api/ask/stream`) sends a `progress` event at once and every 5 s
(`CODEATLAS_SSE_HEARTBEAT_S`) until the answer starts. That covers any first-byte or idle limit, as it
does for CloudFront's 60 s on AWS, but it cannot extend a limit on the total request time. Both setups
should finish well inside 240 s: `fast` in seconds, and `quality` in about 30 s on a laptop CPU. On 2 vCPU
expect quality to be somewhat slower; check it during the smoke test.

**Scale to zero and cold starts.** With `MIN_REPLICAS=0` (the template's default), the app scales to zero
after a few idle minutes. The next request starts a replica: it pulls the image (if the node does not have
it cached) and loads the models, which can take a minute or more. That first request waits, or times out.
Use `MIN_REPLICAS=1` for a demo, and 0 when nobody is using it.

## Demo session: deploy → smoke test → record → delete

1. **Deploy:** steps 1–6, with `MIN_REPLICAS=1`. Allow 30–45 minutes, mostly for the image build and push.
2. **Smoke test:** the section above.
3. **Record:** screenshots or a screen recording of a `fast` answer with a citation open, a `quality`
   question with its progress bar, and the "don't know" card.
4. **Delete:** the [teardown](#teardown) commands, then check that the resource group is gone.

## Estimated cost

Prices are pay-as-you-go list prices as best known when this was written. **Verify every one on the
[Azure pricing calculator](https://azure.microsoft.com/pricing/calculator/) and the
[Container Apps pricing page](https://azure.microsoft.com/pricing/details/container-apps/) before relying
on them.** They vary by region and currency.

| Item | Price used (verify on the Azure pricing page) |
|---|---|
| Container Apps, Consumption, active | $0.000024 per vCPU-second, $0.000003 per GiB-second |
| Container Apps, Consumption, idle | $0.000003 per vCPU-second, $0.000003 per GiB-second |
| Container Apps free grant, per subscription per month | 180,000 vCPU-seconds, 360,000 GiB-seconds, 2 million requests |
| Container Registry Basic | about $0.167 per day (≈ $5/month), billed per day |
| Log Analytics | about $2.30 per GB ingested; the first 5 GB per month are free per billing account |
| Key Vault Standard | $0.03 per 10,000 operations |
| Managed identity, budgets | free |

**A 3-hour demo** (2 vCPU / 4 GiB, one replica kept warm, counted as active the whole time, which is the
upper bound):

| Item | Basis | ≈ USD |
|---|---|---|
| Container Apps compute | 21,600 vCPU-s × $0.000024 + 43,200 GiB-s × $0.000003. **$0 if the monthly free grant is unused** | 0.00–0.65 |
| Container Registry | one day | 0.17 |
| Log Analytics, Key Vault | a few MB, a few operations | ≈ 0 |
| **Infrastructure** | | **≈ 0.20–0.85** |
| Azure OpenAI | about 2.5k input + 0.5k output tokens per `fast` answer. At an example $2.50 input / $10 output per million tokens, that is ≈ $0.01 per question. **Use your model's price** from the [Azure OpenAI pricing page](https://azure.microsoft.com/pricing/details/cognitive-services/openai-service/) | usage |

**Running 24/7** (730 hours a month):

| Configuration | Basis | ≈ USD / month |
|---|---|---|
| `MIN_REPLICAS=0`, light use (about 1 h of activity a day) | compute almost entirely inside the free grant (≈ $1) + registry $5 + logs | **≈ 6–7** |
| `MIN_REPLICAS=1`, mostly idle | 5.08M vCPU-s × $0.000003 + 10.15M GiB-s × $0.000003 after the free grant ≈ $46, + registry $5 + logs | **≈ 52** |
| `MIN_REPLICAS=1`, busy all the time | vCPU at the active rate ≈ $122 + memory ≈ $30, + registry $5 + logs | **≈ 158** |

Azure OpenAI tokens come on top, per question. **With a warm replica, 24/7 is well above the $10 budget.**
The stack is meant for a demo followed by teardown. Scaled to zero it stays under the budget, mostly
because of the registry. The budget covers this resource group only: Azure OpenAI charges land in the
Azure OpenAI resource's resource group.

## Teardown

```bash
KV=$(az deployment group show -g "$RG" -n codeatlas --query properties.outputs.keyVaultName.value -o tsv)
PRINCIPAL=$(az deployment group show -g "$RG" -n codeatlas --query properties.outputs.identityPrincipalId.value -o tsv)
OPENAI_ID=$(az cognitiveservices account show -g "$AZURE_OPENAI_RG" -n "$AZURE_OPENAI_NAME" --query id -o tsv)

# The identity's role on the Azure OpenAI resource lives outside this resource group
az role assignment delete --assignee "$PRINCIPAL" --scope "$OPENAI_ID"

az group delete -n "$RG" --yes                 # everything else: app, registry and images, vault, logs, budget
az keyvault purge -n "$KV"                      # the vault is soft-deleted for 7 days; purge it now
az group exists -n "$RG"                        # false
```

If you created the Azure OpenAI resource only for this, delete its resource group too
(`az group delete -n "$AZURE_OPENAI_RG" --yes`) and purge the soft-deleted resource with
`az cognitiveservices account purge -g "$AZURE_OPENAI_RG" -n "$AZURE_OPENAI_NAME" -l <region>`.
Afterwards, check **Cost Management → Budgets** and the resource list in the portal to confirm that
nothing is left.
