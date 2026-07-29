# Create a Desktop shortcut command that keeps the current checkout path.
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$Launcher = Join-Path $ScriptDir "FluentFlowLocal.cmd"
$Desktop = [Environment]::GetFolderPath("Desktop")
$Destination = Join-Path $Desktop "FluentFlow Local.cmd"

if (-not (Test-Path $Launcher)) {
    throw "Cannot find $Launcher"
}

$Content = "@echo off`r`nset `"FLUENTFLOW_REPO=$Repo`"`r`ncall `"$Launcher`"`r`n"
[System.IO.File]::WriteAllText($Destination, $Content, [System.Text.UTF8Encoding]::new($false))
Write-Host "Installed: $Destination"
Start-Process explorer.exe -ArgumentList "/select,`"$Destination`""
