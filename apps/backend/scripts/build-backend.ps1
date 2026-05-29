param(
  [string]$PythonExe = "python",
  [string]$OutputDir = "dist"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Invoke-CheckedCommand {
  param(
    [scriptblock]$Command,
    [string]$FailureMessage
  )

  & $Command
  if ($LASTEXITCODE -ne 0) {
    throw $FailureMessage
  }
}

$requirements = Join-Path $root "requirements.txt"
$entry = Join-Path $root "app/main.py"
$distPath = Join-Path $root $OutputDir
$workPath = Join-Path $root "build/pyinstaller"

if (-not (Test-Path -LiteralPath $requirements)) {
  throw "requirements.txt not found at '$requirements'."
}

if (-not (Test-Path -LiteralPath $entry)) {
  throw "Backend entrypoint not found at '$entry'."
}

Invoke-CheckedCommand -Command { & $PythonExe -m pip install --upgrade pip } -FailureMessage "Failed to upgrade pip."
Invoke-CheckedCommand -Command { & $PythonExe -m pip install pyinstaller -r $requirements } -FailureMessage "Failed to install backend build dependencies."

if (Test-Path -LiteralPath $distPath) {
  Remove-Item -LiteralPath $distPath -Recurse -Force
}

Invoke-CheckedCommand -Command {
  & $PythonExe -m PyInstaller `
  --onefile `
  --name fracture-backend `
  --distpath $distPath `
  --workpath $workPath `
  --specpath $workPath `
  $entry
} -FailureMessage "PyInstaller failed to build fracture-backend.exe."

$backendExe = Join-Path $distPath "fracture-backend.exe"
if (-not (Test-Path -LiteralPath $backendExe)) {
  throw "Build finished without output executable at '$backendExe'."
}

Write-Host "Built backend executable at: $backendExe"
