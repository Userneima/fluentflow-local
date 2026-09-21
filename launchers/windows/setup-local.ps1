# One-time Windows setup for FluentFlow Local.
# Creates the project virtual environment, installs project-owned GPU runtime
# DLLs when an NVIDIA adapter is present, builds the frontend, downloads the
# transcription model, and creates the Desktop launcher. It never installs a
# machine-wide CUDA Toolkit.
#
# It asks nothing along the way. Running this script is the decision; every
# question it could ask (FFmpeg, the model) has one sensible answer, and asking
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
    $PyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if (-not $PyLauncher) {
        throw "Python 3 was not found. Install Python 3, then run this script again."
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
    throw "npm was not found. Install a supported Node.js version, then run this script again."
}

# The exported local tree renames the local build to `build:frontend`; upstream
# that name belongs to the hosted frontend and the local one is
# `build:frontend:local`. Picking by what package.json actually declares keeps
# this script correct in both trees instead of silently building the wrong one.
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
