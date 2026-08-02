# FluentFlow Local launcher (Windows).
#
# Double-click FluentFlowLocal.cmd. This console window is the local service:
# press Ctrl+C or close it to stop FluentFlow Local. It deliberately starts
# backend.local_main:app, never the hosted application entry.
#
# Launching while an instance is already up RESTARTS it, so double-clicking the
# desktop shortcut is how you pick up new code. Force-closing the console window
# does not always run this script's cleanup, which used to strand a server on
# the port with no way back except Task Manager.
#
# The one thing a restart must never do is kill work in progress: a
# transcription can be hours long, and the startup sweep marks any interrupted
# job failed. So a running or queued job blocks the restart unless -Force.
[CmdletBinding()]
param(
    # Attach to a running instance instead of restarting it.
    [switch]$Reuse,
    # Restart even while a job is running. That job is lost.
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = if ($env:FLUENTFLOW_REPO) { $env:FLUENTFLOW_REPO } else { Split-Path -Parent (Split-Path -Parent $ScriptDir) }
$Port = if ($env:FLUENTFLOW_LOCAL_PORT) { $env:FLUENTFLOW_LOCAL_PORT } else { "8000" }
$AppUrl = "http://127.0.0.1:$Port/"
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

# Every process this script is willing to kill must prove it is a FluentFlow
# server: a Python interpreter whose command line starts backend.local_main.
# Anything else on the port is someone else's and stays untouched.
function Test-FluentFlowProcess($Process) {
    return $Process -and $Process.CommandLine -and $Process.CommandLine -match "backend\.local_main"
}

# Returns the FluentFlow processes holding the port, $null when the port is held
# by something that is not ours, or an empty array when nothing is listening.
#
# A venv interpreter re-execs the base interpreter on this machine, so the
# listener is a child of the process the previous launcher started. Killing only
# the listener leaves the parent behind, so walk up to the outermost FluentFlow
# python and take the whole tree.
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

function Stop-FluentFlowServer {
    $processes = Get-FluentFlowServerProcess
    if ($null -eq $processes) { return $false }
    foreach ($process in ($processes | Sort-Object ProcessId -Descending)) {
        if ([int]$process.ProcessId -eq $PID) { continue }
        try { Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop } catch { }
    }
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-PortListening)) { return $true }
        Start-Sleep -Milliseconds 200
    }
    return $false
}

# Asked of the job store rather than the HTTP API: the store answers for every
# client scope, and this has to be right before anything gets killed.
function Get-ActiveJobCount {
    $probe = @'
try:
    from backend.core.job_store import list_jobs_by_statuses
    print(len(list_jobs_by_statuses(("queued", "running"))))
except Exception:
    print("unknown")
'@
    Push-Location $Repo
    try {
        $output = ($probe | & $Python - | Select-Object -Last 1)
    } catch {
        return $null
    } finally {
        Pop-Location
    }
    $count = 0
    if ([int]::TryParse(("$output").Trim(), [ref]$count)) { return $count }
    return $null
}

if (-not (Test-Path (Join-Path $Repo "backend\\local_main.py"))) {
    Fail "Cannot find the FluentFlow code directory (looked in: $Repo). Set FLUENTFLOW_REPO to the code directory, or run install-local-to-desktop.ps1 again."
}

$PythonCandidates = @(
    (Join-Path $Repo "venv\\Scripts\\python.exe"),
    (Join-Path $Repo ".venv\\Scripts\\python.exe")
)
$Python = $PythonCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Python) {
    Fail "Python virtual environment was not found. In PowerShell, run:`ncd $Repo`npy -3 -m venv venv`n.\\venv\\Scripts\\python.exe -m pip install -r requirements-local.txt"
}

$health = Get-LocalHealth
# /health nests the edition marker under "runtime"; reading it from the top
# level made a healthy running instance look like a foreign process.
$OwnInstanceIsHealthy = $health -and $health.runtime -and $health.runtime.execution -eq "local"
if ($health -and -not $OwnInstanceIsHealthy) {
    Fail "Port $Port is already serving a different application. Stop that application or set FLUENTFLOW_LOCAL_PORT to another port."
}

if ($OwnInstanceIsHealthy -and $Reuse) {
    Write-Host "FluentFlow Local is already running. Opening the workspace."
    Start-Process $AppUrl
    exit 0
}

if ($OwnInstanceIsHealthy -or (Test-PortListening)) {
    # An unhealthy listener still gets restarted when it is provably ours: that
    # is the stranded-server case this whole block exists for.
    $existing = Get-FluentFlowServerProcess
    if ($null -eq $existing -or $existing.Count -eq 0) {
        Fail "Port $Port is occupied, but it is not FluentFlow Local. Stop that application or set FLUENTFLOW_LOCAL_PORT to another port."
    }

    if (-not $Force) {
        $active = Get-ActiveJobCount
        if ($null -eq $active) {
            Write-Host "Could not check for running tasks; not restarting. Opening the workspace." -ForegroundColor Yellow
            Write-Host "Restart anyway with: FluentFlowLocal.cmd -Force" -ForegroundColor Yellow
            Start-Process $AppUrl
            exit 0
        }
        if ($active -gt 0) {
            Write-Host "FluentFlow Local is busy with $active task(s); leaving it running." -ForegroundColor Yellow
            Write-Host "Restarting now would fail them. Wait for them to finish, or force it with: FluentFlowLocal.cmd -Force" -ForegroundColor Yellow
            Start-Process $AppUrl
            exit 0
        }
    }

    Write-Host "Restarting FluentFlow Local (stopping PID $((($existing | Sort-Object ProcessId) | ForEach-Object { $_.ProcessId }) -join ', '))..."
    if (-not (Stop-FluentFlowServer)) {
        Fail "Could not free port $Port. Close the old FluentFlow window, then launch again."
    }
}

Write-Host "== FluentFlow Local startup check =="
& $Python (Join-Path $Repo "scripts\\check_local_readiness.py")
if ($LASTEXITCODE -ne 0) {
    Fail "The environment is not ready. Follow the messages above, then launch again."
}

Write-Host ""
Write-Host "FluentFlow Local is starting: $AppUrl"
Write-Host "To stop: press Ctrl+C in this window, or close the window."
Write-Host ""

# Start the server separately so this foreground PowerShell session can wait for
# health and invoke the browser in the active desktop session. Start-Job uses an
# isolated background process, where URL activation is not reliable.
$ServerArguments = @("-m", "uvicorn", "backend.local_main:app", "--host", "127.0.0.1", "--port", $Port)
$Server = $null
$ExitCode = 1

try {
    $Server = Start-Process -FilePath $Python -ArgumentList $ServerArguments -WorkingDirectory $Repo -NoNewWindow -PassThru
    $deadline = (Get-Date).AddSeconds(20)

    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 250
        $health = Get-LocalHealth
        if ($health -and $health.runtime -and $health.runtime.execution -eq "local") {
            try {
                Start-Process $AppUrl
                Write-Host "Opened FluentFlow Local in your default browser."
            } catch {
                Write-Host "The local service is ready, but the browser could not be opened automatically." -ForegroundColor Yellow
                Write-Host "Open this address manually: $AppUrl" -ForegroundColor Yellow
            }

            Wait-Process -Id $Server.Id
            $Server.Refresh()
            $ExitCode = $Server.ExitCode
            exit $ExitCode
        }

        $Server.Refresh()
        if ($Server.HasExited) {
            Fail "FluentFlow Local exited before it became ready (exit code $($Server.ExitCode)). Run this launcher from PowerShell to inspect the server output."
        }
    }

    Fail "FluentFlow Local did not become ready within 20 seconds. Run this launcher from PowerShell to inspect the server output."
}
finally {
    # Closing the launcher window or pressing Ctrl+C also stops the child server.
    if ($Server -and -not $Server.HasExited) {
        Stop-Process -Id $Server.Id -Force
    }
}

exit $ExitCode
