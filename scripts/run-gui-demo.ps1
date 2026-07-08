[CmdletBinding()]
param(
    [switch]$ResetData,
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

if ($ResetData) {
    throw "ResetData is no longer implemented by this script because the Go backend is started with Encore. Reset the configured PostgreSQL database explicitly before running the demo."
}

$SimulatorUrl = $env:EVE_TRADE_SIMULATOR_URL
if (-not $SimulatorUrl) { $SimulatorUrl = "http://127.0.0.1:8000" }
$deadline = (Get-Date).AddMinutes(4)
do {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "$SimulatorUrl/api/gui/buttons/" -TimeoutSec 2
        if ($response.StatusCode -eq 200) { break }
    } catch {
        Start-Sleep -Seconds 2
    }
} while ((Get-Date) -lt $deadline)

if (-not $response -or $response.StatusCode -ne 200) {
    throw "The GUI simulator at $SimulatorUrl did not become ready within four minutes. Start the simulator and Encore backend first."
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

if ($SkipOutage) {
    $env:GUI_DEMO_ENABLE_OUTAGE = "0"
}
& $Node ".\scripts\gui-simulator-demo.cjs"
exit $LASTEXITCODE
