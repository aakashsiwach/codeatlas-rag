<#
.SYNOPSIS
Judged answer-quality runs on the frozen `quality` setup over the held-out TEST split. This BILLS the LLM.

.DESCRIPTION
  1. refuse unless every reference answer is reviewed (no calls)
  2. codeatlas doctor --llm                        (one 1-token call)
  3. one answer + one judge call, and the estimated cost of N passes against -Budget
  4. ask for confirmation (skip with -Yes)
  5. N judged passes; stop as soon as the actual spend exceeds -Budget
  6. print the README answer-quality table
Set $env:CODEATLAS to the command to run (default: codeatlas on PATH).

.EXAMPLE
scripts/run-judge.ps1 -Passes 3 -Budget 5
#>
[CmdletBinding()]
param(
    [int]$Passes = 3,
    [double]$Budget = 5,
    [string]$EvalSet = "eval/petclinic-test.yaml",
    [string]$Setup = "quality",
    [string]$Out = "results",
    [switch]$Yes
)
$ErrorActionPreference = "Stop"

$codeatlas = if ($env:CODEATLAS) { $env:CODEATLAS } else { "codeatlas" }
$budgetText = $Budget.ToString([System.Globalization.CultureInfo]::InvariantCulture)

function Step([string]$title) { Write-Host ""; Write-Host "== $title ==" }

# Runs codeatlas with the given arguments and stops the script with its exit code if it fails.
function Invoke-CodeAtlas {
    & $codeatlas @args
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Step "1/6 reference answers reviewed"
Invoke-CodeAtlas check-reviewed $EvalSet

Step "2/6 provider check (one 1-token call)"
Invoke-CodeAtlas doctor --llm --skip-models

Step "3/6 single judge check and cost estimate"
Invoke-CodeAtlas judge-check $EvalSet --setup $Setup --passes $Passes --budget $budgetText --final

if (-not $Yes) {
    $answer = Read-Host "Run $Passes judged pass(es) of $EvalSet with the $Setup setup (budget `$$budgetText)? [y/N]"
    if ($answer -notmatch '^(y|yes)$') { Write-Host "aborted; nothing was run"; exit 1 }
}

$runDir = Join-Path $Out ("judge-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
$reports = @()
for ($i = 1; $i -le $Passes; $i++) {
    Step "4/6 judged pass $i of $Passes"
    $passDir = Join-Path $runDir "pass-$i"
    Invoke-CodeAtlas eval $EvalSet --setup $Setup --judge --final --out $passDir
    $reports += @(Get-ChildItem -Path $passDir -Filter "eval-*.json" | ForEach-Object { $_.FullName })

    Step "5/6 spend so far"
    $spend = & $codeatlas judge-summary @reports --budget $budgetText 2>&1
    $status = $LASTEXITCODE
    if ($status -ne 0) {
        $spend | Select-Object -Last 2 | ForEach-Object { Write-Host $_ }
        Write-Host "stopping after pass $i of $Passes"
        exit $status
    }
    $spend | Select-Object -Last 1 | ForEach-Object { Write-Host $_ }
}

Step "6/6 answer quality (paste into the README; reports in $runDir)"
Invoke-CodeAtlas judge-summary @reports
