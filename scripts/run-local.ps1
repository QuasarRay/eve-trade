[CmdletBinding()]
param(
    [switch]$Detached,
    [switch]$NoBuild,
    [int]$DockerWaitSeconds = 120
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Test-Command {
    param([Parameter(Mandatory = $true)][string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-DockerDaemon {
    docker info *> $null
    return $LASTEXITCODE -eq 0
}

function Start-DockerDesktopIfAvailable {
    if (-not ($IsWindows -or $env:OS -eq "Windows_NT")) {
        return $false
    }

    $candidates = @(
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
        "${env:ProgramFiles(x86)}\Docker\Docker\Docker Desktop.exe",
        "$env:LocalAppData\Docker\Docker Desktop.exe"
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }

    if (-not $candidates) {
        return $false
    }

    Write-Host "Docker is installed but the daemon is not responding. Starting Docker Desktop..."
    Start-Process -FilePath $candidates[0] | Out-Null
    return $true
}

if (-not (Test-Command "docker")) {
    throw "Docker CLI was not found. Install Docker Desktop, then run this launcher again."
}

if (-not (Test-DockerDaemon)) {
    $started = Start-DockerDesktopIfAvailable
    if (-not $started) {
        throw "Docker is installed, but the daemon is not running. Start Docker Desktop, then run this launcher again."
    }

    $deadline = (Get-Date).AddSeconds($DockerWaitSeconds)
    do {
        Start-Sleep -Seconds 2
        if (Test-DockerDaemon) {
            break
        }
    } while ((Get-Date) -lt $deadline)

    if (-not (Test-DockerDaemon)) {
        throw "Docker Desktop did not become ready within $DockerWaitSeconds seconds."
    }
}

$composeArgs = @("compose", "up")
if (-not $NoBuild) {
    $composeArgs += "--build"
}
if ($Detached) {
    $composeArgs += "--detach"
}

Write-Host "Starting eve-trade..."
Write-Host "API Gateway:      http://localhost:8080"
Write-Host "Market service:   http://localhost:8081"
Write-Host "Trade settlement: localhost:9092"
Write-Host "PostgreSQL:        localhost:5432"
Write-Host ""

& docker @composeArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

if ($Detached) {
    Write-Host ""
    Write-Host "eve-trade is running in the background."
    Write-Host "Stop it with: .\stop-eve-trade.cmd or docker compose down"
}
