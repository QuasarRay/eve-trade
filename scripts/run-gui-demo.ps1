[CmdletBinding()]
param(
    [switch]$ResetData,
    [switch]$NoBuild,
    [switch]$SkipInstall,
    [switch]$SkipOutage
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Resolve-Tool {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$BundledPath
    )
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    if (Test-Path -LiteralPath $BundledPath) {
        return $BundledPath
    }
    throw "$Name was not found. Install Node.js/pnpm or run this command from Codex Desktop with workspace dependencies available."
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is not running."
}

if ($ResetData) {
    Write-Host "ResetData removes the eve-trade local PostgreSQL and RabbitMQ volumes."
    docker compose down -v --remove-orphans
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$composeArgs = @("compose", "up", "-d")
if (-not $NoBuild) {
    $composeArgs += "--build"
}
& docker @composeArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$deadline = (Get-Date).AddMinutes(4)
do {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8000/api/gui/buttons/" -TimeoutSec 2
        if ($response.StatusCode -eq 200) { break }
    } catch {
        Start-Sleep -Seconds 2
    }
} while ((Get-Date) -lt $deadline)

if (-not $response -or $response.StatusCode -ne 200) {
    docker compose ps
    throw "The GUI simulator did not become ready within four minutes."
}

$RuntimeRoot = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies"
$Node = Resolve-Tool -Name "node" -BundledPath (Join-Path $RuntimeRoot "node\bin\node.exe")
$Pnpm = Resolve-Tool -Name "pnpm" -BundledPath (Join-Path $RuntimeRoot "bin\pnpm.cmd")

if (-not $SkipInstall) {
    & $Pnpm install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $Node ".\node_modules\playwright\cli.js" install chromium
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$env:GUI_DEMO_SKIP_OUTAGE = if ($SkipOutage) { "1" } else { "0" }
& $Node ".\scripts\gui-simulator-demo.cjs"
exit $LASTEXITCODE
