# FluentFlow Local launcher (Windows).
#
# Double-click FluentFlowLocal.cmd. This console window is the local service:
# press Ctrl+C or close it to stop FluentFlow Local. It deliberately starts
# backend.local_main:app, never the hosted application entry.
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = if ($env:FLUENTFLOW_REPO) { $env:FLUENTFLOW_REPO } else { Split-Path -Parent (Split-Path -Parent $ScriptDir) }
$Port = if ($env:FLUENTFLOW_LOCAL_PORT) { $env:FLUENTFLOW_LOCAL_PORT } else { "8000" }
$AppUrl = "http://127.0.0.1:$Port/"
$LocalAppData = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $env:USERPROFILE "AppData\\Local" }
$LogDir = Join-Path $LocalAppData "FluentFlow\\Logs"
$LogFile = Join-Path $LogDir "local.log"

function Fail([string]$Message) {
    Write-Host ""
    Write-Host "x $Message" -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to close" | Out-Null
    exit 1
}

function Get-LocalHealth {
    try {
        return Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
    } catch {
        return $null
    }
}

function Test-PortListening {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connect = $client.BeginConnect("127.0.0.1", [int]$Port, $null, $null)
        if (-not $connect.AsyncWaitHandle.WaitOne(2000)) { return $false }
        $client.EndConnect($connect)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

if (-not (Test-Path (Join-Path $Repo "backend\\local_main.py"))) {
    Fail "Cannot find the FluentFlow code directory (looked in: $Repo). Set FLUENTFLOW_REPO to the code directory, or run install-local-to-desktop.ps1 again."
}

$health = Get-LocalHealth
if ($health) {
    # /health nests the edition marker under "runtime"; reading it from the
    # top level made a healthy running instance look like a foreign process.
    if ($health.runtime -and $health.runtime.execution -eq "local") {
        Write-Host "FluentFlow Local is already running. Opening the workspace."
        Start-Process $AppUrl
        exit 0
    }
    Fail "Port $Port is already serving a different application. Stop that application or set FLUENTFLOW_LOCAL_PORT to another port."
}
if (Test-PortListening) {
    Fail "Port $Port is occupied, but it is not FluentFlow Local. Stop that application or set FLUENTFLOW_LOCAL_PORT to another port."
}

$PythonCandidates = @(
    (Join-Path $Repo "venv\\Scripts\\python.exe"),
    (Join-Path $Repo ".venv\\Scripts\\python.exe")
)
$Python = $PythonCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Python) {
    Fail "Python virtual environment was not found. In PowerShell, run:`ncd $Repo`npy -3 -m venv venv`n.\\venv\\Scripts\\python.exe -m pip install -r requirements-local.txt"
}

Write-Host "== FluentFlow Local startup check =="
& $Python (Join-Path $Repo "scripts\\check_local_readiness.py")
if ($LASTEXITCODE -ne 0) {
    Fail "The environment is not ready. Follow the messages above, then launch again."
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Add-Content -Path $LogFile -Value "---- $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') starting backend.local_main:app on :$Port ----"

Set-Location $Repo
Start-Job -ScriptBlock {
    param($ProbePort, $ProbeUrl, $ProbeLog)
    for ($attempt = 0; $attempt -lt 80; $attempt++) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$ProbePort/health" -TimeoutSec 1 | Out-Null
            Start-Process $ProbeUrl
            return
        } catch {
            Start-Sleep -Milliseconds 250
        }
    }
    Add-Content -Path $ProbeLog -Value "Service did not listen on port $ProbePort within 20 seconds."
} -ArgumentList $Port, $AppUrl, $LogFile | Out-Null

Write-Host ""
Write-Host "FluentFlow Local is starting: $AppUrl"
Write-Host "Log file: $LogFile"
Write-Host "To stop: press Ctrl+C in this window, or close the window."
Write-Host ""

# Let python-dotenv in backend.local_main parse .env; PowerShell must not
# source it because dotenv files may contain comments, spaces, or non-shell syntax.
& $Python -m uvicorn backend.local_main:app --host 127.0.0.1 --port $Port 2>&1 | Tee-Object -FilePath $LogFile -Append
exit $LASTEXITCODE
