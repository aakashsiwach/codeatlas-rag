<#
.SYNOPSIS
Common tasks on Windows, the same as the Makefile. Run with the virtualenv active (.venv\Scripts\Activate.ps1).

.EXAMPLE
./tasks.ps1 check
#>
param([ValidateSet("help", "install", "lint", "test", "ui-test", "e2e", "check", "demo", "docs", "docker", "eval-dev")]
      [string]$Task = "help")
$ErrorActionPreference = "Stop"

# Runs a native command and stops on a non-zero exit code.
function Invoke-Checked([scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function In-UI([scriptblock]$Command) {
    Push-Location (Join-Path $PSScriptRoot "ui")
    try { Invoke-Checked $Command } finally { Pop-Location }
}

$tasks = [ordered]@{
    "install"  = { Invoke-Checked { python -m pip install -c requirements-lock.txt -e ".[local,openai,bedrock,dev]" }; In-UI { npm ci } }
    "lint"     = { Invoke-Checked { ruff check . } }
    "test"     = { Invoke-Checked { python -m pytest -q } }
    "ui-test"  = { In-UI { npm run typecheck }; In-UI { npm test } }
    "e2e"      = { In-UI { npx playwright install chromium }; In-UI { npm run e2e } }
    "check"    = { & $tasks["lint"]; & $tasks["test"]; & $tasks["ui-test"] }
    "demo"     = { Invoke-Checked { codeatlas demo } }
    "docs"     = { Invoke-Checked { python scripts/gen_config_docs.py }; Invoke-Checked { python scripts/export_openapi.py } }
    "docker"   = { Invoke-Checked { docker build --target deploy -t codeatlas:local-deploy . } }
    "eval-dev" = { Invoke-Checked { codeatlas eval eval/petclinic-dev.yaml -k 5 --deep-k 10 --configs vector,hybrid,hybrid+graph,hybrid+rerank,hybrid+rerank+graph } }
}

if ($Task -eq "help") {
    Write-Host "Tasks: $($tasks.Keys -join ', ')"
    Write-Host "  check = lint + test + ui-test (what CI checks before a commit, except e2e)"
    Write-Host "  demo  = clone and index PetClinic if needed, serve API + UI on http://127.0.0.1:8000 (fake LLM)"
    exit 0
}
Set-Location $PSScriptRoot
& $tasks[$Task]
