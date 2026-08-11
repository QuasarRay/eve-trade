$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Candidates = @()
$Python = Get-Command python -ErrorAction SilentlyContinue
if ($Python) { $Candidates += [PSCustomObject]@{ Exe = $Python.Source; Prefix = @() } }
$Py = Get-Command py -ErrorAction SilentlyContinue
if ($Py) { $Candidates += [PSCustomObject]@{ Exe = $Py.Source; Prefix = @("-3") } }
foreach ($Candidate in $Candidates) {
    $Prefix = $Candidate.Prefix
    & $Candidate.Exe @Prefix -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
    if ($LASTEXITCODE -eq 0) {
        & $Candidate.Exe @Prefix -B (Join-Path $Here "agentctl.py") @args
        exit $LASTEXITCODE
    }
}
[Console]::Error.WriteLine("Aegis requires Python 3.11 or newer")
exit 126
