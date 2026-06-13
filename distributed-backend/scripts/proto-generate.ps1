$ErrorActionPreference = "Stop"

Set-Location (Resolve-Path (Join-Path $PSScriptRoot "..\.."))

Write-Host "Checking protobuf workspace..."
buf build

Write-Host "Linting protobuf contracts..."
buf lint

Write-Host "Generating Go and Connect protobuf code..."
buf generate

Write-Host "Done."
