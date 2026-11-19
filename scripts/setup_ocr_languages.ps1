param([string]$TesseractExe = '')

$ErrorActionPreference = 'Stop'
if (-not $TesseractExe) {
    $command = Get-Command tesseract -ErrorAction SilentlyContinue
    if (-not $command) {
        throw 'Tesseract fehlt. Zuerst Tesseract installieren und die neue PowerShell erneut öffnen.'
    }
    $TesseractExe = $command.Source
}
if (-not (Test-Path -LiteralPath $TesseractExe -PathType Leaf)) {
    throw "Tesseract nicht gefunden: $TesseractExe"
}
$languages = @(& $TesseractExe --list-langs 2>&1)
if ($LASTEXITCODE -ne 0 -or $languages.Count -eq 0 -or $languages[0] -notmatch 'in "([^"]+)"') {
    throw 'Tesseract-Sprachdatenordner konnte nicht bestimmt werden.'
}
$target = [System.IO.Path]::GetFullPath($Matches[1])
if (-not (Test-Path -LiteralPath $target -PathType Container)) {
    New-Item -ItemType Directory -Path $target -Force | Out-Null
}
$files = @(
    @{ Name = 'deu'; Sha256 = '19d219bbb6672c869d20a9636c6816a81eb9a71796cb93ebe0cb1530e2cdb22d' },
    @{ Name = 'eng'; Sha256 = '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2' }
)
foreach ($file in $files) {
    $destination = Join-Path $target ($file.Name + '.traineddata')
    if (Test-Path -LiteralPath $destination -PathType Leaf) {
        Write-Output "Bereits vorhanden: $($file.Name)"
        continue
    }
    $temporary = Join-Path $target ($file.Name + '.traineddata.download')
    try {
        $url = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/$($file.Name).traineddata"
        Invoke-WebRequest -Uri $url -OutFile $temporary -ErrorAction Stop
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $temporary).Hash.ToLowerInvariant()
        if ($actual -ne $file.Sha256) { throw "Prüfsumme für $($file.Name) stimmt nicht." }
        Move-Item -LiteralPath $temporary -Destination $destination -ErrorAction Stop
        Write-Output "Installiert: $($file.Name)"
    }
    finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}
$available = @(& $TesseractExe --list-langs 2>&1)
if ($LASTEXITCODE -ne 0 -or 'deu' -notin $available -or 'eng' -notin $available) {
    throw 'Tesseract findet deu und eng noch nicht. Sprachdatenordner und Zugriffsrechte prüfen.'
}
Write-Output "OCR-Sprachen bereit: deu und eng in $target"
