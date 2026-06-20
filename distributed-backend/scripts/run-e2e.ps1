# This block makes script failures stop immediately instead of letting later commands run on a broken environment.
# It exists because integration tests are worthless if migration, build, or test failures are accidentally ignored.
$ErrorActionPreference = "Stop"

# This block moves execution to the repository root no matter where the script is launched from.
# It exists so all relative compose, migration, and Go package paths are deterministic.
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

function Invoke-CheckedNativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string] $FilePath,

        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]] $ArgumentList
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath exited with code $LASTEXITCODE"
    }
}

# This block destroys old integration containers and volumes before a new run.
# It exists so stale database state cannot make a broken test look correct.
Invoke-CheckedNativeCommand docker compose -f docker-compose.integration.yml --profile test down -v --remove-orphans

try {
    # This block builds and runs the full service harness including the Python e2e test container.
    # It exists so one command proves market talks to  settlement and settlement writes  PostgreSQL rows.
    Invoke-CheckedNativeCommand docker compose -f docker-compose.integration.yml --profile test up --build --abort-on-container-exit --exit-code-from e2e-tests
}
finally {
    # This block cleans up successful or failed integration containers and volumes after the run.
    # It exists so the next test starts from the same deterministic environment.
    Invoke-CheckedNativeCommand docker compose -f docker-compose.integration.yml --profile test down -v --remove-orphans
}
