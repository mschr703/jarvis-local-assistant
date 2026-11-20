param([ValidateRange(1, 65535)][int]$HttpsPort = 8443)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'tailscale_route.ps1')
$state = tailscale status --json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $state.BackendState -ne 'Running') {
    throw 'Tailscale ist auf diesem Laptop nicht verbunden.'
}
$tailHost = [string]$state.Self.DNSName
$tailHost = $tailHost.TrimEnd('.').ToLowerInvariant()
$ownerId = [string]$state.Self.UserID
$owner = $state.User.PSObject.Properties[$ownerId].Value
$tailLogin = [string]$owner.LoginName
if ($tailHost -notmatch '^[a-z0-9-]+(\.[a-z0-9-]+)*\.ts\.net$' -or
    [string]::IsNullOrWhiteSpace($tailLogin)) {
    throw 'Tailscale-Host oder Nutzeridentität fehlt.'
}
$serve = tailscale serve status --json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Tailscale Serve konnte nicht gelesen werden.' }
$endpoint = "${tailHost}:$HttpsPort"
if ($serve.Web -and $serve.Web.PSObject.Properties[$endpoint]) {
    throw "Port $HttpsPort ist in Tailscale Serve bereits belegt; bestehende Weiterleitung bleibt erhalten."
}
foreach ($url in @('http://127.0.0.1:5173/', 'http://127.0.0.1:8765/health')) {
    $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10
    if ($response.StatusCode -ne 200) { throw "Jarvis antwortet nicht auf $url" }
}
$gatewayPort = Get-NetTCPConnection -LocalPort 8770 -State Listen -ErrorAction SilentlyContinue
if ($gatewayPort) { throw 'Lokaler Gateway-Port 8770 ist bereits belegt.' }
Push-Location -LiteralPath $root
try {
    & pnpm --dir frontend build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend-Build fehlgeschlagen.' }
    $logDir = Join-Path $env:LOCALAPPDATA 'JarvisLocal\logs'
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $env:JARVIS_TAILSCALE_HOST = $tailHost
    $env:JARVIS_TAILSCALE_LOGIN = $tailLogin
    $env:JARVIS_TAILSCALE_PORT = [string]$HttpsPort
    $python = Join-Path $root 'backend\.venv\Scripts\python.exe'
    $gateway = Start-Process -FilePath $python -ArgumentList @(
        '-m', 'uvicorn', 'tailscale_gateway:create_app', '--factory',
        '--app-dir', 'scripts', '--host', '127.0.0.1', '--port', '8770'
    ) -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (
        Join-Path $logDir 'tailscale-out.log') -RedirectStandardError (
        Join-Path $logDir 'tailscale-error.log') -PassThru
    try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        if (-not (Get-NetTCPConnection -LocalPort 8770 -State Listen -ErrorAction SilentlyContinue)) {
            if ($gateway.HasExited) { break }
            continue
        }
        & curl.exe --silent --fail --output NUL `
            --header "Host: $endpoint" --header "Tailscale-User-Login: $tailLogin" `
            'http://127.0.0.1:8770/api/health' 2>$null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        if ($gateway.HasExited) { break }
    }
    if (-not $ready) {
        if (-not $gateway.HasExited) { Stop-Process -Id $gateway.Id -Force }
        throw "Jarvis-Tailscale-Gateway startete nicht. Log: $logDir\tailscale-error.log"
    }
    Start-JarvisServeRoute -TailHost $tailHost -HttpsPort $HttpsPort -GatewayPid $gateway.Id `
        -RecordPath (Join-Path $env:LOCALAPPDATA 'JarvisLocal\tailscale-gateway.json')
    Write-Output "Jarvis im privaten Tailnet: https://${tailHost}:$HttpsPort/"
    Write-Output 'Zugriff nur mit der Tailscale-Nutzeridentität dieses Laptop-Eigentümers.'
    } catch {
        if ($gateway -and -not $gateway.HasExited) { Stop-Process -Id $gateway.Id -Force }
        throw
    }
} finally {
    Pop-Location
    Remove-Item Env:JARVIS_TAILSCALE_HOST -ErrorAction SilentlyContinue
    Remove-Item Env:JARVIS_TAILSCALE_LOGIN -ErrorAction SilentlyContinue
    Remove-Item Env:JARVIS_TAILSCALE_PORT -ErrorAction SilentlyContinue
}
