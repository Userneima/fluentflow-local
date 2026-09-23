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
function Install-WithWinget([string]$Id, [string]$What) {
    $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $Winget) {
        throw "$What was not found, and winget is not available to install it. Install $What yourself, then run this script again."
    }
    Write-Host "$What was not found. Installing it with winget..."
    Invoke-Checked $Winget.Source @(
        "install", "--id", $Id, "-e", "--source", "winget",
        "--accept-source-agreements", "--accept-package-agreements"
    )
    Update-SessionPath
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
        Write-Host "FFmpeg installed. If the next step cannot find it, open a new PowerShell window so PATH is refreshed."
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
        Install-WithWinget "Python.Python.3.12" "Python 3.10 or newer"
        $PyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
        if (-not $PyLauncher) {
            throw "Python was installed but py.exe is not on PATH yet. Open a new PowerShell window and run this script again."
        }
    }
    Write-Host "Creating project virtual environment..."
    Invoke-Checked $PyLauncher.Source @("-3", "-m", "venv", $Venv)
}

Write-Host "Installing FluentFlow Local Python dependencies..."
Invoke-Checked $Python @("-m", "pip", "install", "-r", (Join-Path $Repo "requirements-local.txt"))

if (Test-NvidiaAdapter) {
    Write-Host "NVIDIA adapter detected. Installing FluentFlow's GPU runtime..."
    Invoke-Checked $Python @("-m", "pip", "install", "-r", (Join-Path $Repo "requirements-windows-gpu.txt"))
} else {
    Write-Host "No NVIDIA adapter detected; FluentFlow will use the CPU transcription runtime."
}

$Npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $Npm) {
    Install-WithWinget "OpenJS.NodeJS.LTS" "Node.js"
    $Npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $Npm) {
        throw "Node.js was installed but npm is not on PATH yet. Open a new PowerShell window and run this script again."
    }
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
    Invoke-Checked $Npm.Source @("ci")
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
        Write-Host "The model was not downloaded. The first transcription will retry it, or run scripts\stt_model.py fetch later."
    }
}

Write-Host "Checking local runtime..."
Invoke-Checked $Python @((Join-Path $Repo "scripts\check_local_readiness.py"))
Invoke-Checked (Join-Path $ScriptDir "install-local-to-desktop.ps1") @()
Write-Host "Setup complete. Double-click 'FluentFlow Local.cmd' on your Desktop."
