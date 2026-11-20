$ErrorActionPreference = 'Stop'
$recordPath = Join-Path $env:LOCALAPPDATA 'JarvisLocal\tailscale-gateway.json'
if (-not (Test-Path -LiteralPath $recordPath)) {
    throw 'Kein von Jarvis gestarteter Tailscale-Gateway-Prozess vermerkt.'
}
$record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
$endpoint = "$($record.host):$($record.port)"
$serve = tailscale serve status --json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Tailscale Serve konnte nicht gelesen werden.' }
$handler = $serve.Web.PSObject.Properties[$endpoint].Value.Handlers.'/'
if ($handler.Proxy -ne 'http://127.0.0.1:8770') {
    throw 'Tailscale-Port zeigt nicht auf den Jarvis-Gateway; keine Weiterleitung verändert.'
}
& tailscale serve "--https=$($record.port)" off
if ($LASTEXITCODE -ne 0) { throw 'Tailscale-Weiterleitung konnte nicht entfernt werden.' }
$gatewayPid = [int]$record.gateway_pid
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$gatewayPid"
if ($process -and $process.CommandLine -like '*tailscale_gateway:create_app*') {
    Stop-Process -Id $gatewayPid -Force
}
Remove-Item -LiteralPath $recordPath
Write-Output 'Jarvis-Tailscale-Zugang gestoppt; andere Tailscale-Dienste bleiben bestehen.'
