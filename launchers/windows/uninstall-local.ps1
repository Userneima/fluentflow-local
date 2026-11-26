# Uninstall FluentFlow Local from this Windows machine.
#
# Three layers, because "uninstall" means three very different things here:
#   Program (always): the code folder (when it was installed from a release
#       zip; a git checkout keeps its code), its virtual environment, frontend
#       dependencies and build, the FFmpeg and Node.js the installer downloaded,
#       the pip and npm caches, the Desktop launcher, logs, the agent skill, and
#       the fluentflow-local MCP tools in Claude Code and Codex (only when they
#       point at this folder). Reinstalling brings all of it back.
#   Models (-Models): several gigabytes of transcription weights. In a
#       one-folder install they have their own folder, removed whole; otherwise
#       they live in the shared Hugging Face cache, so only the directories this
#       product downloads are removed, and that list comes from the code rather
#       than from a name pattern.
#   Data (-Data): task history, transcripts, notes, media, videos downloaded
#       from links. Not recoverable, so it takes its own switch and a second
#       confirmation.
# A one-folder install (install.ps1, FLUENTFLOW_HOME in .env) removed with -All
# leaves nothing: the folder itself goes once it is empty.
#
# A Python, FFmpeg or Node.js that was on the machine already is left alone:
# other programs are likely to be using it.
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
$Repo = (Resolve-Path (Split-Path -Parent (Split-Path -Parent $ScriptDir))).Path
. (Join-Path $ScriptDir "fluentflow-env.ps1")
# The port and every location below follow the environment, then .env, like
# the service.
$Port = Get-FluentFlowPort $Repo

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
    $Busy = Get-FluentFlowBusyCount $Port
    if ($Busy -gt 0) { Write-Host "注意：后台还有 ${Busy} 个任务在进行中，停止会让它们中断。" }
    elseif ($Busy -lt 0) { Write-Host "注意：问不到后台有没有任务在进行，停止可能让它们中断。" }
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

# Ask the code where the data lives: FLUENTFLOW_DATA_DIR (environment or .env)
# can move it.
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = Join-Path $Repo "venv\Scripts\python.exe" }

$DataRoot = $null
if (Test-Path $Python) {
    Push-Location $Repo
    try {
        $Output = & $Python -c "from backend.core.runtime_paths import load_project_env, app_data_root; load_project_env(); print(app_data_root())" 2>$null
        $Candidate = ($Output | Select-Object -Last 1)
        if ($Candidate) { $DataRoot = $Candidate.Trim() }
    } catch {
        $DataRoot = $null
    } finally {
        Pop-Location
    }
}
if (-not $DataRoot) {
    $DataRoot = Get-FluentFlowDataDir $Repo
    Write-Host "`n（问不到代码里的数据目录，按 .env 和默认位置处理：${DataRoot}）"
}
# Every location below is placed as the service places it (Resolve-FluentFlowPlace:
# a relative path is in the code folder, which the service runs in). One that
# cannot be placed for sure is neither deleted nor taken to be outside the code
# folder.
$DataPlace = Resolve-FluentFlowPlace $Repo $DataRoot
$DataSure = $DataPlace.Sure
$DataRoot = $DataPlace.Path
function Get-Place([string]$Name) { return Resolve-FluentFlowPlace $Repo (Get-FluentFlowSetting $Repo $Name) }
function Get-SurePath($Place) { if ($Place -and $Place.Sure) { return $Place.Path }; return $null }
# Inside the code folder, or cannot be told.
function Test-InRepo($Place) { return $Place -and ((-not $Place.Sure) -or (Test-FluentFlowInside $Place.Path $Repo)) }

# A one-folder install: everything below this folder is FluentFlow's.
$HomeDir = Get-SurePath (Get-Place "FLUENTFLOW_HOME")
function Test-InHome([string]$Path) {
    if (-not $HomeDir -or -not $Path) { return $false }
    return (Test-FluentFlowInside $Path $HomeDir) -and -not (Test-FluentFlowInside $HomeDir $Path)
}
# Only a folder the installer made (it leaves .fluentflow-home in it) is
# FluentFlow's to empty; a folder the user already had keeps what was there.
if ($HomeDir -and (-not (Test-InHome $Repo) -or -not (Test-Path (Join-Path $HomeDir ".fluentflow-home")))) { $HomeDir = $null }
$ModelPlace = Get-Place "HF_HOME"
$LinkPlace = Get-Place "FLUENTFLOW_LINK_OUTPUT_DIR"
$ModelHome = Get-SurePath $ModelPlace
$LinkRoot = Get-SurePath $LinkPlace
$IsGitCheckout = Test-Path (Join-Path $Repo ".git")
# The code folder goes whole only when nothing the user keeps lives in it: a
# data, download or model folder set inside it is left, and only the program's
# own parts are removed (as for a git checkout).
$KeepsUserFiles = (Test-InRepo $DataPlace) -or (Test-InRepo $LinkPlace) -or (Test-InRepo $ModelPlace)
# Each part of the data can be moved on its own (task database, sources, notes,
# edits, settings...): any of them inside the code folder keeps it too.
$PartNames = @(Get-ChildItem Env: | Where-Object { $_.Name -match '^FLUENTFLOW_\w*(DIR|PATH|CACHE)$' } | ForEach-Object { $_.Name })
if (Test-Path (Join-Path $Repo ".env")) {
    $PartNames += @([System.IO.File]::ReadAllLines((Join-Path $Repo ".env"), [System.Text.Encoding]::UTF8) | ForEach-Object { if ($_ -match '^\s*(export\s+)?(FLUENTFLOW_\w*(DIR|PATH|CACHE))\s*=') { $Matches[2] } })
}
# The window's variable and the .env line are both checked: the service a
# launcher starts need not have this window's variables.
foreach ($Name in ($PartNames | Where-Object { $_ -ne "FLUENTFLOW_INSTALL_DIR" } | Sort-Object -Unique)) {
    foreach ($Where in @([Environment]::GetEnvironmentVariable($Name, "Process"), (Get-FluentFlowEnvFileValue $Repo $Name))) {
        if (Test-InRepo (Resolve-FluentFlowPlace $Repo $Where)) { $KeepsUserFiles = $true }
    }
}

$ModelDirs = @()
if ($Models -and (Test-InHome $ModelHome)) {
    $ModelDirs = @($ModelHome)
} elseif ($Models) {
    if (Test-Path $Python) {
        try {
            Import-FluentFlowEnv $Repo | Out-Null
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

if ($IsGitCheckout -or $KeepsUserFiles) {
    Add-Target (Join-Path $Repo ".venv") "Python 虚拟环境"
    Add-Target (Join-Path $Repo "venv") "Python 虚拟环境（旧位置）"
    Add-Target (Join-Path $Repo "node_modules") "前端依赖"
    Add-Target (Join-Path $Repo "frontend\dist-local") "前端构建产物"
} else {
    # Installed from a release zip: the folder holds nothing but the program
    # (and .env, whose access token is regenerated on reinstall).
    Add-Target $Repo "程序（代码、Python 虚拟环境、前端）"
    # An update's copies beside the program (install.ps1): the one that failed,
    # or one an interrupted update left. Only those carrying its marker.
    # Only one whose marker names where it is now (Test-FluentFlowUpdateCopy):
    # a copy or rename the user made of the code folder is left alone.
    foreach ($Side in @("$Repo.new", "$Repo.old", "$Repo.failed")) {
        if (Test-FluentFlowUpdateCopy $Side) { Add-Target $Side "更新时留下的另一份程序" }
    }
}
Add-Target (Join-Path ([Environment]::GetFolderPath("Desktop")) "FluentFlow Local.cmd") "桌面启动器"
Add-Target (Join-Path ([Environment]::GetFolderPath("Desktop")) "停止 FluentFlow Local.cmd") "桌面上的停止图标"
Add-Target (Join-Path $env:LOCALAPPDATA "FluentFlow\Logs") "日志"
# Whether an entry of the data folder is FluentFlow's
# (launchers/data-folder-contents.txt): its name is on the list, and for a
# common name (bin, models, logs...) everything inside it is what FluentFlow
# puts there. The data folder may be one the user keeps their own bin or
# models in, which a name alone cannot tell apart.
$Registry = Join-Path $Repo "launchers\data-folder-contents.txt"
$DataRules = @()
if (Test-Path -LiteralPath $Registry) {
    $DataRules = @([System.IO.File]::ReadAllLines($Registry, [System.Text.Encoding]::UTF8) |
        ForEach-Object { , @((($_ -split '#', 2)[0].Trim() -split '\s+') | Where-Object { $_ }) } | Where-Object { $_.Count -gt 0 })
}
function Test-FluentFlowDataEntry($Entry) {
    foreach ($Rule in $DataRules) {
        if ($Entry.Name -notlike $Rule[0]) { continue }
        if ($Rule.Count -eq 1) { return $true }
        if (-not $Entry.PSIsContainer -or ($Entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) { return $false }
        foreach ($Child in @(Get-ChildItem -Force -LiteralPath $Entry.FullName)) {
            if ($Child.Name -in @(".DS_Store", "Thumbs.db", "desktop.ini")) { continue }
            $ChildName = $Child.Name
            if (@($Rule[1..($Rule.Count - 1)] | Where-Object { $ChildName -like $_ }).Count -eq 0) { return $false }
        }
        return $true
    }
    return $false
}

$DataLeft = @()
if ($DataSure) {
    foreach ($Part in @(@("bin", "安装时下载的 FFmpeg"), @("tools", "安装时下载的 Node.js"))) {
        $Entry = Get-Item -Force -LiteralPath (Join-Path $DataRoot $Part[0]) -ErrorAction SilentlyContinue
        if (-not $Entry) { continue }
        if (Test-FluentFlowDataEntry $Entry) { Add-Target $Entry.FullName $Part[1] }
        elseif (-not $Data) { $DataLeft += $Entry }
    }
}
if ($HomeDir) { Add-Target (Join-Path $HomeDir "cache") "pip、uv 和 npm 的下载缓存" }
foreach ($ToolDir in @((Join-Path $env:USERPROFILE ".claude"), (Join-Path $env:USERPROFILE ".codex"))) {
    $Skill = Join-Path $ToolDir "skills\fluentflow"
    if (Test-Path (Join-Path $Skill ".installed-by-fluentflow")) { Add-Target $Skill "给 agent 的 skill" }
}
foreach ($Dir in $ModelDirs) { Add-Target $Dir "转录模型" }
# The data folder may be one the user keeps their own files in too: it goes
# whole only when everything in it is something FluentFlow makes
# (launchers/data-folder-contents.txt); otherwise only those go, and the rest
# is named and left.
if ($Data -and -not $DataSure) {
    Write-Host "`n数据目录的位置读不准（${DataRoot}，里面有展开不了的 `$），不删它；请自己确认它在哪后再删。"
} elseif ($Data -and (Test-Path -LiteralPath $DataRoot -PathType Container)) {
    $Recognised = @()
    foreach ($Entry in @(Get-ChildItem -Force -LiteralPath $DataRoot)) {
        if (Test-FluentFlowDataEntry $Entry) { $Recognised += $Entry } else { $DataLeft += $Entry }
    }
    if ($DataLeft.Count -eq 0) {
        Add-Target $DataRoot "任务历史、转录稿、笔记、媒体文件" $true
    } else {
        foreach ($Entry in $Recognised) {
            if (@($Targets | Where-Object { $_.Path -eq $Entry.FullName }).Count -eq 0) { Add-Target $Entry.FullName "FluentFlow 的数据：$($Entry.Name)" $true }
        }
    }
}
if ($Data -and (Test-InHome $LinkRoot)) { Add-Target $LinkRoot "从链接下载的视频和它们的笔记" $true }

# The MCP tools registered at install, removed only when they start this
# folder's server: an entry pointing at another copy is someone else's.
function Test-MentionsRepo([string]$Text) {
    if (-not $Text) { return $false }
    # The folder followed by a separator: C:\app must not claim C:\app-backup.
    return $Text.Replace('\\', '\').IndexOf($Repo.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -ge 0
}
$McpRemovals = New-Object System.Collections.ArrayList
if (Get-Command claude -ErrorAction SilentlyContinue) {
    $Described = Invoke-FluentFlowTool claude @("mcp", "get", "fluentflow-local")
    if ($Described.Code -eq 0 -and (Test-MentionsRepo $Described.Output)) { [void]$McpRemovals.Add("Claude Code") }
    elseif ($Described.Code -eq 0) { Write-Host "`nClaude Code 里的 fluentflow-local 指向别的位置，不动它。" }
}
$CodexConfig = Join-Path $env:USERPROFILE ".codex\config.toml"
if (Test-Path $CodexConfig) {
    $Toml = [System.IO.File]::ReadAllText($CodexConfig)
    $Section = [regex]::Match($Toml, '(?ms)^\[mcp_servers\.fluentflow-local\]\s*$.*?(?=^\[|\z)')
    if ($Section.Success -and (Test-MentionsRepo $Section.Value)) { [void]$McpRemovals.Add("Codex") }
    elseif ($Section.Success) { Write-Host "`nCodex 里的 fluentflow-local 指向别的位置，不动它。" }
}

if ($Targets.Count -eq 0 -and $McpRemovals.Count -eq 0) {
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
foreach ($Tool in $McpRemovals) { Write-Host "  注销 $Tool 里的 fluentflow-local 工具" }
if ($DataLeft.Count -gt 0) {
    Write-Host "`n数据目录 ${DataRoot} 里还有 FluentFlow 认不出的东西，不删，文件夹也留着："
    foreach ($Entry in $DataLeft) { Write-Host "  $($Entry.Name)" }
    Write-Host "  它们可能是你自己的文件，确认不要了再自己删。"
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
foreach ($Tool in $McpRemovals) {
    if ($Tool -eq "Claude Code") {
        if ((Invoke-FluentFlowTool claude @("mcp", "remove", "--scope", "user", "fluentflow-local")).Code -eq 0) { Write-Host "已注销：Claude Code 里的 fluentflow-local" }
        else { Write-Host "Claude Code 注销失败，请手动运行：claude mcp remove --scope user fluentflow-local" }
    } else {
        $Removed = $false
        if (Get-Command codex -ErrorAction SilentlyContinue) {
            $Removed = ((Invoke-FluentFlowTool codex @("mcp", "remove", "fluentflow-local")).Code -eq 0)
        }
        if ($Removed) { Write-Host "已注销：Codex 里的 fluentflow-local" }
        else { Write-Host "Codex 没能自动注销，请删掉 $CodexConfig 里 [mcp_servers.fluentflow-local] 那一节。" }
    }
}
# Programs still running from the code folder hold their files: the background
# model download, the daily yt-dlp update, a service on another port.
Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and $_.Path.StartsWith($Repo + '\', [StringComparison]::OrdinalIgnoreCase)
} | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500
# Windows will not remove a folder a process is standing in, and the window that
# runs this script may be standing in the code folder. This script is already in
# memory; what cannot go is emptied and named at the end.
Set-Location $env:USERPROFILE
$Stuck = New-Object System.Collections.ArrayList
foreach ($Target in $Targets) {
    try {
        Remove-Item -Recurse -Force -LiteralPath $Target.Path -ErrorAction Stop
        Write-Host "已删除：$($Target.Path)"
    } catch {
        if (Test-Path -PathType Container -LiteralPath $Target.Path) {
            Get-ChildItem -Force -LiteralPath $Target.Path | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }
        if (Get-ChildItem -Force -LiteralPath $Target.Path -ErrorAction SilentlyContinue) {
            Write-Host "删不掉：$($Target.Path)（$($_.Exception.Message)）" -ForegroundColor Yellow
        } else {
            Write-Host "已清空：$($Target.Path)（文件夹本身还在用）"
        }
        [void]$Stuck.Add($Target.Path)
    }
}
if ($HomeDir -and (Test-Path $HomeDir) -and -not (Get-ChildItem -Force -LiteralPath $HomeDir | Where-Object { $_.Name -ne ".fluentflow-home" } | Select-Object -First 1)) {
    Remove-Item -Force -LiteralPath (Join-Path $HomeDir ".fluentflow-home") -ErrorAction SilentlyContinue
    Remove-Item -Force -LiteralPath $HomeDir
    Write-Host "已删除：$HomeDir（已经空了）"
}

Write-Host "`n== 还剩下 =="
if ($DataLeft.Count -gt 0) {
    Write-Host "  数据目录 ${DataRoot}：FluentFlow 自己的部分删了，认不出的这些留着，确认不要了再自己删："
    foreach ($Entry in $DataLeft) { Write-Host "    $($Entry.Name)" }
}
# A zip install's code folder, and this script with it, is gone by now, so
# what is left is removed by deleting its folder.
if ((-not $Data) -and (Test-Path $DataRoot)) {
    Write-Host "  你的任务数据（$(Format-Size (Get-FolderSize $DataRoot))）：${DataRoot}"
    if ($IsGitCheckout) { Write-Host "    要一并删除：.\launchers\windows\uninstall-local.ps1 -Data" }
    else { Write-Host "    确定不要了，直接删掉这个文件夹。" }
}
if ((-not $Data) -and (Test-InHome $LinkRoot) -and (Test-Path $LinkRoot)) {
    Write-Host "  从链接下载的视频和笔记：${LinkRoot}"
}
if ($Models) {
} elseif (Test-InHome $ModelHome) {
    if (Test-Path $ModelHome) { Write-Host "  转录模型（通常几 GB）：${ModelHome}，不要了直接删掉这个文件夹。" }
} elseif ($IsGitCheckout) {
    Write-Host "  转录模型（通常几 GB）：在 Hugging Face 缓存里"
    Write-Host "    要一并删除：.\launchers\windows\uninstall-local.ps1 -Models"
} else {
    Write-Host "  转录模型（通常几 GB）：在 %USERPROFILE%\.cache\huggingface\hub\ 下以"
    Write-Host "    models--Systran--faster-whisper- 开头的目录里，可自行确认后删除。"
}
if ($IsGitCheckout) {
    Write-Host "  代码目录：${Repo}"
    Write-Host "    这是 git 检出，里面可能有你的改动，留给你自己删。"
}
foreach ($Path in $Stuck) {
    Write-Host "  ${Path}：有窗口或程序正在这个文件夹里（比如运行本脚本的命令行），"
    Write-Host "    关掉它之后再删掉这个文件夹。"
}
Write-Host ""
Write-Host "  Python 没有动：它装在系统里（「设置 → 应用」里能看到），别的程序可能也在用；"
Write-Host "  确定不需要，可以在那里卸载。机器上原本就有的 FFmpeg、Node.js 也没有动。"
Write-Host "`n✓ 卸载完成。"
