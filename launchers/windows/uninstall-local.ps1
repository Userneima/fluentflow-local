# Uninstall FluentFlow Local from this Windows machine.
#
# Three layers, because "uninstall" means three very different things here:
#   Program (always): virtual environment, frontend dependencies and build,
#       the Desktop launcher, logs. Reinstalling brings all of it back.
#   Models (-Models): several gigabytes of transcription weights. They live in
#       the shared Hugging Face cache, so only the directories this product
#       downloads are removed, and that list comes from the code rather than
#       from a name pattern.
#   Data (-Data): task history, transcripts, notes, media. Not recoverable,
#       so it takes its own switch and a second confirmation.
#
# FFmpeg, Node.js and Python are left alone: they are machine-wide tools other
# programs are likely to be using.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\launchers\windows\uninstall-local.ps1
#   ... -Models      also remove the transcription models
#   ... -Data        also remove your task data
#   ... -All         remove all three
#   ... -All -DryRun show what would go, delete nothing
param(
    [switch]$Models,
    [switch]$Data,
    [switch]$All,
    [switch]$DryRun,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"

if ($All) { $Models = $true; $Data = $true }

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent (Split-Path -Parent $ScriptDir)
# Port: the FLUENTFLOW_LOCAL_PORT environment variable first, then the same line
# in the repository's .env, else 8000 -- the order the macOS launcher and the
# backend use, so the launcher probes the port the service actually listens on.
function Get-EnvFilePort([string]$RepoDir) {
    $EnvFile = Join-Path $RepoDir ".env"
    if (-not (Test-Path $EnvFile)) { return $null }
    $Line = Get-Content $EnvFile -ErrorAction SilentlyContinue |
        Where-Object { $_ -match '^\s*(export\s+)?FLUENTFLOW_LOCAL_PORT\s*=' } | Select-Object -Last 1
    if (-not $Line) { return $null }
    $Value = (($Line -split '=', 2)[1] -split '\s#')[0].Trim().Trim('"').Trim("'")
    if ($Value -match '^\d+$') { return $Value }
    return $null
}
$Port = if ($env:FLUENTFLOW_LOCAL_PORT) { $env:FLUENTFLOW_LOCAL_PORT } elseif (Get-EnvFilePort $Repo) { Get-EnvFilePort $Repo } else { "8000" }

if (-not (Test-Path (Join-Path $Repo "backend\local_main.py"))) {
    throw "在 ${Repo} 下找不到 backend\local_main.py，这不像 FluentFlow Local 代码目录。"
}

function Get-FolderSize([string]$Path) {
    try {
        if (Test-Path -PathType Leaf $Path) {
            return (Get-Item $Path).Length
        }
        return (Get-ChildItem $Path -Recurse -Force -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
    } catch {
        return 0
    }
}

function Format-Size([double]$Bytes) {
    if ($Bytes -ge 1GB) { return "{0:N1} GB" -f ($Bytes / 1GB) }
    if ($Bytes -ge 1MB) { return "{0:N0} MB" -f ($Bytes / 1MB) }
    return "{0:N0} KB" -f ($Bytes / 1KB)
}

# The service holds files this script is about to delete, so stop it first.
Write-Host "`n== 检查服务是否在运行 =="
$Health = $null
try {
    $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
} catch {
    $Health = $null
}

# /health says it is the local edition under runtime.execution (and edition);
# reading a top-level "execution" never matched, so a running service went
# unnoticed and its environment was deleted under it.
if ($Health -and ($Health.edition -eq "local" -or $Health.runtime.execution -eq "local")) {
    Write-Host "FluentFlow Local 正在端口 ${Port} 上运行。"
    try {
        $Jobs = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/jobs?limit=100" -TimeoutSec 3
        $Busy = @($Jobs.jobs | Where-Object { $_.status -in @("queued", "processing", "running", "pending") }).Count
        if ($Busy -gt 0) {
            Write-Host "注意：后台还有 ${Busy} 个任务在进行中，停止会让它们中断。"
        }
    } catch {
        # A service that cannot answer its own job list is not worth protecting.
    }
    if ($DryRun) {
        Write-Host "（-DryRun：不会真的停止它）"
    } else {
        if (-not $Yes) {
            $Reply = Read-Host "停止它并继续卸载？[y/N]"
            if ($Reply -notmatch '^[Yy]') { throw "已取消，什么都没有删除。" }
        }
        Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 2
        Write-Host "已停止。"
    }
} else {
    Write-Host "服务没有在运行。"
}

# Ask the code where the data lives: FLUENTFLOW_DATA_DIR can move it.
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = Join-Path $Repo "venv\Scripts\python.exe" }

$DataRoot = $null
if (Test-Path $Python) {
    try {
        $Output = & $Python -c "from backend.core.runtime_paths import app_data_root; print(app_data_root())" 2>$null
        $Candidate = ($Output | Select-Object -Last 1)
        if ($Candidate -and (Split-Path -IsAbsolute $Candidate)) { $DataRoot = $Candidate.Trim() }
    } catch {
        $DataRoot = $null
    }
}
if (-not $DataRoot) {
    $DataRoot = if ($env:FLUENTFLOW_DATA_DIR) { $env:FLUENTFLOW_DATA_DIR } else { Join-Path $env:APPDATA "FluentFlow" }
    Write-Host "`n（问不到代码里的数据目录，按默认位置处理：${DataRoot}）"
}

$ModelDirs = @()
if ($Models) {
    if (Test-Path $Python) {
        try {
            $ModelDirs = @(& $Python (Join-Path $Repo "scripts\stt_model.py") "cache-paths" 2>$null |
                Where-Object { $_ -and (Test-Path $_) })
        } catch {
            $ModelDirs = @()
        }
    }
    if ($ModelDirs.Count -eq 0) {
        Write-Host "`n（列不出模型目录：虚拟环境可能已经不可用。模型通常在"
        Write-Host "  %USERPROFILE%\.cache\huggingface\hub\ 下以 models--mlx-community--whisper-"
        Write-Host "  和 models--Systran--faster-whisper- 开头的目录里，可自行确认后删除。）"
    }
}

$Targets = New-Object System.Collections.ArrayList
function Add-Target([string]$Path, [string]$Label, [bool]$Sensitive = $false) {
    if ($Path -and (Test-Path $Path)) {
        [void]$Targets.Add([pscustomobject]@{ Path = $Path; Label = $Label; Sensitive = $Sensitive })
    }
}

Add-Target (Join-Path $Repo ".venv") "Python 虚拟环境"
Add-Target (Join-Path $Repo "venv") "Python 虚拟环境（旧位置）"
Add-Target (Join-Path $Repo "node_modules") "前端依赖"
Add-Target (Join-Path $Repo "frontend\dist-local") "前端构建产物"
Add-Target (Join-Path ([Environment]::GetFolderPath("Desktop")) "FluentFlow Local.cmd") "桌面启动器"
Add-Target (Join-Path $env:LOCALAPPDATA "FluentFlow\Logs") "日志"
foreach ($Dir in $ModelDirs) { Add-Target $Dir "转录模型" }
if ($Data) { Add-Target $DataRoot "任务历史、转录稿、笔记、媒体文件" $true }

if ($Targets.Count -eq 0) {
    Write-Host "`n✓ 没有找到需要删除的东西。"
    exit 0
}

Write-Host "`n== 将要删除 =="
$HasSensitive = $false
foreach ($Target in $Targets) {
    Write-Host ("  {0,-10} {1}" -f (Format-Size (Get-FolderSize $Target.Path)), $Target.Path)
    Write-Host ("             {0}" -f $Target.Label)
    if ($Target.Sensitive) { $HasSensitive = $true }
}

if ($DryRun) {
    Write-Host "`n（-DryRun：以上都没有真的删除。）"
    exit 0
}

Write-Host ""
if ($HasSensitive) {
    Write-Host "注意：其中包含你的任务数据，删掉之后无法恢复。"
    if (-not $Yes) {
        $Reply = Read-Host "确认请输入 DELETE（其他任何输入都会取消）"
        if ($Reply -ne "DELETE") { throw "已取消，什么都没有删除。" }
    }
} elseif (-not $Yes) {
    $Reply = Read-Host "确认删除以上内容？[y/N]"
    if ($Reply -notmatch '^[Yy]') { throw "已取消，什么都没有删除。" }
}

Write-Host "`n== 删除 =="
foreach ($Target in $Targets) {
    Remove-Item -Recurse -Force -LiteralPath $Target.Path
    Write-Host "已删除：$($Target.Path)"
}

Write-Host "`n== 还剩下 =="
if ((-not $Data) -and (Test-Path $DataRoot)) {
    Write-Host "  你的任务数据（$(Format-Size (Get-FolderSize $DataRoot))）：${DataRoot}"
    Write-Host "    要一并删除：.\launchers\windows\uninstall-local.ps1 -Data"
}
if (-not $Models) {
    Write-Host "  转录模型（通常几 GB）：在 Hugging Face 缓存里"
    Write-Host "    要一并删除：.\launchers\windows\uninstall-local.ps1 -Models"
}
Write-Host "  代码目录：${Repo}"
Write-Host "    本脚本就在里面，所以留给你自己删。"
Write-Host ""
Write-Host "  没有动 FFmpeg、Node.js 和 Python，它们是系统工具，"
Write-Host "  这台机器上别的程序可能也在用。"
Write-Host "`n✓ 卸载完成。"
