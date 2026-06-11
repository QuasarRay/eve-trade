# fix-sqlx-pgconnection-lines.ps1
# Run from:
# C:\Users\Astral\Desktop\eve-trade\distributed-backend\src\trade-settlement

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$targets = @(
    @{ File = ".\src\db\orders.rs";      Lines = @(294, 351, 406, 550) },
    @{ File = ".\src\db\settlements.rs"; Lines = @(378, 415, 451, 474, 480, 486, 553) }
)

$old = "&mut **tx"
$new = "&mut *tx"

foreach ($target in $targets) {
    $file = $target.File

    if (-not (Test-Path $file)) {
        throw "File not found: $file"
    }

    $lines = [System.Collections.Generic.List[string]]::new()
    [System.IO.File]::ReadAllLines((Resolve-Path $file)) | ForEach-Object {
        $lines.Add($_)
    }

    foreach ($lineNumber in $target.Lines) {
        $index = $lineNumber - 1

        if ($index -lt 0 -or $index -ge $lines.Count) {
            throw "$file does not have line $lineNumber"
        }

        $line = $lines[$index]

        if ($line -notlike "*$old*") {
            throw @"
Refusing to edit unexpected line.

File: $file
Line: $lineNumber

Expected to find:
$old

Actual line:
$line
"@
        }

        $lines[$index] = $line.Replace($old, $new)

        Write-Host "Updated $file line $lineNumber"
    }

    [System.IO.File]::WriteAllLines((Resolve-Path $file), $lines, [System.Text.UTF8Encoding]::new($false))
}

Write-Host "Done. Now run: cargo check"