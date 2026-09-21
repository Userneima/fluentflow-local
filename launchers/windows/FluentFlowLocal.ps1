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

function Test-AppPageServes {
    # /health answering is not the same as the app being usable. A process that
    # had run for eleven days kept returning 200 on /health while serving the
    # page as headers only — Content-Length set, zero bytes of body — so the
    # launcher reported "already running" and opened a blank window. Probe the
    # address the browser will actually load.
    try {
        $page = Invoke-WebRequest -UseBasicParsing -Uri $AppUrl -TimeoutSec 5
        return ($page.StatusCode -eq 200 -and $page.RawContentLength -gt 0)
    } catch {
        return $false
    }
}

function Get-PortOwnerIds {
    try {
        return @(Get-NetTCPConnection -LocalPort ([int]$Port) -State Listen -ErrorAction Stop |
            Select-Object -ExpandProperty OwningProcess -Unique)
    } catch {
        return @()
    }
}

# Stopping the listener is not enough on this machine: a venv interpreter
# re-execs the base interpreter, so the process holding the port is a child of
# the one the previous launcher started, and killing only the child leaves the
# parent behind. These two walk up to the outermost FluentFlow python and take
# the whole tree.
function Test-FluentFlowProcess($Process) {
    return $Process -and $Process.CommandLine -and $Process.CommandLine -match "backend\.local_main"
}

function Get-FluentFlowServerProcess {
    $owners = @(
        Get-NetTCPConnection -LocalPort ([int]$Port) -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
    if (-not $owners) { return @() }

    $byId = @{}
    foreach ($item in Get-CimInstance Win32_Process -ErrorAction SilentlyContinue) {
        $byId[[int]$item.ProcessId] = $item
    }

    $selected = @{}
    foreach ($owner in $owners) {
        $process = $byId[[int]$owner]
        if (-not (Test-FluentFlowProcess $process)) { return $null }
        while ($true) {
            $parent = $byId[[int]$process.ParentProcessId]
            if (Test-FluentFlowProcess $parent) { $process = $parent } else { break }
        }
        $pending = @($process)
        while ($pending.Count -gt 0) {
            $current = $pending[0]
            $pending = @($pending | Select-Object -Skip 1)
            $selected[[int]$current.ProcessId] = $current
            foreach ($candidate in $byId.Values) {
                if ([int]$candidate.ParentProcessId -eq [int]$current.ProcessId -and (Test-FluentFlowProcess $candidate)) {
                    $pending += $candidate
                }
            }
        }
    }
    return @($selected.Values)
}

function Get-RunningJobCount {
    try {
        $jobs = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/jobs?limit=100" -TimeoutSec 3
        return @($jobs.jobs | Where-Object { $_.status -in @("queued", "processing", "running", "pending") }).Count
    } catch {
        # The instance cannot answer for itself, so there is nothing worth keeping.
        return 0
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
        if (Test-AppPageServes) {
            Write-Host "FluentFlow Local is already running. Opening the workspace."
            Start-Process $AppUrl
            exit 0
        }
        Write-Host ""
        Write-Host "! FluentFlow Local on port $Port still answers its health check but no longer serves the page." -ForegroundColor Yellow
        Write-Host "  This is usually a process that has been running too long. Restarting it." -ForegroundColor Yellow
        Write-Host ""
        $ownerIds = Get-PortOwnerIds
        if ($ownerIds.Count -eq 0) {
            Fail "Could not identify the process holding port $Port. Stop it manually and try again."
        }
        # Double-clicking the launcher is already the decision to use the app, so a
        # broken instance is restarted without asking. The one case worth stopping
        # for is real work in flight: a dead interface does not mean a dead job, and
        # a three-hour transcription killed halfway starts over from nothing.
        $running = Get-RunningJobCount
        if ($running -gt 0) {
            Write-Host "$running job(s) are still in progress; restarting makes them start over."
            Read-Host "Press Enter to restart anyway, or Ctrl+C to cancel" | Out-Null
        }
        $tree = Get-FluentFlowServerProcess
        if ($null -ne $tree -and $tree.Count -gt 0) {
            foreach ($process in ($tree | Sort-Object ProcessId -Descending)) {
                if ([int]$process.ProcessId -eq $PID) { continue }
                Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
            }
        } else {
            foreach ($id in $ownerIds) { Stop-Process -Id $id -ErrorAction SilentlyContinue }
        }
        for ($i = 0; $i -lt 40; $i++) {
            if (-not (Get-LocalHealth)) { break }
            Start-Sleep -Milliseconds 250
        }
        if (Get-LocalHealth) {
            Fail "The old process did not exit (PID: $($ownerIds -join ', ')). End it manually and launch again."
        }
        Write-Host "Old process stopped. Starting a fresh one."
    } else {
        Fail "Port $Port is already serving a different application. Stop that application or set FLUENTFLOW_LOCAL_PORT to another port."
    }
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
