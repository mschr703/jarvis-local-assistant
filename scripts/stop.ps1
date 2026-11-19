$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pidFile = Join-Path $root '.jarvis-dev.json'
$expected = @{
    backend = Join-Path $root 'backend\.venv\Scripts\python.exe'
    frontend = Join-Path $root 'frontend\node_modules\vite\bin\vite.js'
}
$ports = @{ backend = 8765; frontend = 5173 }
foreach ($name in @('backend', 'frontend')) {
    $signature = if ($name -eq 'backend') { 'jarvis.app:create_app' } else { 'frontend/vite.config.ts' }
    # Eine PID-Datei kann auf einen anderen Jarvis-Prozess derselben Kopie
    # zeigen. Entscheidend ist der tatsächliche Besitzer des erwarteten Ports.
    $listener = Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $ports[$name] -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $listener) { continue }
    $processId = [int]$listener.OwningProcess
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId"
    if ($null -eq $process -or [string]::IsNullOrEmpty($process.CommandLine)) { continue }
    if (-not $process.CommandLine.Contains($expected[$name]) -or -not $process.CommandLine.Contains($signature)) {
        throw "PID $processId gehört nicht zum erwarteten Jarvis-$name-Prozess. Nichts gestoppt."
    }
    Stop-Process -Id $processId -Force
    Write-Output "Jarvis-$name gestoppt (PID $processId)."
}
if (Test-Path -LiteralPath $pidFile) {
    $saved = $null
    try { $saved = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json }
    catch { Write-Output 'Ungültige PID-Datei ignoriert.' }
    if ($saved.hotkey -and $saved.owner_pid) {
        $helperId = [int]$saved.hotkey
        $helper = Get-CimInstance Win32_Process -Filter "ProcessId=$helperId"
        $helperScript = Join-Path $root 'scripts\hotkey.py'
        $helperPython = Join-Path $root 'backend\.venv\Scripts\python.exe'
        $ownerMarker = "--owner-pid $([int]$saved.owner_pid)"
        if ($helper -and $helper.CommandLine -and
            $helper.CommandLine.Contains($helperScript) -and
            $helper.CommandLine.Contains($helperPython) -and
            $helper.CommandLine.Contains($ownerMarker)) {
            Stop-Process -Id $helperId -Force
            Write-Output "Jarvis-Hotkey gestoppt (PID $helperId)."
        }
    }
}
Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
