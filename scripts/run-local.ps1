[CmdletBinding()]
param(
    [string]$DatabaseUrl = "postgres://postgres:postgres@localhost:5432/eve_trade",
    [string]$TradeSettlementTarget = "127.0.0.1:9092",
    [string]$UDPSecret = "local-game-edge-secret"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if (-not (Get-Command encore -ErrorAction SilentlyContinue)) {
    throw "Encore CLI was not found. Install it with: iwr https://encore.dev/install.ps1 | iex"
}

$env:DATABASE_URL = $DatabaseUrl
$env:TRADE_SETTLEMENT_GRPC_TARGET = $TradeSettlementTarget
$env:API_GATEWAY_QUILKIN_UDP_ENABLED = "true"
$env:API_GATEWAY_QUILKIN_UDP_ADDR = ":26000"
$env:API_GATEWAY_UDP_AUTH_REQUIRED = "true"
$env:API_GATEWAY_UDP_HMAC_SECRET = $UDPSecret
$env:API_GATEWAY_UDP_HMAC_KEY_ID = "primary"
$env:API_GATEWAY_UDP_PRINCIPAL_KEYS_JSON = '{"seller":{"capsuleer_id":1001,"secret":"seller-player-secret"},"buyer":{"capsuleer_id":2002,"secret":"buyer-player-secret"},"other":{"capsuleer_id":3003,"secret":"other-player-secret"}}'

Write-Host "Starting Encore Go backend with encore run..."
Write-Host "Encore HTTP:              http://localhost:4000"
Write-Host "Quilkin UDP adapter:      udp://localhost:26000"
Write-Host "PostgreSQL DATABASE_URL:  $DatabaseUrl"
Write-Host "Rust settlement gRPC:     $TradeSettlementTarget"
Write-Host "Pub/Sub backend:          Encore local runtime"
Write-Host ""

encore run
exit $LASTEXITCODE
