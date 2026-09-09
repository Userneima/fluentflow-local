# One-time Windows setup for FluentFlow Local.
# Creates the project virtual environment, installs project-owned GPU runtime
# DLLs when an NVIDIA adapter is present, builds the frontend, and creates the
# Desktop launcher.  It never installs a machine-wide CUDA Toolkit.
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

Write-Host "Checking local runtime..."
Invoke-Checked $Python @((Join-Path $Repo "scripts\check_local_readiness.py"))
Invoke-Checked (Join-Path $ScriptDir "install-local-to-desktop.ps1") @()
Write-Host "Setup complete. Double-click 'FluentFlow Local.cmd' on your Desktop."
