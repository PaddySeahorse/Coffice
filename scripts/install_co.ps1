 param(
    [string]$Prefix = "$env:LOCALAPPDATA\co",
    [string]$SourceDir = ""
)

$ErrorActionPreference = "Stop"
$repo = if ($env:CO_REPO_URL) { $env:CO_REPO_URL } else { "https://github.com/PaddySeahorse/co.git" }
$source = if ($SourceDir) { $SourceDir } else { Join-Path ([System.IO.Path]::GetTempPath()) ("co-build-" + [guid]::NewGuid()) }
$build = Join-Path $source "build"
$binary = Join-Path $build "Release\co.exe"
$destination = Join-Path $Prefix "bin\co.exe"

foreach ($tool in @("git", "cmake")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "Missing required tool '$tool'. Install it and rerun this script."
    }
}

Write-Host "==> cloning $repo"
& git clone --quiet $repo $source
if ($LASTEXITCODE -ne 0) { throw "git clone failed for $repo" }

Write-Host "==> building with CMake"
& cmake -S $source -B $build -DCMAKE_BUILD_TYPE=Release
if ($LASTEXITCODE -ne 0) { throw "CMake configuration failed" }
& cmake --build $build --config Release
if ($LASTEXITCODE -ne 0) { throw "CMake build failed" }
if (-not (Test-Path $binary)) { throw "Build completed but $binary was not found" }

New-Item -ItemType Directory -Force (Split-Path $destination) | Out-Null
Copy-Item $binary $destination -Force
Write-Host "==> installed $destination"
Write-Host "Set CO_BIN=$destination in your environment, or add $(Split-Path $destination) to PATH."
