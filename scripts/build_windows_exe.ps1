$ErrorActionPreference = "Stop"

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RootDir

if ($env:PYTHON_BIN) {
    $PythonBin = $env:PYTHON_BIN
} else {
    $PythonBin = "py -3.11"
}

$BuildVenv = Join-Path $RootDir ".build-venv-windows"
$SpecPath = Join-Path $RootDir "packaging\stock_watch_assistant_windows.spec"
$OutputDir = Join-Path $RootDir "dist\StockWatchAssistant"
$ZipPath = Join-Path $RootDir "dist\StockWatchAssistant-Windows-x86_64.zip"

$SpecText = Get-Content $SpecPath -Raw
if ($SpecText -match 'config"\s*/\s*"watchlist\.yaml' -or $SpecText -match "config'\s*/\s*'watchlist\.yaml") {
    throw "Privacy guard failed: Windows spec must use packaging/default_config/watchlist.yaml, not local config/watchlist.yaml."
}
if ($SpecText -match '\.env"\)' -or $SpecText -match "\.env'\)") {
    throw "Privacy guard failed: Windows spec must not bundle local .env."
}

Invoke-Expression "$PythonBin -m venv `"$BuildVenv`""
& "$BuildVenv\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel
& "$BuildVenv\Scripts\python.exe" -m pip install -r requirements-desktop.txt pyinstaller

if (Test-Path build) { Remove-Item build -Recurse -Force }
if (Test-Path dist) { Remove-Item dist -Recurse -Force }

& "$BuildVenv\Scripts\pyinstaller.exe" --clean --noconfirm $SpecPath

if (Test-Path (Join-Path $OutputDir ".env")) {
    throw "Privacy guard failed: .env was found in the Windows package."
}
$BundledEnvFiles = Get-ChildItem $OutputDir -Recurse -Force -File -Filter ".env" -ErrorAction SilentlyContinue
if ($BundledEnvFiles) {
    throw "Privacy guard failed: .env was found in the Windows package."
}

if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path $OutputDir -DestinationPath $ZipPath -Force

Write-Host "Windows exe built at:"
Write-Host "$OutputDir\StockWatchAssistant.exe"
Write-Host "Windows zip built at:"
Write-Host "$ZipPath"
