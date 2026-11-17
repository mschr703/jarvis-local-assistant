$ErrorActionPreference = 'Stop'
$running = docker inspect --format '{{.State.Running}}' jarvis-voicestudio 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host 'Kein Jarvis-VoiceStudio-Container vorhanden.'; exit 0 }
if ($running -eq 'true') {
    docker stop jarvis-voicestudio | Out-Null
    Write-Host 'Jarvis-VoiceStudio-Container gestoppt.'
} else {
    Write-Host 'Jarvis-VoiceStudio-Container ist bereits gestoppt.'
}
