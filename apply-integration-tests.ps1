$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot ".")
$Runner = Join-Path $RepoRoot "distributed-backend\scripts\run-e2e.ps1"

if (-not (Test-Path -LiteralPath $Runner)) {
    throw "Missing e2e runner at $Runner"
}

Write-Host "The legacy Go integration-test generator has been retired."
Write-Host "Running the current Python e2e harness via $Runner"

& $Runner
