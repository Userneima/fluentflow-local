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
