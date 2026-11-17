$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envFile = Join-Path $repo '.env'
if (-not (Test-Path -LiteralPath $envFile)) {
    Copy-Item -LiteralPath (Join-Path $repo '.env.example') -Destination $envFile
}

$contents = [System.IO.File]::ReadAllText($envFile)
$matches = [regex]::Matches($contents, '(?m)^JARVIS_VOICESTUDIO_API_KEY=(.+)$')
if ($matches.Count -gt 1) { throw 'Mehrere VoiceStudio-Schlüssel in .env gefunden.' }
if ($matches.Count -eq 1) {
    $key = $matches[0].Groups[1].Value.Trim()
    if ($key -notmatch '^[A-Fa-f0-9]{64}$') {
        throw 'Der lokale VoiceStudio-Schlüssel in .env muss 64 Hex-Zeichen haben.'
    }
} else {
    $key = [Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLowerInvariant()
    Add-Content -LiteralPath $envFile -Value "JARVIS_VOICESTUDIO_API_KEY=$key" -Encoding utf8
}

$image = 'ghcr.io/debpalash/voicestudio:stable'
$existingImage = docker image inspect $image 2>$null
if ($LASTEXITCODE -ne 0) { docker pull $image | Out-Host }
$existing = docker container inspect jarvis-voicestudio 2>$null
if ($LASTEXITCODE -eq 0) {
    $running = docker inspect --format '{{.State.Running}}' jarvis-voicestudio
    if ($running -ne 'true') { docker start jarvis-voicestudio | Out-Null }
} else {
    docker run -d --name jarvis-voicestudio `
        -p 127.0.0.1:3900:3900 `
        -e "OMNIVOICE_API_KEY=$key" `
        -e OMNIVOICE_ANALYTICS_DISABLED=1 `
        -v jarvis-voicestudio-data:/app/omnivoice_data `
        $image | Out-Null
}

$ready = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    try {
        $status = Invoke-RestMethod -Uri 'http://127.0.0.1:3900/health' `
            -Headers @{ Authorization = "Bearer $key" } -TimeoutSec 2
        if ($status) { $ready = $true; break }
    } catch { Start-Sleep -Seconds 2 }
}
if (-not $ready) {
    throw 'VoiceStudio antwortet nicht. Details: docker logs jarvis-voicestudio'
}
Write-Host 'VoiceStudio läuft lokal auf 127.0.0.1:3900. Der Schlüssel ist in .env gespeichert.'
Write-Host 'Falls noch kein Sprachmodell aktiv ist: http://127.0.0.1:3900 öffnen und ein deutsches Modell einrichten.'
