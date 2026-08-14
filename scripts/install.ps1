# Coffice one-shot installer for Windows.
#
# Fetches the latest GitHub release and deploys the Python package (wheel),
# the prebuilt Agent Deck UI, and the LibreOffice extension (.oxt) when
# LibreOffice is installed. Also detects the co CLI and can install it from
# its own GitHub release. No compilation or build toolchain is needed; every
# artifact comes from the release assets.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#       [-Prefix DIR] [-Version vX.Y.Z] [-InstallOxt] [-ConfigureShell]
#       [-WithCo] [-WithoutCo]
#
# Parameters:
#   -Prefix           install under DIR (default: $env:LOCALAPPDATA\coffice)
#   -Version          install a specific release tag (default: latest)
#   -InstallOxt       require the LO extension: fail loudly if LibreOffice is
#                     missing (default: install when LO present, else hint)
#   -WithCo           also install the co CLI from its GitHub release
#   -WithoutCo        skip the co CLI even if it is missing
#   -ConfigureShell   append the coffice bin dir to the user PATH
#
# Environment:
#   COFFICE_REPO      owner/repo (default: PaddySeahorse/Coffice)
#   COFFICE_PREFIX    install prefix (same as -Prefix)
#   COFFICE_VERSION   release tag to install (same as -Version)
#   CO_INSTALL        auto|yes|no (same as -WithCo/-WithoutCo)
#
# Requirements: Python 3.11+ on PATH, and curl.exe or Invoke-WebRequest
# (PowerShell built-in). tar.exe (bundled with Windows 10+) is used to unpack
# the UI bundle.

param(
    [string]$Prefix = "",
    [string]$Version = "",
    [switch]$InstallOxt,
    [switch]$WithCo,
    [switch]$WithoutCo,
    [switch]$ConfigureShell
)

$ErrorActionPreference = "Stop"

$repo = if ($env:COFFICE_REPO) { $env:COFFICE_REPO } else { "PaddySeahorse/Coffice" }
$coRepo = if ($env:CO_REPO) { $env:CO_REPO } else { "PaddySeahorse/co" }
$prefix = if ($env:COFFICE_PREFIX) { $env:COFFICE_PREFIX } else {
    if ($Prefix) { $Prefix } else { Join-Path $env:LOCALAPPDATA "coffice" }
}
if ($env:COFFICE_VERSION) { $Version = $env:COFFICE_VERSION }
$coInstall = if ($env:CO_INSTALL) { $env:CO_INSTALL } else { "auto" }
if ($WithCo) { $coInstall = "yes" }
if ($WithoutCo) { $coInstall = "no" }

$binDir = Join-Path $prefix "bin"
$venvDir = Join-Path $prefix "venv"
$uiDir = Join-Path $prefix "ui"
$py = Join-Path $venvDir "Scripts\python.exe"
$launcher = Join-Path $binDir "coffice.cmd"

function Die([string]$message) {
    Write-Host "ERROR: $message" -ForegroundColor Red
    exit 1
}

Write-Host "==> Coffice installer"
Write-Host "    repo:   $repo"
Write-Host "    prefix: $prefix"
Write-Host "    tag:    $(if ($Version) { $Version } else { 'latest' })"

# ---- resolve release tag and asset URLs -----------------------------------
# Release assets follow a fixed naming pattern (see .github/workflows/release.yml):
#   wheel: coffice-{version}-py3-none-any.whl   (version = tag without leading v)
#   ui:    coffice-agent-deck_{tag}.tar.gz
#   oxt:   Coffice.oxt
$tag = ""
if ($Version) {
    $tag = $Version
} else {
    Write-Host "==> resolving latest release"
    try {
        # Every Coffice release is published as prerelease, so releases/latest
        # never resolves; list releases and take the newest entry.
        $releases = Invoke-RestMethod -Uri "https://api.github.com/repos/$repo/releases?per_page=1" -Headers @{ "User-Agent" = "coffice-installer" } -TimeoutSec 20
        $tag = if ($releases -is [array]) { $releases[0].tag_name } else { $releases.tag_name }
    } catch {
        Die "failed to resolve the latest release for $repo (network access required). Use -Version vX.Y.Z to install a specific release."
    }
}
if (-not $tag) { Die "failed to resolve the latest release for $repo" }

$versionNoV = $tag.TrimStart("v")
$downloadBase = "https://github.com/$repo/releases/download/$tag"
$wheelUrl = "$downloadBase/coffice-$versionNoV-py3-none-any.whl"
$uiUrl = "$downloadBase/coffice-agent-deck_$tag.tar.gz"
$oxtUrl = "$downloadBase/Coffice.oxt"

Write-Host "==> installing coffice $tag"

New-Item -ItemType Directory -Force $binDir, $uiDir | Out-Null

# ---- create virtualenv ----------------------------------------------------
if (-not (Test-Path $py)) {
    Write-Host "==> creating virtualenv"
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv venv $venvDir
        if ($LASTEXITCODE -ne 0) { Die "uv venv failed" }
    } else {
        $pycmd = Get-Command py -ErrorAction SilentlyContinue
        if ($pycmd) {
            & py -3.11 -m venv $venvDir
        } else {
            & python -m venv $venvDir
        }
        if ($LASTEXITCODE -ne 0) {
            Die "python -m venv failed. Install Python 3.11+ (https://www.python.org/downloads/) and ensure it is on PATH, or install uv (https://docs.astral.sh/uv/) and rerun."
        }
    }
}

# ---- install the Python wheel ---------------------------------------------
Write-Host "==> installing Python package (prebuilt wheel, no compilation)"
if (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv pip install --python $py --upgrade pip $wheelUrl
    if ($LASTEXITCODE -ne 0) { Die "uv pip install failed for $wheelUrl" }
} else {
    & $py -m pip install --upgrade pip
    & $py -m pip install $wheelUrl
    if ($LASTEXITCODE -ne 0) { Die "pip install failed for $wheelUrl" }
}

# ---- deploy the Agent Deck UI ----------------------------------------------
Write-Host "==> deploying Agent Deck UI"
$uiBundle = Join-Path $env:TEMP "coffice-agent-deck_$tag.tar.gz"
Invoke-WebRequest -Uri $uiUrl -OutFile $uiBundle -UseBasicParsing
if (Get-Command tar -ErrorAction SilentlyContinue) {
    & tar -xzf $uiBundle -C $uiDir
    if ($LASTEXITCODE -ne 0) { Die "failed to unpack the UI bundle" }
} else {
    Die "tar.exe not found (bundled with Windows 10+). Install Windows tar or unpack the UI bundle manually from $uiBundle to $uiDir"
}
if (-not (Test-Path (Join-Path $uiDir "dist\index.html"))) {
    Die "UI bundle did not contain dist/ (broke download or release layout)"
}
Remove-Item $uiBundle -Force

# ---- LibreOffice detection --------------------------------------------------
function Get-Unopkg {
    $cmd = Get-Command unopkg -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        (Join-Path $env:ProgramFiles "LibreOffice\program\unopkg.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "LibreOffice\program\unopkg.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\LibreOffice\program\unopkg.exe")
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    return $null
}

# ---- download the LibreOffice extension ------------------------------------
$oxtPath = Join-Path $prefix "Coffice.oxt"
try {
    Invoke-WebRequest -Uri $oxtUrl -OutFile $oxtPath -UseBasicParsing
    Write-Host "==> downloading LibreOffice extension"
    $unopkg = Get-Unopkg
    if ($unopkg) {
        Write-Host "==> LibreOffice found ($unopkg)"
        Write-Host "==> installing Coffice extension (unopkg)"
        & $unopkg add --force $oxtPath
        if ($LASTEXITCODE -ne 0) { Die "unopkg add failed; check LibreOffice installation" }
    } else {
        Write-Host "==> LibreOffice not detected"
        Write-Host "    The Coffice sidebar lives inside LibreOffice, so the extension"
        Write-Host "    cannot be installed until LibreOffice is present."
        if ($InstallOxt) {
            Die "LibreOffice is required for -InstallOxt. Download it from https://www.libreoffice.org/download/"
        }
        Write-Host "    Install LibreOffice first, then run:"
        Write-Host "        coffice install-oxt   (installs $oxtPath)"
    }
} catch {
    Write-Host "    warning: failed to download the LibreOffice extension ($oxtUrl)" -ForegroundColor Yellow
}

# ---- co CLI: detect, then optionally install from its release --------------
function Co-Available {
    if ($env:CO_BIN -and (Test-Path $env:CO_BIN)) { return $true }
    if ($env:COFFICE_CO_BIN -and (Test-Path $env:COFFICE_CO_BIN)) { return $true }
    if (Get-Command co -ErrorAction SilentlyContinue) { return $true }
    return $false
}

$coBinTarget = Join-Path $binDir "co.exe"
$installCo = $false
if ($coInstall -eq "no") {
    Write-Host "==> skipping co CLI (requested)"
} elseif ($coInstall -eq "yes") {
    $installCo = $true
} elseif (Co-Available) {
    Write-Host "==> co CLI already available"
} else {
    Write-Host "==> co CLI not found (version-control snapshots)"
    Write-Host "    Coffice works without it, but document history (co log/diff,"
    Write-Host "    snapshot commits) is disabled until the co binary is available."
    $answer = Read-Host "    Install co from its GitHub release? [y/N]"
    if ($answer -match "^(y|Y|yes|YES)$") { $installCo = $true }
}

if ($installCo) {
    Write-Host "==> installing co CLI from its release"
    $coArch = if ($env:PROCESSOR_ARCHITECTURE -or $env:PROCESSOR_ARCHITEW6432) {
        "amd64"
    } else { "" }
    if ($coArch -ne "amd64") {
        Write-Host "    WARNING: co ships amd64 binaries only; build from source" -ForegroundColor Yellow
        Write-Host "    with scripts\install_co.ps1, or point CO_BIN at co.exe."
    } else {
        try {
            $coReleases = Invoke-RestMethod -Uri "https://api.github.com/repos/$coRepo/releases?per_page=1" -Headers @{ "User-Agent" = "coffice-installer" } -TimeoutSec 20
            $coTag = if ($coReleases -is [array]) { $coReleases[0].tag_name } else { $coReleases.tag_name }
        } catch {
            Die "failed to resolve the latest co release for $coRepo (use CO_INSTALL=no to skip)"
        }
        if (-not $coTag) { Die "failed to resolve the latest co release for $coRepo" }
        $coUrl = "https://github.com/$coRepo/releases/download/$coTag/co_${coTag}_windows_amd64.zip"
        Write-Host "==> downloading co $coTag"
        $coBundle = Join-Path $env:TEMP "co_${coTag}_windows_amd64.zip"
        Invoke-WebRequest -Uri $coUrl -OutFile $coBundle -UseBasicParsing
        if (Get-Command tar -ErrorAction SilentlyContinue) {
            & tar -xzf $coBundle -C $binDir
            if ($LASTEXITCODE -ne 0) { Die "failed to unpack the co binary" }
        } else {
            Expand-Archive -Path $coBundle -DestinationPath $binDir -Force
        }
        Remove-Item $coBundle -Force
        if (-not (Test-Path $coBinTarget)) { Die "co bundle did not contain co.exe" }
        Write-Host "    installed: $coBinTarget"
    }
}

# ---- write the launcher -----------------------------------------------------
$launcherContent = @"
@echo off
rem Coffice launcher
set "PREFIX=$prefix"
set "PY=%PREFIX%\venv\Scripts\python.exe"
if "%1"=="run-agent" goto run-agent
if "%1"=="run-ui" goto run-ui
if "%1"=="run-mcp" goto run-mcp
if "%1"=="install-oxt" goto install-oxt
echo usage: coffice {run-agent^|run-ui^|run-mcp^|install-oxt}
exit /b 1
:run-agent
"%PY%" -m coffice.agent.agent_api
exit /b %errorlevel%
:run-ui
"%PY%" -m http.server 8787 --directory "%PREFIX%\ui\dist"
exit /b %errorlevel%
:run-mcp
"%PY%" -m coffice.mcp.server
exit /b %errorlevel%
:install-oxt
if not exist "%PREFIX%\Coffice.oxt" goto oxt-missing
set "UNOPKG=unopkg"
where unopkg >nul 2>&1
if not errorlevel 1 goto oxt-run
if exist "%ProgramFiles%\LibreOffice\program\unopkg.exe" goto oxt-pf
if exist "%ProgramFiles(x86)%\LibreOffice\program\unopkg.exe" goto oxt-pf86
echo LibreOffice not detected. Install it from
echo https://www.libreoffice.org/download/ then rerun this command.
exit /b 1
:oxt-pf
set "UNOPKG=%ProgramFiles%\LibreOffice\program\unopkg.exe"
goto oxt-run
:oxt-pf86
set "UNOPKG=%ProgramFiles(x86)%\LibreOffice\program\unopkg.exe"
goto oxt-run
:oxt-run
"%UNOPKG%" add --force "%PREFIX%\Coffice.oxt"
exit /b %errorlevel%
:oxt-missing
echo no .oxt bundled; rerun install.ps1 to fetch it
exit /b 1
"@
Set-Content -Path $launcher -Value $launcherContent -Encoding ASCII

if ($ConfigureShell) {
    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($currentPath -notlike "*$binDir*") {
        [Environment]::SetEnvironmentVariable("Path", "$currentPath;$binDir", "User")
        Write-Host "==> added $binDir to the user PATH"
    }
    if ((Test-Path $coBinTarget) -and -not $env:CO_BIN) {
        [Environment]::SetEnvironmentVariable("CO_BIN", $coBinTarget, "User")
        Write-Host "==> set user CO_BIN=$coBinTarget"
    }
}

Write-Host ""
Write-Host "==> coffice $tag installed"
Write-Host "    prefix:  $prefix"
Write-Host "    launcher: $launcher"
if (Test-Path $coBinTarget) {
    Write-Host "    co CLI:  $coBinTarget (set CO_BIN to it if not on PATH)"
}
Write-Host ""
Write-Host "Run the agent and UI, then open http://127.0.0.1:8787/ in a browser:"
Write-Host "    coffice run-agent"
Write-Host "    coffice run-ui"
Write-Host ""
Write-Host "Configure the LLM via COFFICE_LLM_BASE_URL / COFFICE_LLM_MODEL /"
Write-Host "COFFICE_LLM_API_KEY, or from the Agent Deck Settings panel"
Write-Host "(~\.coffice\llm.json persists changes between launches)."
