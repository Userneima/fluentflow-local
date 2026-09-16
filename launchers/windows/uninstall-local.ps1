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
$Port = if ($env:FLUENTFLOW_LOCAL_PORT) { $env:FLUENTFLOW_LOCAL_PORT } else { "8000" }

if (-not (Test-Path (Join-Path $Repo "backend\local_main.py"))) {
    throw "backend\local_main.py was not found under $Repo; this does not look like the FluentFlow Local checkout."
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
Write-Host "`n== Checking whether the service is running =="
$Health = $null
try {
    $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
} catch {
    $Health = $null
}

if ($Health -and $Health.execution -eq "local") {
    Write-Host "FluentFlow Local is running on port $Port."
    try {
        $Jobs = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/jobs?limit=100" -TimeoutSec 3
        $Busy = @($Jobs | Where-Object { $_.status -in @("queued", "processing", "running", "pending") }).Count
        if ($Busy -gt 0) {
            Write-Host "WARNING: $Busy job(s) are still running; stopping now interrupts them."
        }
    } catch {
        # A service that cannot answer its own job list is not worth protecting.
    }
    if ($DryRun) {
        Write-Host "(-DryRun: it will not actually be stopped)"
    } else {
        if (-not $Yes) {
            $Reply = Read-Host "Stop it and continue uninstalling? [y/N]"
            if ($Reply -notmatch '^[Yy]') { throw "Cancelled. Nothing was deleted." }
        }
        Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 2
        Write-Host "Stopped."
    }
} else {
    Write-Host "The service is not running."
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
    Write-Host "`n(Could not ask the code where data lives; using the default: $DataRoot)"
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
        Write-Host "`n(Could not list the model directories: the virtual environment may already be gone."
        Write-Host " They are usually under %USERPROFILE%\.cache\huggingface\hub\ in directories starting"
        Write-Host " with models--mlx-community--whisper- or models--Systran--faster-whisper-.)"
    }
}

$Targets = New-Object System.Collections.ArrayList
function Add-Target([string]$Path, [string]$Label, [bool]$Sensitive = $false) {
    if ($Path -and (Test-Path $Path)) {
        [void]$Targets.Add([pscustomobject]@{ Path = $Path; Label = $Label; Sensitive = $Sensitive })
    }
}

Add-Target (Join-Path $Repo ".venv") "Python virtual environment"
Add-Target (Join-Path $Repo "venv") "Python virtual environment (old location)"
Add-Target (Join-Path $Repo "node_modules") "Frontend dependencies"
Add-Target (Join-Path $Repo "frontend\dist-local") "Frontend build output"
Add-Target (Join-Path ([Environment]::GetFolderPath("Desktop")) "FluentFlow Local.cmd") "Desktop launcher"
Add-Target (Join-Path $env:LOCALAPPDATA "FluentFlow\Logs") "Logs"
foreach ($Dir in $ModelDirs) { Add-Target $Dir "Transcription model" }
if ($Data) { Add-Target $DataRoot "Task history, transcripts, notes, media" $true }

if ($Targets.Count -eq 0) {
    Write-Host "`nNothing left to remove."
    exit 0
}

Write-Host "`n== Will be deleted =="
$HasSensitive = $false
foreach ($Target in $Targets) {
    Write-Host ("  {0,-10} {1}" -f (Format-Size (Get-FolderSize $Target.Path)), $Target.Path)
    Write-Host ("             {0}" -f $Target.Label)
    if ($Target.Sensitive) { $HasSensitive = $true }
}

if ($DryRun) {
    Write-Host "`n(-DryRun: nothing above was actually deleted.)"
    exit 0
}

Write-Host ""
if ($HasSensitive) {
    Write-Host "WARNING: this includes your task data. It cannot be recovered."
    if (-not $Yes) {
        $Reply = Read-Host "Type DELETE to confirm (anything else cancels)"
        if ($Reply -ne "DELETE") { throw "Cancelled. Nothing was deleted." }
    }
} elseif (-not $Yes) {
    $Reply = Read-Host "Delete everything listed above? [y/N]"
    if ($Reply -notmatch '^[Yy]') { throw "Cancelled. Nothing was deleted." }
}

Write-Host "`n== Deleting =="
foreach ($Target in $Targets) {
    Remove-Item -Recurse -Force -LiteralPath $Target.Path
    Write-Host "Removed: $($Target.Path)"
}

Write-Host "`n== Still here =="
if ((-not $Data) -and (Test-Path $DataRoot)) {
    Write-Host "  Your task data ($(Format-Size (Get-FolderSize $DataRoot))): $DataRoot"
    Write-Host "    To remove it too: .\launchers\windows\uninstall-local.ps1 -Data"
}
if (-not $Models) {
    Write-Host "  Transcription models (usually several GB): in the Hugging Face cache"
    Write-Host "    To remove them too: .\launchers\windows\uninstall-local.ps1 -Models"
}
Write-Host "  The checkout: $Repo"
Write-Host "    This script lives inside it, so removing it is left to you."
Write-Host ""
Write-Host "  FFmpeg, Node.js and Python were left alone; other programs on this"
Write-Host "  machine are likely to be using them."
Write-Host "`nUninstall complete."
