param(
  [ValidateSet("dev", "prod")]
  [string]$Mode = "dev",
  [switch]$BuildIfMissing
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$desktopDir = Join-Path $repoRoot "apps/desktop"
$backendDir = Join-Path $repoRoot "apps/backend"
$releaseExe = Join-Path $desktopDir "src-tauri/target/release/fracture-desktop.exe"
$backendExe = Join-Path $repoRoot "apps/backend/dist/fracture-backend.exe"
$backendBuildScript = Join-Path $repoRoot "apps/backend/scripts/build-backend.ps1"

function Get-WinInetProxyAddress {
  try {
    $settings = Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    if ($settings.ProxyEnable -ne 1 -or [string]::IsNullOrWhiteSpace($settings.ProxyServer)) {
      return $null
    }

    $proxySpec = $settings.ProxyServer
    if ($proxySpec -match "=") {
      $explicitHttps = [regex]::Match($proxySpec, "(^|;)https=([^;]+)")
      if ($explicitHttps.Success) {
        $proxySpec = $explicitHttps.Groups[2].Value
      } else {
        $explicitHttp = [regex]::Match($proxySpec, "(^|;)http=([^;]+)")
        if ($explicitHttp.Success) {
          $proxySpec = $explicitHttp.Groups[2].Value
        } else {
          $proxySpec = $proxySpec.Split(";")[0]
          if ($proxySpec -match "=") {
            $proxySpec = $proxySpec.Split("=")[1]
          }
        }
      }
    }

    if ([string]::IsNullOrWhiteSpace($proxySpec)) {
      return $null
    }

    if ($proxySpec -notmatch "^[a-zA-Z]+://") {
      return "http://$proxySpec"
    }

    return $proxySpec
  } catch {
    return $null
  }
}

function Configure-NativeProxy {
  if (-not [string]::IsNullOrWhiteSpace($env:HTTPS_PROXY) -or -not [string]::IsNullOrWhiteSpace($env:HTTP_PROXY)) {
    return
  }

  $proxy = Get-WinInetProxyAddress
  if ([string]::IsNullOrWhiteSpace($proxy)) {
    return
  }

  $env:HTTPS_PROXY = $proxy
  $env:HTTP_PROXY = $proxy
  Write-Host "Configured HTTP(S)_PROXY for native tools from Windows proxy settings."
}

function Test-CargoExecutable {
  param(
    [string]$CargoExe
  )

  if ([string]::IsNullOrWhiteSpace($CargoExe) -or -not (Test-Path -LiteralPath $CargoExe)) {
    return $false
  }

  try {
    & $CargoExe --version *> $null
    return $LASTEXITCODE -eq 0
  } catch {
    return $false
  }
}

function Resolve-CargoExecutable {
  $candidates = [System.Collections.Generic.List[string]]::new()

  $fromPath = Get-Command cargo -ErrorAction SilentlyContinue
  if ($null -ne $fromPath -and -not [string]::IsNullOrWhiteSpace($fromPath.Source)) {
    $candidates.Add($fromPath.Source)
  }

  if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
    $userCargo = Join-Path $env:USERPROFILE ".cargo\bin\cargo.exe"
    $candidates.Add($userCargo)
  }

  if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
    $rustInstalls = Get-ChildItem -LiteralPath $env:ProgramFiles -Directory -Filter "Rust*" -ErrorAction SilentlyContinue |
      Sort-Object LastWriteTime -Descending
    foreach ($installDir in $rustInstalls) {
      $candidates.Add((Join-Path $installDir.FullName "bin\cargo.exe"))
    }
  }

  foreach ($candidate in ($candidates | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique)) {
    if (Test-CargoExecutable -CargoExe $candidate) {
      return $candidate
    }
  }

  return $null
}

function Use-CargoToolchain {
  param(
    [string]$CargoExe
  )

  $cargoBin = Split-Path -Parent $CargoExe
  $pathEntries = $env:Path -split ";"
  if (-not ($pathEntries -contains $cargoBin)) {
    $env:Path = "$cargoBin;$env:Path"
  }

  Configure-NativeProxy
}

if ($Mode -eq "dev") {
  Write-Host "Starting Fracture in development mode..."
  $cargoExe = Resolve-CargoExecutable

  if ($null -eq $cargoExe) {
    throw "Cargo is required for dev mode. Install Rust/Cargo and run again."
  }

  Use-CargoToolchain -CargoExe $cargoExe
  Write-Host "Cargo detected at '$cargoExe'. Launching Tauri desktop dev shell..."
  & npm --prefix $desktopDir run tauri:dev
  exit $LASTEXITCODE
}

if (-not (Test-Path -LiteralPath $releaseExe)) {
  if (-not $BuildIfMissing) {
    throw "Production executable not found at '$releaseExe'. Run 'npm run run:prod:build' to build and launch."
  }

  if (-not (Test-Path -LiteralPath $backendExe)) {
    Write-Host "Backend executable missing. Building backend..."
    & powershell -ExecutionPolicy Bypass -File $backendBuildScript
    if ($LASTEXITCODE -ne 0) {
      exit $LASTEXITCODE
    }
  }

  $cargoExe = Resolve-CargoExecutable
  if ($null -eq $cargoExe) {
    throw "Cannot build desktop production executable because 'cargo' is not installed. Install Rust (rustup) or use a prebuilt fracture-desktop.exe."
  }

  Use-CargoToolchain -CargoExe $cargoExe
  Write-Host "Using cargo at '$cargoExe'."

  Write-Host "Release executable missing. Building desktop app..."
  & npm --prefix $desktopDir run tauri:build
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}

Write-Host "Starting Fracture in production mode..."
& $releaseExe
exit $LASTEXITCODE
