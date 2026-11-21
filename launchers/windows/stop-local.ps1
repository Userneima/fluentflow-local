# Stop FluentFlow Local, which runs in the background (FluentFlowLocal.ps1
# starts it with Start-FluentFlowService). Double-click 「停止 FluentFlow Local」
# on the Desktop, or run this file. Work in progress is named first and asked
# about; -Yes stops without asking.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\launchers\windows\stop-local.ps1 [-Yes]
param([switch]$Yes)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = if ($env:FLUENTFLOW_REPO) { $env:FLUENTFLOW_REPO } else { Split-Path -Parent (Split-Path -Parent $ScriptDir) }
. (Join-Path $ScriptDir "fluentflow-env.ps1")
$Port = Get-FluentFlowPort $Repo

if (-not (Test-FluentFlowHealthy $Port)) {
    Write-Host "FluentFlow Local 没有在运行（端口 $Port）。"
    exit 0
}
try {
    $Jobs = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/jobs?limit=100" -TimeoutSec 3
    $Busy = @($Jobs.jobs | Where-Object { $_.status -in @("queued", "processing", "running", "pending") }).Count
} catch { $Busy = 0 }
if ($Busy -gt 0 -and -not $Yes) {
    $Reply = Read-Host "还有 $Busy 个任务在处理，停止会让它们中断（下次启动时接着做）。仍然停止？[y/N]"
    if ($Reply -notmatch '^[Yy]') { Write-Host "没有停止。"; exit 0 }
}
Get-NetTCPConnection -LocalPort ([int]$Port) -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
for ($i = 0; $i -lt 20 -and (Test-FluentFlowHealthy $Port); $i++) { Start-Sleep -Milliseconds 250 }
if (Test-FluentFlowHealthy $Port) { Write-Host "没能停下来，请在任务管理器里结束 python.exe。"; exit 1 }
Write-Host "FluentFlow Local 已停止。"
