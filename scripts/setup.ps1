param(
    [switch]$SkipModelDownload,
    [switch]$IncludeSearchModel
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path

function Require-Command {
    param([string]$Name, [string]$InstallUrl)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "'$Name' fehlt. Installation: $InstallUrl"
    }
}

function Require-Version {
    param([string]$Name, [version]$Current, [version]$Minimum)
    if ($Current -lt $Minimum) {
        throw "'$Name' ist zu alt ($Current). Mindestens $Minimum ist nötig."
    }
}

function Run-Checked {
    param([string]$Description, [scriptblock]$Action)
    Write-Host "`n$Description"
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$Description fehlgeschlagen (Exitcode $LASTEXITCODE)."
    }
}

if ($env:OS -ne 'Windows_NT') {
    throw 'Dieser Einrichtungshelfer ist für Windows vorgesehen.'
}

Require-Command 'python' 'https://www.python.org/downloads/windows/'
Require-Command 'node' 'https://nodejs.org/en/download'
Require-Command 'pnpm' 'https://pnpm.io/installation'
$pythonVersionOutput = (& python --version).Trim()
if ($LASTEXITCODE -ne 0 -or $pythonVersionOutput -notmatch '^Python\s+(\d+\.\d+(?:\.\d+)?)') {
    throw "Python-Version konnte nicht gelesen werden: $pythonVersionOutput"
}
Require-Version 'Python' ([version]$Matches[1]) ([version]'3.13')
Require-Version 'Node.js' ([version]((& node --version).TrimStart('v'))) ([version]'24.0')
Require-Version 'pnpm' ([version](& pnpm --version)) ([version]'11.0')

$uvCommand = if (Get-Command uv -ErrorAction SilentlyContinue) { 'uv' } else { $null }
if (-not $uvCommand) {
    & python -m uv --version *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "uv fehlt. Installation: https://docs.astral.sh/uv/getting-started/installation/"
    }
}

Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath '.env' -PathType Leaf)) {
        Copy-Item -LiteralPath '.env.example' -Destination '.env'
        Write-Host 'Lokale .env aus der Vorlage erstellt.'
    } else {
        Write-Host 'Vorhandene lokale .env bleibt erhalten.'
    }

    $provider = 'openrouter'
    foreach ($line in (Get-Content -LiteralPath '.env' -Encoding UTF8)) {
        if ($line -match '^\s*JARVIS_PROVIDER\s*=') {
            if ($line -match '^\s*JARVIS_PROVIDER\s*=\s*["'']?(ollama|openrouter)["'']?\s*$') {
                $provider = $Matches[1].ToLowerInvariant()
            } else {
                throw 'JARVIS_PROVIDER in .env muss ollama oder openrouter sein.'
            }
        }
    }
    if ($env:JARVIS_PROVIDER) {
        $provider = $env:JARVIS_PROVIDER.ToLowerInvariant()
        if ($provider -notin @('ollama', 'openrouter')) {
            throw 'JARVIS_PROVIDER im Prozess muss ollama oder openrouter sein.'
        }
    }
    if ($provider -eq 'ollama' -or ($IncludeSearchModel -and -not $SkipModelDownload)) {
        Require-Command 'ollama' 'https://ollama.com/download/windows'
    }

    if ($uvCommand) {
        Run-Checked 'Backend-Abhängigkeiten installieren' { & uv sync --project backend --frozen }
    } else {
        Run-Checked 'Backend-Abhängigkeiten installieren' { & python -m uv sync --project backend --frozen }
    }
    Run-Checked 'Frontend-Abhängigkeiten installieren' { & pnpm --dir frontend install --frozen-lockfile }

    if (-not $SkipModelDownload -and $provider -eq 'ollama') {
        Run-Checked 'Lokales Modell laden' { & ollama pull qwen3:4b-instruct }
    }
    if (-not $SkipModelDownload -and $IncludeSearchModel) {
        Run-Checked 'Lokales Suchmodell laden' { & ollama pull embeddinggemma:300m }
    }

    Write-Host "`nEinrichtung abgeschlossen. Start: python scripts/dev.py"
} finally {
    Pop-Location
}
