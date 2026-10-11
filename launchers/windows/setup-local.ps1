# One-time Windows setup for FluentFlow Local.
# Creates the project virtual environment, installs project-owned GPU runtime
# DLLs when an NVIDIA adapter is present, builds the frontend, downloads the
# transcription model, and creates the Desktop launcher. It never installs a
# machine-wide CUDA Toolkit.
#
# It asks nothing along the way. Running this script is the decision; every
# question it could ask (Python, Node.js, FFmpeg, the model) has one sensible answer, and asking
# only chops the wait into pieces the user has to sit through. Missing pieces
# are installed, each step says what it is doing, and only a failure stops it.
#
# Nothing here asks for administrator rights, so an agent can run it unattended:
# Python is installed for the current user only, Node.js (needed only to build
# the interface) is a portable copy in the data folder, and FFmpeg comes from
# winget's portable package.
#
# A failed download is retried once from a mirror in China: pip from Tsinghua,
# npm from npmmirror, the transcription model from hf-mirror. A PIP_INDEX_URL or
# npm registry you set yourself is left alone.
#
# Python dependencies are installed from requirements-local.txt (lower bounds,
# newest matching versions). There is no tested lock file for Windows yet; the
# only lock in the repository, requirements-local.lock.macos-arm64.txt, is for
# Apple Silicon and must not be used here.
#
#   ... -SkipModel   do not download the transcription model
param([switch]$SkipModel)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$Venv = Join-Path $Repo ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"

function Invoke-Checked([string]$FilePath, [string[]]$Arguments) {
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $FilePath $($Arguments -join ' ')"
    }
}

# A package winget just installed is not on this session's PATH until it is
# re-read from the registry; without this the next Get-Command misses it and the
# script tells the user to install the thing it installed a moment ago.
function Update-SessionPath {
    $env:Path = @(
        [Environment]::GetEnvironmentVariable("Path", "Machine"),
        [Environment]::GetEnvironmentVariable("Path", "User")
    ) -join ";"
}

# Same treatment as FFmpeg below: required, one sensible answer, so install it
# with winget instead of stopping to send the user off to a download page.
function Install-WithWinget([string]$Id, [string]$What, [string[]]$Extra = @()) {
    $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $Winget) {
        throw "$What was not found, and winget is not available to install it. Install $What yourself, then run this script again."
    }
    Write-Host "$What was not found. Installing it with winget..."
    Invoke-Checked $Winget.Source (@(
        "install", "--id", $Id, "-e", "--source", "winget",
        "--accept-source-agreements", "--accept-package-agreements"
    ) + $Extra)
    Update-SessionPath
}

# Retried once from a mirror in China when the default index fails.
function Invoke-Pip([string[]]$Arguments) {
    & $Python -m pip @Arguments
    if ($LASTEXITCODE -eq 0) { return }
    if ($env:PIP_INDEX_URL) { throw "pip failed: $($Arguments -join ' ')" }
    Write-Host "Download failed; retrying from the Tsinghua mirror..."
    $env:PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"
    try {
        Invoke-Checked $Python (@("-m", "pip") + $Arguments)
    } finally {
        Remove-Item Env:PIP_INDEX_URL -ErrorAction SilentlyContinue
    }
}

function Test-NvidiaAdapter {
    try {
        return @(
            Get-CimInstance Win32_VideoController -ErrorAction Stop |
                Where-Object { $_.Name -match "NVIDIA" }
        ).Count -gt 0
    } catch {
        return $false
    }
}

# Checked before anything downloads. FFmpeg is required, and the pip install
# below takes a long time: finding out at the readiness check means the user
# waited through the whole install to be told one thing was missing.
if (-not (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue) -or
    -not (Get-Command ffprobe.exe -ErrorAction SilentlyContinue)) {
    $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($Winget) {
        Write-Host "FFmpeg was not found (transcoding and frame extraction need it). Installing it..."
        # The agreement switches are what make this unattended: without them
        # winget stops on a prompt the user did not ask to be shown.
        Invoke-Checked $Winget.Source @(
            "install", "--id", "Gyan.FFmpeg", "-e", "--source", "winget",
            "--accept-source-agreements", "--accept-package-agreements"
        )
        # Re-read PATH now and prove ffmpeg is reachable before the long pip
        # install. Without this the script ran for twenty minutes and then
        # failed at the readiness check over the thing it had itself installed.
        Update-SessionPath
        if (-not (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue) -or
            -not (Get-Command ffprobe.exe -ErrorAction SilentlyContinue)) {
            throw "FFmpeg was installed by winget but ffmpeg.exe / ffprobe.exe are still not on PATH. Close this PowerShell window, open a new one, and run this script again. If they are still missing, add FFmpeg's bin folder to PATH yourself."
        }
        Write-Host "FFmpeg installed: $((Get-Command ffmpeg.exe).Source)"
    } else {
        throw "FFmpeg was not found. Install it (winget install Gyan.FFmpeg) and make sure ffmpeg.exe and ffprobe.exe are on PATH, then run this script again."
    }
}

if (-not (Test-Path $Python)) {
    # The dependencies need 3.10 or newer; an older `py -3` builds a venv that
    # then fails deep inside pip with an error that never mentions the version.
    $PyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    $PythonOk = $false
    if ($PyLauncher) {
        # try/catch because Windows PowerShell 5.1 turns a native command's
        # stderr into a terminating error under ErrorActionPreference=Stop, and
        # py.exe with no Python installed writes exactly that.
        try {
            & $PyLauncher.Source -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
            $PythonOk = ($LASTEXITCODE -eq 0)
        } catch {
            $PythonOk = $false
        }
    }
    if (-not $PythonOk) {
        # User scope: the machine-wide installer asks for administrator rights.
        Install-WithWinget "Python.Python.3.12" "Python 3.10 or newer" @("--scope", "user")
        $PyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
        if (-not $PyLauncher) {
            throw "Python was installed but py.exe is not on PATH yet. Open a new PowerShell window and run this script again."
        }
    }
    Write-Host "Creating project virtual environment..."
    Invoke-Checked $PyLauncher.Source @("-3", "-m", "venv", $Venv)
}

Write-Host "Installing FluentFlow Local Python dependencies..."
Invoke-Pip @("install", "-r", (Join-Path $Repo "requirements-local.txt"))

# yt-dlp is kept at its newest release: Douyin, Bilibili and YouTube change their
# pages often and an old copy fails a whole platform. Not fatal: it only affects
# video links, and every launch retries through scripts\update_yt_dlp.py.
Write-Host "Installing/updating yt-dlp (the video link downloader)..."
try {
    & $Python -m pip install -U --disable-pip-version-check yt-dlp
    $YtDlpOk = ($LASTEXITCODE -eq 0)
} catch {
    $YtDlpOk = $false
}
if (-not $YtDlpOk) {
    Write-Host "! yt-dlp could not be installed or updated (usually a network problem). Local files still work; video links need it. Every launch retries automatically, or run: .\.venv\Scripts\python.exe scripts\update_yt_dlp.py --force" -ForegroundColor Yellow
}

if (Test-NvidiaAdapter) {
    Write-Host "NVIDIA adapter detected. Installing FluentFlow's GPU runtime..."
    Invoke-Pip @("install", "-r", (Join-Path $Repo "requirements-windows-gpu.txt"))
} else {
    Write-Host "No NVIDIA adapter detected; FluentFlow will use the CPU transcription runtime."
}

# Only building the interface needs Node.js. Without one on the machine, a
# portable copy goes into the data folder for this step alone: the winget
# package is a machine-wide installer that asks for administrator rights.
$NodeVersion = "v24.20.0"
$Npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $Npm) {
    $DataDir = if ($env:FLUENTFLOW_DATA_DIR) { $env:FLUENTFLOW_DATA_DIR } else { Join-Path $env:APPDATA "FluentFlow" }
    $ToolsDir = Join-Path $DataDir "tools"
    New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null
    $NodeName = "node-$NodeVersion-win-x64"
    $NodeHome = Join-Path $ToolsDir $NodeName
    if (-not (Test-Path (Join-Path $NodeHome "npm.cmd"))) {
        Write-Host "Node.js was not found (building the interface needs it). Downloading a portable copy..."
        $Zip = Join-Path $ToolsDir "$NodeName.zip"
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "https://nodejs.org/dist/$NodeVersion/$NodeName.zip" -OutFile $Zip
        } catch {
            Invoke-WebRequest -UseBasicParsing -Uri "https://npmmirror.com/mirrors/node/$NodeVersion/$NodeName.zip" -OutFile $Zip
        }
        Expand-Archive -Force -Path $Zip -DestinationPath $ToolsDir
        Remove-Item $Zip
    }
    $env:Path = "$NodeHome;$env:Path"
    $Npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $Npm) {
        throw "Node.js was downloaded but npm.cmd is not runnable from $NodeHome."
    }
}

# Retried once from npmmirror when the default registry fails.
function Invoke-Npm([string[]]$Arguments) {
    & $Npm.Source @Arguments
    if ($LASTEXITCODE -eq 0) { return }
    Write-Host "Download failed; retrying from the npmmirror registry..."
    Invoke-Checked $Npm.Source ($Arguments + @("--registry=https://registry.npmmirror.com"))
}

# Picked by what package.json declares rather than hard-coded, so a renamed
# build script fails loudly here instead of silently building nothing.
$BuildScript = "build:frontend"
$PackageJson = Join-Path $Repo "package.json"
if (Test-Path $PackageJson) {
    $Scripts = (Get-Content $PackageJson -Raw | ConvertFrom-Json).scripts
    if ($Scripts.PSObject.Properties.Name -contains "build:frontend:local") {
        $BuildScript = "build:frontend:local"
    }
}

Push-Location $Repo
try {
    Write-Host "Installing frontend dependencies..."
    Invoke-Npm @("ci")
    Write-Host "Building the local frontend ($BuildScript)..."
    Invoke-Checked $Npm.Source @("run", $BuildScript)
} finally {
    Pop-Location
}

# Downloaded here rather than inside the user's first transcription, where the
# wait has no progress and no explanation. A failure is not fatal: the model can
# be fetched later, and the first run falls back to downloading it itself.
if ($SkipModel) {
    Write-Host "Skipping the transcription model; the first transcription will download it."
} else {
    Write-Host "Preparing the transcription model..."
    & $Python (Join-Path $Repo "scripts\stt_model.py") "fetch"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Download failed; retrying from the hf-mirror.com mirror..."
        & $Python (Join-Path $Repo "scripts\stt_model.py") "fetch" "--mirror"
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "The model was not downloaded. The first transcription will download it (switching to the hf-mirror.com mirror if huggingface.co is unreachable), or run .\.venv\Scripts\python.exe scripts\stt_model.py fetch --mirror now."
    }
}

Write-Host "Checking local runtime..."
Invoke-Checked $Python @((Join-Path $Repo "scripts\check_local_readiness.py"))
Invoke-Checked (Join-Path $ScriptDir "install-local-to-desktop.ps1") @()
Write-Host "Setup complete. Double-click 'FluentFlow Local.cmd' on your Desktop."
