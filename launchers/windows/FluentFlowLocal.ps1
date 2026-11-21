# FluentFlow Local launcher (Windows).
#
# Double-click FluentFlowLocal.cmd. It checks the environment, starts the
# service in the background (Start-FluentFlowService: closing this window does
# not stop it), opens the page and exits. Stop it with 「停止 FluentFlow Local」
# on the Desktop (stop-local.ps1). It deliberately starts
# backend.local_main:app, never the hosted application entry.
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = if ($env:FLUENTFLOW_REPO) { $env:FLUENTFLOW_REPO } else { Split-Path -Parent (Split-Path -Parent $ScriptDir) }
# The port follows the environment, then the repository's .env, else 8000:
# the rule the backend uses (fluentflow-env.ps1), so the launcher probes the
# port the service actually listens on.
. (Join-Path $ScriptDir "fluentflow-env.ps1")
$Port = Get-FluentFlowPort $Repo
$AppUrl = "http://127.0.0.1:$Port/"
$LocalAppData = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $env:USERPROFILE "AppData\Local" }
$LogDir = Join-Path $LocalAppData "FluentFlow\Logs"
$LogFile = Join-Path $LogDir "local.log"

function Fail([string]$Message) {
    Write-Host ""
    Write-Host "x $Message" -ForegroundColor Red
    Write-Host ""
    Read-Host "按回车键关闭" | Out-Null
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

if (-not (Test-Path (Join-Path $Repo "backend\local_main.py"))) {
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
        foreach ($id in $ownerIds) { Stop-Process -Id $id -ErrorAction SilentlyContinue }
        for ($i = 0; $i -lt 40; $i++) {
            if (-not (Get-LocalHealth)) { break }
            Start-Sleep -Milliseconds 250
        }
        if (Get-LocalHealth) {
            Fail "The old process did not exit (PID: $($ownerIds -join ', ')). End it manually and launch again."
        }
        Write-Host "Old process stopped. Starting a fresh one."
    } else {
        Fail "端口 $Port 上运行的是别的程序。请先退出它，或设置 FLUENTFLOW_LOCAL_PORT 换一个端口。"
    }
}
if (Test-PortListening) {
    Fail "端口 $Port 被占用，占用的不是 FluentFlow Local。请先退出那个程序，或设置 FLUENTFLOW_LOCAL_PORT 换一个端口。"
}

$PythonCandidates = @(
    (Join-Path $Repo "venv\Scripts\python.exe"),
    (Join-Path $Repo ".venv\Scripts\python.exe")
)
$Python = $PythonCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
$SetupScript = Join-Path $Repo "launchers\windows\setup-local.ps1"
if (-not $Python) {
    Fail "没找到 Python 虚拟环境。先在 PowerShell 里运行安装脚本：`npowershell -ExecutionPolicy Bypass -File `"$SetupScript`""
}

# A virtual environment does not survive every move: after a friend moved the
# folder to drive D the environment no longer started and had to be rebuilt by
# hand (2026-11). When it cannot import what the service needs, it is rebuilt
# here (it holds nothing but packages; the transcription model is not touched).
function Test-VenvWorks {
    try {
        & $Python -c "import fastapi, uvicorn, faster_whisper" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}
if (-not (Test-VenvWorks)) {
    Write-Host "Python 环境用不了了（多半是文件夹换过位置），正在重建，需要几分钟…" -ForegroundColor Yellow
    Remove-Item -Recurse -Force (Split-Path -Parent (Split-Path -Parent $Python)) -ErrorAction SilentlyContinue
    & powershell -NoProfile -ExecutionPolicy Bypass -File $SetupScript -SkipModel
    $Python = Join-Path $Repo ".venv\Scripts\python.exe"
    if ($LASTEXITCODE -ne 0 -or -not (Test-VenvWorks)) {
        Fail "重建 Python 环境没有成功，请看上面的提示，处理后重新运行：`npowershell -ExecutionPolicy Bypass -File `"$SetupScript`""
    }
}

# Add PATH as the registry has it now after what this process already had. The
# installer starts this launcher right after setup may have installed FFmpeg or
# Python with winget; this process inherits the installer's PATH from before
# that, so without this the startup check reports FFmpeg missing on every fresh
# Windows machine and the window waits on "press Enter". The inherited entries
# stay first, so an FFmpeg the user put ahead on PATH is still the one used.
$env:Path = @(
    $env:Path,
    [Environment]::GetEnvironmentVariable("Path", "Machine"),
    [Environment]::GetEnvironmentVariable("Path", "User")
) -join ";"

Write-Host "== FluentFlow Local startup check =="
# Python's UTF-8 mode, for everything started from here. Without it every text
# read that names no encoding uses the code page, GBK on a Chinese Windows:
# ffmpeg, ffprobe and yt-dlp write UTF-8, and a Chinese file name or video title
# in their output stopped the read that was waiting for it.
$env:PYTHONUTF8 = "1"

& $Python (Join-Path $Repo "scripts\check_local_readiness.py")
if ($LASTEXITCODE -ne 0) {
    Fail "The environment is not ready. Follow the messages above, then launch again."
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
# The log is UTF-8, written a line at a time and never held open, so it can be
# read while the service runs. Tee-Object wrote UTF-16 in Windows PowerShell 5.1,
# beside Add-Content's ANSI lines in the same file, and kept it locked. Past 5 MB,
# or when it is still one of those UTF-16 logs, it is moved aside and one
# previous generation is kept.
$Utf8 = New-Object System.Text.UTF8Encoding $false
function Write-LogLine([string]$Line) {
    [System.IO.File]::AppendAllText($LogFile, $Line + [Environment]::NewLine, $Utf8)
}
if (Test-Path $LogFile) {
    # Tee-Object appended UTF-16 without a byte order mark after an ANSI first
    # line, so the old logs are told apart by the NUL bytes UTF-16 text has.
    $Head = [byte[]](Get-Content -Path $LogFile -Encoding Byte -TotalCount 4096)
    $Utf16 = $Head -contains 0
    if ($Utf16 -or (Get-Item $LogFile).Length -gt 5MB) {
        Move-Item -Force -Path $LogFile -Destination "$LogFile.1"
    }
}
Write-LogLine "---- $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') starting backend.local_main:app on :$Port ----"

# Upgrade yt-dlp at most once per day. Platforms change their pages often and an
# old yt-dlp fails a whole platform with no way for the user to update it. It
# runs as a separate hidden process so an offline start is not delayed and so
# closing this window does not kill pip halfway through an install. The backend
# starts a fresh yt-dlp process per download, so the next download uses it.
try {
    Start-Process -FilePath $Python `
        -ArgumentList @("`"$(Join-Path $Repo 'scripts\update_yt_dlp.py')`"") `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir "yt-dlp-update.log") `
        -RedirectStandardError (Join-Path $LogDir "yt-dlp-update.err.log") | Out-Null
} catch {
    Write-Host "没能启动 yt-dlp 更新检查：$_" -ForegroundColor Yellow
}

Set-Location $Repo
try {
    $ServicePid = Start-FluentFlowService $Repo $Python $Port $LogFile
} catch {
    Fail "$_"
}
Write-Host "FluentFlow Local 正在后台启动（进程 $ServicePid）…"
for ($attempt = 0; $attempt -lt 90 -and -not (Test-FluentFlowHealthy $Port); $attempt++) { Start-Sleep -Seconds 1 }
if (-not (Test-FluentFlowHealthy $Port)) {
    Fail "服务没有在 90 秒内启动。日志：$LogFile"
}
Start-Process $AppUrl
Write-Host ""
Write-Host "FluentFlow Local 在后台运行：$AppUrl"
Write-Host "关掉这个窗口不影响它。要停止，双击桌面上的「停止 FluentFlow Local」。"
Write-Host "日志文件：$LogFile"
exit 0
