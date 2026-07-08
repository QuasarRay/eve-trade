[CmdletBinding()]
param()

Write-Host "The Encore backend runs in the foreground. Stop it with Ctrl+C in the terminal running scripts/run-local.ps1."
Write-Host "Non-Go dependencies such as PostgreSQL, NSQ, Quilkin, or Rust trade-settlement must be stopped with the tool that started them."
