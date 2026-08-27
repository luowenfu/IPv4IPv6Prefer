# IPv4 / IPv6 Prefer - one-file build
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$icon = Join-Path $PSScriptRoot "assets\app.ico"
if (-not (Test-Path $icon)) {
  throw "Missing icon: $icon"
}

python -m pip install -r requirements.txt
python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name IPv4IPv6Prefer `
  --icon $icon `
  --add-data "$icon;assets" `
  --paths src `
  --distpath dist `
  --workpath build `
  --specpath build `
  src/main.py

Write-Host "Built: $PSScriptRoot\dist\IPv4IPv6Prefer.exe"
