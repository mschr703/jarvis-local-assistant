function Undo-JarvisServeRoute {
    param([string]$Endpoint, [int]$HttpsPort)

    $serve = & tailscale serve status --json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw 'Tailscale Serve konnte beim Aufräumen nicht gelesen werden.'
    }
    $route = if ($serve.Web) { $serve.Web.PSObject.Properties[$Endpoint] } else { $null }
    if (-not $route -or $route.Value.Handlers.'/'.Proxy -ne 'http://127.0.0.1:8770') {
        return $false
    }
    & tailscale serve "--https=$HttpsPort" off
    if ($LASTEXITCODE -ne 0) {
        throw 'Jarvis-Tailscale-Weiterleitung konnte nicht zurückgenommen werden.'
    }
    return $true
}

function Start-JarvisServeRoute {
    param([string]$TailHost, [int]$HttpsPort, [int]$GatewayPid, [string]$RecordPath)

    $endpoint = "${TailHost}:$HttpsPort"
    try {
        & tailscale serve --bg "--https=$HttpsPort" 'http://127.0.0.1:8770'
        if ($LASTEXITCODE -ne 0) {
            throw "Tailscale Serve konnte Port $HttpsPort nicht einrichten."
        }
        @{gateway_pid=$GatewayPid; host=$TailHost; port=$HttpsPort} | ConvertTo-Json |
            Set-Content -LiteralPath $RecordPath -ErrorAction Stop
    } catch {
        $startError = $_
        try {
            Undo-JarvisServeRoute -Endpoint $endpoint -HttpsPort $HttpsPort | Out-Null
        } catch {
            Write-Warning 'Jarvis-Tailscale-Route konnte nicht zurückgenommen werden; Tailscale Serve manuell prüfen.'
        }
        throw $startError
    }
}
