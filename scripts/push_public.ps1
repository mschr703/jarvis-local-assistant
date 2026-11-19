param(
    [string]$Remote = 'origin',
    [string]$Refspec = 'HEAD:main',
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
if (-not $Refspec.StartsWith('HEAD:')) {
    throw 'Nur ein HEAD:<Zielbranch>-Refspec ist erlaubt; geprueft wird die HEAD-Historie.'
}
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Push-Location -LiteralPath $root
try {
    & python scripts/audit_public.py
    if ($LASTEXITCODE -ne 0) { throw 'Oeffentlicher Datei-Scan fehlgeschlagen. Kein Push.' }
    & git diff --check
    if ($LASTEXITCODE -ne 0) { throw 'Arbeitsbaum enthaelt fehlerhafte Whitespaces. Kein Push.' }
    & git diff --cached --check
    if ($LASTEXITCODE -ne 0) { throw 'Index enthaelt fehlerhafte Whitespaces. Kein Push.' }
    if ($CheckOnly) {
        Write-Output 'Oeffentliche Push-Pruefung bestanden; CheckOnly hat nichts gepusht.'
        return
    }
    $changes = & git status --porcelain
    if ($LASTEXITCODE -ne 0) { throw 'Git-Status konnte nicht gelesen werden. Kein Push.' }
    if ($changes) { throw 'Arbeitsbaum ist nicht sauber. Aenderungen zuerst committen. Kein Push.' }
    & git push $Remote $Refspec
    if ($LASTEXITCODE -ne 0) { throw 'Git-Push fehlgeschlagen.' }
}
finally {
    Pop-Location
}
