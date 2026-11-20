param()

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$hookDir = (Resolve-Path -LiteralPath (Join-Path $root '.githooks')).Path
$existing = & git -C $root config --local --get core.hooksPath
if ($LASTEXITCODE -gt 1) { throw 'Git-Konfiguration konnte nicht gelesen werden.' }
if ($existing -and -not [string]::Equals($existing, $hookDir,
        [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Ein anderer core.hooksPath ist bereits gesetzt. Bestehende Git-Hooks nicht überschrieben.'
}
& git -C $root config --local core.hooksPath $hookDir
if ($LASTEXITCODE -ne 0) { throw 'Pre-Push-Hook konnte nicht aktiviert werden.' }
Write-Output 'Jarvis-Pre-Push-Prüfung ist für diesen Checkout und seine Git-Worktrees aktiv.'
