# Create a Desktop shortcut command that keeps the current checkout path.
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$Launcher = Join-Path $ScriptDir "FluentFlowLocal.cmd"
$Desktop = [Environment]::GetFolderPath("Desktop")
$Destination = Join-Path $Desktop "FluentFlow Local.cmd"

if (-not (Test-Path $Launcher)) {
    throw "找不到 ${Launcher}"
}

$Content = "@echo off`r`nset `"FLUENTFLOW_REPO=$Repo`"`r`ncall `"$Launcher`"`r`n"
[System.IO.File]::WriteAllText($Destination, $Content, [System.Text.UTF8Encoding]::new($false))
Write-Host "已安装：${Destination}"

# The service runs in the background, so stopping it gets its own icon.
# Pure ASCII inside the .cmd: cmd reads it in the console's code page.
$StopScript = Join-Path $ScriptDir "stop-local.ps1"
$StopDestination = Join-Path $Desktop "停止 FluentFlow Local.cmd"
$StopContent = "@echo off`r`nset `"FLUENTFLOW_REPO=$Repo`"`r`npowershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$StopScript`"`r`npause`r`n"
[System.IO.File]::WriteAllText($StopDestination, $StopContent, [System.Text.UTF8Encoding]::new($false))
Write-Host "已安装：${StopDestination}"
Start-Process explorer.exe -ArgumentList "/select,`"$Destination`""
