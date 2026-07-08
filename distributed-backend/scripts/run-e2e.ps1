$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python was not found."
}

Write-Host "Running e2e tests against the configured Encore/Rust/PostgreSQL environment..."
python -m pytest distributed-backend/tests/e2e -q
exit $LASTEXITCODE
