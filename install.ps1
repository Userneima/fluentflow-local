# One-command install of FluentFlow Local on Windows, for agents (people can run it too):
#
#   powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/Userneima/fluentflow-local/main/install.ps1 | iex"
#
# In China, the same script from npmmirror (published with each release):
#   powershell -ExecutionPolicy Bypass -Command "irm https://registry.npmmirror.com/fluentflow-local/latest/files/install.ps1 | iex"
#
# The whole product goes into one folder, FluentFlow, on the drive chosen:
#   app\        the code and its Python environment
#   data\       task history, notes, media, the downloaded FFmpeg and Node.js
#   models\     the transcription models (the Hugging Face cache, HF_HOME)
#   downloads\  videos fetched from links, with their notes
#   cache\      pip, uv and npm download caches
# Where each one is goes into app\.env, which the service and every script read,
# so nothing large lands on drive C and uninstalling can remove one folder.
# Python itself, when the machine has none, is a per-user install on drive C.
#
# Which drive: run by a person in a console, it lists the drives and asks, with
# the drive that has the most free space as the default. Run by an agent (no
# console to answer), it never asks: it takes FLUENTFLOW_INSTALL_DIR, or else
# that same default. Drive C goes to %USERPROFILE%\FluentFlow, another drive to
# X:\FluentFlow.
#
# Then it runs the setup script, starts FluentFlow, registers its MCP tools with
# Claude Code and Codex when they are installed, and checks the MCP server.
# Nothing asks for administrator rights. An existing install keeps its code
# (including one from before the one-folder layout, %USERPROFILE%\fluentflow-local);
# only the later steps run again.
#
# Optional environment variables:
#   FLUENTFLOW_INSTALL_DIR   the folder to install into (for example D:\FluentFlow)
#   FLUENTFLOW_INSTALL_REF   which release tag (for example v0.7.0), default the latest
#   FLUENTFLOW_INSTALL_SOURCE a release already on this machine (an unpacked
#                            folder or a .zip) to install or update from, with
#                            no download
$ErrorActionPreference = "Stop"

# Python's UTF-8 mode for every Python this starts (the setup script, the
# service, the MCP check), and the console decoding their output as UTF-8.
# Without it, an English Windows prints in cp1252 and the first Chinese line
# raises UnicodeEncodeError.
$env:PYTHONUTF8 = "1"
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}
# Windows PowerShell 5.1 on an older .NET offers only TLS 1.0/1.1 by default.
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch {}

$RepoSlug = "Userneima/fluentflow-local"

function Get-FixedDrives {
    $SystemDrive = $env:SystemDrive.TrimEnd('\')
    @(Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" -ErrorAction SilentlyContinue |
        Where-Object { $_.FreeSpace -gt 0 } |
        ForEach-Object {
            [pscustomobject]@{
                Name = $_.DeviceID
                FreeGB = [math]::Floor($_.FreeSpace / 1GB)
                IsSystem = ($_.DeviceID -eq $SystemDrive)
            }
        })
}

function Get-InstallRoot([string]$Drive) {
    if ($Drive -eq $env:SystemDrive.TrimEnd('\')) { return Join-Path $env:USERPROFILE "FluentFlow" }
    return "$Drive\FluentFlow"
}

# Most free space wins; drive C only when no other drive has room (about 10 GB
# for the environment, the models and the interface build).
function Select-DefaultDrive($Drives) {
    $Other = @($Drives | Where-Object { -not $_.IsSystem -and $_.FreeGB -ge 10 } | Sort-Object FreeGB -Descending)
    if ($Other.Count -gt 0) { return $Other[0].Name }
    return $env:SystemDrive.TrimEnd('\')
}

# A person at a console can answer; an agent's shell cannot, and a prompt there
# would hang the install.
function Test-CanAsk {
    try { return [Environment]::UserInteractive -and -not [Console]::IsInputRedirected } catch { return $false }
}

# An earlier one-folder install: its code, or its data when the code folder was
# set aside to upgrade (README, Updating). Reused rather than picking a drive
# again, which could land on another drive and leave the data behind.
function Find-ExistingRoot {
    $Own = Join-Path $env:USERPROFILE "FluentFlow"
    if ((Test-Path (Join-Path $Own "app\backend\local_main.py")) -or (Test-Path (Join-Path $Own "data\fluentflow_jobs.sqlite"))) { return $Own }
    # On another drive, only a folder this Windows account made (its marker
    # names the account): on a shared computer another user's install, with
    # their tasks and access token, is not this user's to take over.
    foreach ($Candidate in @(Get-FixedDrives | ForEach-Object { "$($_.Name)\FluentFlow" })) {
        $Marker = Join-Path $Candidate ".fluentflow-home"
        if ((Test-Path $Marker) -and ((Get-Content -Raw $Marker) -match [regex]::Escape($CurrentSid))) { return $Candidate }
    }
    return $null
}

$CurrentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$LegacyDir = Join-Path $env:USERPROFILE "fluentflow-local"
$Existing = $null
if (-not $env:FLUENTFLOW_INSTALL_DIR) { $Existing = Find-ExistingRoot }
if ($env:FLUENTFLOW_INSTALL_DIR) {
    $Root = $env:FLUENTFLOW_INSTALL_DIR
} elseif ($Existing) {
    $Root = $Existing
} elseif (Test-Path (Join-Path $LegacyDir "backend\local_main.py")) {
    $Root = $LegacyDir
} else {
    $Drives = Get-FixedDrives
    $Default = Select-DefaultDrive $Drives
    if ($Drives.Count -gt 1 -and (Test-CanAsk)) {
        Write-Host "FluentFlow needs about 10 GB. Drives on this computer:"
        foreach ($Drive in $Drives) { Write-Host ("  {0}  {1} GB free" -f $Drive.Name, $Drive.FreeGB) }
        $Answer = (Read-Host "Install to which drive? Press Enter for $Default").Trim().TrimEnd(':', '\')
        if ($Answer) {
            $Picked = @($Drives | Where-Object { $_.Name -eq "$($Answer.ToUpper()):" })
            if ($Picked.Count -eq 0) { throw "There is no drive $Answer on this computer." }
            $Default = $Picked[0].Name
        }
    }
    $Root = Get-InstallRoot $Default
}

# An install from before the one-folder layout has the code directly in the
# folder; it keeps its layout and its data where it is.
if (Test-Path (Join-Path $Root "backend\local_main.py")) {
    $Dir = $Root
    $OneFolder = $false
} else {
    $Dir = Join-Path $Root "app"
    $OneFolder = $true
    # Everything in this folder becomes FluentFlow's, and uninstalling empties
    # it; so it must be new, empty, or one this installer made (the marker).
    $Marker = Join-Path $Root ".fluentflow-home"
    $HasApp = Test-Path (Join-Path $Dir "backend\local_main.py")
    if ((Test-Path $Root) -and -not (Test-Path $Marker) -and -not $HasApp -and (Get-ChildItem -Force -LiteralPath $Root | Select-Object -First 1)) {
        throw "$Root already holds other files. Choose a new or empty folder for FluentFlow (FLUENTFLOW_INSTALL_DIR), for example $Root\FluentFlow."
    }
    New-Item -ItemType Directory -Force -Path $Root | Out-Null
    if (-not (Test-Path $Marker)) { Set-Content -Path $Marker -Value "FluentFlow Local lives here; uninstalling removes this folder. Installed by $CurrentSid" -Encoding ASCII }
}
Write-Host "Installing into $Root"

# The update's working folders beside the install. One already there is
# removed only when an earlier update made it (its .fluentflow-update marker):
# a folder the user named app.old is theirs.
$UpdateMarker = ".fluentflow-update"
function Assert-OwnLeftover([string]$Path) {
    if ((Test-Path $Path) -and -not (Test-Path (Join-Path $Path $UpdateMarker))) {
        throw "$Path exists and was not made by a FluentFlow update; move or rename it first"
    }
}
# Where the old .env (or this window) puts data; inside the code folder it would
# go away with the old code.
function Get-InstalledSetting([string]$Folder, [string]$Name) {
    $FromEnv = [Environment]::GetEnvironmentVariable($Name)
    if ($FromEnv) { return $FromEnv }
    $EnvFile = Join-Path $Folder ".env"
    if (-not (Test-Path $EnvFile)) { return $null }
    $Line = Get-Content $EnvFile | Where-Object { $_ -match ('^\s*(export\s+)?' + $Name + '\s*=') } | Select-Object -Last 1
    if (-not $Line) { return $null }
    return (($Line -split '=', 2)[1] -split '\s#')[0].Trim().Trim('"').Trim("'")
}

Write-Host "== Getting the code =="
# An install already here is updated in place when a newer release is out: the
# new code goes beside it, keeps its .env, and replaces it only once the
# service is stopped; setup then adds what the new version needs (packages,
# speaker models, the transcription model), and if setup or the restart fails
# the previous version is put back. A git checkout is the developer's and is
# left as it is.
$HaveCode = Test-Path (Join-Path $Dir "backend\local_main.py")
$IsGit = $HaveCode -and (Test-Path (Join-Path $Dir ".git"))
$Updating = $false
$Current = if ($HaveCode) { (Get-Content -Raw (Join-Path $Dir "VERSION")).Trim() } else { $null }
if ($IsGit) {
    Write-Host "Using the git checkout in $Dir as it is (update it with git pull, then run this again)."
} else {
    if (-not $HaveCode -and (Test-Path $Dir)) { throw "$Dir exists but is not a FluentFlow Local folder. Set FLUENTFLOW_INSTALL_DIR to another place." }
  $LocalSource = $env:FLUENTFLOW_INSTALL_SOURCE
  if ($LocalSource) {
    if ($LocalSource -like "*.zip") {
        $Unzipped = Join-Path ([System.IO.Path]::GetTempPath()) ("fluentflow-src-" + [guid]::NewGuid())
        Expand-Archive -Path $LocalSource -DestinationPath $Unzipped
        $Inner = Get-ChildItem $Unzipped -Directory | Where-Object { Test-Path (Join-Path $_.FullName "backend\local_main.py") } | Select-Object -First 1
        $LocalSource = if (Test-Path (Join-Path $Unzipped "backend\local_main.py")) { $Unzipped } else { $Inner.FullName }
    }
    if (-not $LocalSource -or -not (Test-Path (Join-Path $LocalSource "backend\local_main.py"))) { throw "FLUENTFLOW_INSTALL_SOURCE is not a FluentFlow Local release: $($env:FLUENTFLOW_INSTALL_SOURCE)" }
    $Latest = (Get-Content -Raw (Join-Path $LocalSource "VERSION")).Trim()
    if ($HaveCode) {
        if ([version]$Latest -gt [version]$Current) { $Updating = $true; Write-Host "Updating FluentFlow Local $Current to $Latest from $LocalSource." }
        else { Write-Host "FluentFlow Local $Current in $Dir is not older than $Latest in $LocalSource." }
    }
    if (-not $HaveCode -or $Updating) {
        $Target = if ($Updating) { "$Dir.new" } else { $Dir }
        if ($Updating) { Assert-OwnLeftover $Target }
        if (Test-Path $Target) { Remove-Item -Recurse -Force -LiteralPath $Target }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Target) | Out-Null
        Copy-Item -Recurse -LiteralPath $LocalSource -Destination $Target
        Remove-Item -Recurse -Force -LiteralPath (Join-Path $Target ".git"), (Join-Path $Target ".venv"), (Join-Path $Target ".env") -ErrorAction SilentlyContinue
        Set-Content -Path (Join-Path $Target $UpdateMarker) -Value "made by a FluentFlow Local install or update" -Encoding ASCII
        Write-Host "Placed in $Target"
    }
  } else {
    # Two hosts of the release: GitHub, and the npm package fluentflow-local on
    # npmmirror (scripts/stage_npm_package.py), which is reachable in China.
    # GitHub is probed for a few seconds, the same rule as every other download
    # (launchers/windows/fluentflow-env.ps1, which is not on this machine yet):
    # when it is unreachable or plainly slow, npmmirror goes first.
    $Tag = $env:FLUENTFLOW_INSTALL_REF
    $GithubZip = $null
    try {
        if (-not $Tag) {
            # The releases/latest redirect, not the GitHub API, which limits anonymous calls.
            $Request = [System.Net.WebRequest]::Create("https://github.com/$RepoSlug/releases/latest")
            $Request.AllowAutoRedirect = $false
            $Request.Timeout = 10000
            $Response = $Request.GetResponse()
            $Latest = ($Response.Headers["Location"] -split "/tag/")[-1]
            $Response.Close()
            if ($Latest -match '^v\d') { $Tag = $Latest }
        }
        if ($Tag) { $GithubZip = "https://github.com/$RepoSlug/archive/refs/tags/$Tag.zip" }
    } catch {}
    $NpmVersion = if ($Tag) { $Tag.TrimStart('v') } else { "latest" }
    $NpmMeta = $null
    try { $NpmMeta = Invoke-RestMethod -UseBasicParsing -TimeoutSec 15 "https://registry.npmmirror.com/fluentflow-local/$NpmVersion" } catch {}
    $Latest = if ($Tag) { $Tag.TrimStart('v') } elseif ($NpmMeta) { "$($NpmMeta.version)" } else { $null }
    if ($HaveCode) {
        if ($Latest -and $Current -and ([version]$Latest -gt [version]$Current)) {
            $Updating = $true
            Write-Host "Updating FluentFlow Local $Current to $Latest."
        } elseif ($Latest) {
            Write-Host "FluentFlow Local $Current in $Dir is the latest release."
        } else {
            Write-Host "Could not learn the latest release; keeping $Current in $Dir."
        }
    } elseif (-not $GithubZip -and -not $NpmMeta) {
        throw "Could not reach github.com or registry.npmmirror.com to download FluentFlow; check the network or proxy."
    }
  if (-not $HaveCode -or $Updating) {
    $Target = if ($Updating) { "$Dir.new" } else { $Dir }
    if ($Updating) { Assert-OwnLeftover $Target; if (Test-Path $Target) { Remove-Item -Recurse -Force -LiteralPath $Target } }

    function Measure-Url([string]$Url) {
        $Got = 0
        $Watch = [Diagnostics.Stopwatch]::StartNew()
        $Response = $null
        try {
            $Request = [System.Net.HttpWebRequest]::Create($Url)
            $Request.Timeout = 5000
            $Request.ReadWriteTimeout = 5000
            $Response = $Request.GetResponse()
            $Stream = $Response.GetResponseStream()
            $Buffer = New-Object byte[] 65536
            while ($Got -lt 1MB -and $Watch.Elapsed.TotalSeconds -lt 5) {
                $Read = $Stream.Read($Buffer, 0, $Buffer.Length)
                if ($Read -le 0) { if ($Got -gt 0) { $Got = 1MB }; break }
                $Got += $Read
            }
        } catch {} finally { if ($Response) { $Response.Close() } }
        return $Got
    }
    $Order = @()
    if ($GithubZip -and (-not $NpmMeta -or (Measure-Url $GithubZip) -ge 1MB)) { $Order = @("github", "npm") } else { $Order = @("npm", "github") }

    $Work = Join-Path ([System.IO.Path]::GetTempPath()) ("fluentflow-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $Work | Out-Null
    $SavedProgress = $ProgressPreference
    $ProgressPreference = "SilentlyContinue"
    $Unpacked = $null
    try {
        foreach ($Source in $Order) {
            try {
                if ($Source -eq "github" -and $GithubZip) {
                    Write-Host "Downloading $Tag from github.com..."
                    Invoke-WebRequest -UseBasicParsing -TimeoutSec 600 -Uri $GithubZip -OutFile (Join-Path $Work "src.zip")
                    Expand-Archive -Path (Join-Path $Work "src.zip") -DestinationPath (Join-Path $Work "src")
                    $Unpacked = (Get-ChildItem (Join-Path $Work "src") -Directory | Select-Object -First 1).FullName
                    # The interface built for this release (release.yml), so the
                    # setup script skips Node.js and the build. Older releases
                    # have none; the setup script then builds it.
                    try {
                        Invoke-WebRequest -UseBasicParsing -TimeoutSec 300 -Uri "https://github.com/$RepoSlug/releases/download/$Tag/frontend-dist-local.zip" -OutFile (Join-Path $Work "ui.zip")
                        Expand-Archive -Path (Join-Path $Work "ui.zip") -DestinationPath (Join-Path $Unpacked "frontend\dist-local")
                    } catch { Write-Host "  (no prebuilt interface attached to $Tag; it will be built here)" }
                } elseif ($Source -eq "npm" -and $NpmMeta) {
                    Write-Host "Downloading $($NpmMeta.version) from registry.npmmirror.com..."
                    $Tgz = Join-Path $Work "src.tgz"
                    Invoke-WebRequest -UseBasicParsing -TimeoutSec 600 -Uri $NpmMeta.dist.tarball -OutFile $Tgz
                    $Stream = [System.IO.File]::OpenRead($Tgz)
                    try { $Digest = [Convert]::ToBase64String([System.Security.Cryptography.SHA512]::Create().ComputeHash($Stream)) } finally { $Stream.Close() }
                    if ("sha512-$Digest" -ne $NpmMeta.dist.integrity) { throw "the download does not match its checksum" }
                    New-Item -ItemType Directory -Path (Join-Path $Work "npm") | Out-Null
                    & tar.exe -xzf $Tgz -C (Join-Path $Work "npm")
                    if ($LASTEXITCODE -ne 0) { throw "tar could not unpack it" }
                    $Unpacked = Join-Path $Work "npm\package\source"
                }
                if ($Unpacked -and (Test-Path (Join-Path $Unpacked "backend\local_main.py"))) { break }
                $Unpacked = $null
            } catch {
                Write-Host "  $Source failed: $($_.Exception.Message)"
                $Unpacked = $null
            }
        }
        if (-not $Unpacked) { throw "The code could not be downloaded from github.com or registry.npmmirror.com; check the network or proxy." }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Dir) | Out-Null
        Move-Item $Unpacked $Target
    } finally {
        $ProgressPreference = $SavedProgress
        Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
    }
    Set-Content -Path (Join-Path $Target $UpdateMarker) -Value "made by a FluentFlow Local install or update" -Encoding ASCII
    Write-Host "Placed in $Target"
  }
  }
}

# Programs still running from a folder hold its files: the service, the
# background model download, the daily yt-dlp update.
function Stop-FolderPrograms([string]$Folder) {
    Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and $_.Path.StartsWith($Folder.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)
    } | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500
}

# Until the folders are swapped nothing here may rely on either copy's scripts:
# the old one may predate them, the new one is not in place yet.
function Get-InstalledPort([string]$Folder) {
    if ($env:FLUENTFLOW_LOCAL_PORT) { return $env:FLUENTFLOW_LOCAL_PORT }
    $EnvFile = Join-Path $Folder ".env"
    if (Test-Path $EnvFile) {
        $Line = Get-Content $EnvFile | Where-Object { $_ -match '^\s*(export\s+)?FLUENTFLOW_LOCAL_PORT\s*=\s*[''"]?(\d+)' } | Select-Object -Last 1
        if ($Line -and $Line -match '=\s*[''"]?(\d+)') { return $Matches[1] }
    }
    return "8000"
}
function Test-ServiceUp([string]$ProbePort) {
    try { return (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:$ProbePort/health").Content -match '"execution":"local"|"edition":"local"' } catch { return $false }
}

# How many tasks are still working (the same count as Get-FluentFlowBusyCount
# in launchers/windows/fluentflow-env.ps1 and launchers/macos/count_active_jobs.py,
# repeated here because neither copy's scripts can be relied on yet):
# counts them: each task's activity.busy (a task is "completed" once its
# transcript is stored while its cut and note may run for many minutes), asked
# under both scopes the local edition uses. -1 when the service cannot say.
function Get-BusyTaskCount([string]$Port) {
    $Counts = @()
    foreach ($Scope in @($null, "local-single-user")) {
        try {
            $Headers = @{}
            if ($Scope) { $Headers["X-FluentFlow-Client-Id"] = $Scope }
            $Jobs = (Invoke-RestMethod -Uri "http://127.0.0.1:$Port/jobs" -Headers $Headers -TimeoutSec 10).jobs
            $Counts += @($Jobs | Where-Object {
                if ($_.activity -and ($_.activity.busy -is [bool])) { $_.activity.busy }
                else { ($_.status -in @("queued", "processing", "running", "pending")) -or ($_.summary_status -eq "pending") -or ($_.result.debreath.status -eq "running") }
            }).Count
        } catch {}
    }
    if ($Counts.Count -eq 0) { return -1 }
    return ($Counts | Measure-Object -Maximum).Maximum
}

if ($Updating) {
  try {
    foreach ($Leftover in @("$Dir.old", "$Dir.failed")) { Assert-OwnLeftover $Leftover }
    foreach ($Name in @("FLUENTFLOW_DATA_DIR", "FLUENTFLOW_LINK_OUTPUT_DIR", "HF_HOME")) {
        $Where = Get-InstalledSetting $Dir $Name
        if ($Where -and [System.IO.Path]::IsPathRooted($Where) -and
            [System.IO.Path]::GetFullPath($Where).StartsWith($Dir.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw "$Name ($Where) is inside the code folder, which an update replaces; move it out of $Dir first"
        }
    }
    $OldPort = Get-InstalledPort $Dir
    if (Test-ServiceUp $OldPort) {
        $Busy = Get-BusyTaskCount $OldPort
        if ($Busy -ne 0 -and $env:FLUENTFLOW_UPDATE_FORCE -ne "1") {
            $What = if ($Busy -lt 0) { "The running FluentFlow could not say whether tasks are being processed" } else { "$Busy task(s) are still being processed" }
            throw "$What; run this again when they finish (or set FLUENTFLOW_UPDATE_FORCE=1 to stop them; they carry on after the restart)"
        }
        Write-Host "Stopping the running FluentFlow Local..."
        Get-NetTCPConnection -LocalPort ([int]$OldPort) -State Listen -ErrorAction SilentlyContinue |
            ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    }
    Stop-FolderPrograms $Dir
    # The optional pyannote speaker engine is not in the standard packages; a
    # user who installed it keeps it after the update.
    $HadPyannote = Test-Path (Join-Path $Dir ".venv\Lib\site-packages\pyannote")
    if (Test-Path (Join-Path $Dir ".env")) { Copy-Item -Force (Join-Path $Dir ".env") (Join-Path "$Dir.new" ".env") }
    if (Test-Path "$Dir.old") { Remove-Item -Recurse -Force -LiteralPath "$Dir.old" }
    Set-Location (Split-Path -Parent $Dir)
    try {
        Move-Item -LiteralPath $Dir -Destination "$Dir.old" -ErrorAction Stop
    } catch {
        throw "Could not move $Dir aside ($($_.Exception.Message)). A window or program is using it; close it (or run this from another folder) and run this again"
    }
  } catch {
    Remove-Item -Recurse -Force -LiteralPath "$Dir.new" -ErrorAction SilentlyContinue
    throw "$($_.Exception.Message). Nothing was changed; FluentFlow Local $Current is still in $Dir."
  }
    try {
        Move-Item -LiteralPath "$Dir.new" -Destination $Dir -ErrorAction Stop
    } catch {
        Move-Item -LiteralPath "$Dir.old" -Destination $Dir -ErrorAction SilentlyContinue
        throw "Could not put the new version in place ($($_.Exception.Message)); FluentFlow Local $Current is back in $Dir."
    }
}

. (Join-Path $Dir "launchers\windows\fluentflow-env.ps1")
if ($OneFolder) {
    # Written before the setup script runs, which reads them (as does the
    # service) to put FFmpeg, Node.js, the models and the caches in this folder.
    # A line already in .env (set by the user, or an earlier run) is kept.
    Add-FluentFlowEnvLine $Dir "FLUENTFLOW_HOME" $Root
    Add-FluentFlowEnvLine $Dir "FLUENTFLOW_DATA_DIR" (Join-Path $Root "data")
    Add-FluentFlowEnvLine $Dir "HF_HOME" (Join-Path $Root "models")
    Add-FluentFlowEnvLine $Dir "FLUENTFLOW_LINK_OUTPUT_DIR" (Join-Path $Root "downloads")
    Add-FluentFlowEnvLine $Dir "PIP_CACHE_DIR" (Join-Path $Root "cache\pip")
    Add-FluentFlowEnvLine $Dir "UV_CACHE_DIR" (Join-Path $Root "cache\uv")
    Add-FluentFlowEnvLine $Dir "npm_config_cache" (Join-Path $Root "cache\npm")
}
$Port = Get-FluentFlowPort $Dir

try {
    & powershell -ExecutionPolicy Bypass -File (Join-Path $Dir "launchers\windows\setup-local.ps1") @args
    if ($LASTEXITCODE -ne 0) { throw "The setup script failed; see the lines above." }
    if ($Updating -and $HadPyannote) {
        Write-Host "Reinstalling the optional pyannote speaker engine you had..."
        & (Join-Path $Dir ".venv\Scripts\python.exe") -m pip install -q --disable-pip-version-check -r (Join-Path $Dir "requirements-speaker.txt")
        if ($LASTEXITCODE -ne 0) { throw "Reinstalling pyannote failed." }
    }

    $Python = Join-Path $Dir ".venv\Scripts\python.exe"
    $McpScript = Join-Path $Dir "scripts\fluentflow_mcp_server.py"

    Write-Host "== Starting FluentFlow Local =="
    function Test-Healthy {
        try { return (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:$Port/health").Content -match '"edition":"local"' } catch { return $false }
    }
    if (-not (Test-Healthy)) {
        $Launcher = Join-Path ([Environment]::GetFolderPath("Desktop")) "FluentFlow Local.cmd"
        if (Test-Path $Launcher) {
            Start-Process $Launcher
        } else {
            $LogDir = Join-Path $env:LOCALAPPDATA "FluentFlow\Logs"
            New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
            Start-FluentFlowService $Dir $Python $Port (Join-Path $LogDir "local.log") | Out-Null
        }
        for ($i = 0; $i -lt 90 -and -not (Test-Healthy); $i++) { Start-Sleep -Seconds 1 }
        if (-not (Test-Healthy)) { throw "The service did not start within 90 seconds." }
    }
    Write-Host "Running at http://127.0.0.1:$Port"

    # The MCP server reads the access token from the repository .env, so registering
    # it carries no secret.
    Write-Host "== Registering the MCP tools =="
    $CodexToml = "[mcp_servers.fluentflow-local]`ncommand = '$Python'`nargs = ['$McpScript']"
    # The MCP server reaches 8000 by itself; another port has to be told.
    $ClaudeEnv = @()
    $CodexEnv = @()
    if ($Port -ne "8000") {
        $ClaudeEnv = @("-e", "FLUENTFLOW_API_BASE=http://127.0.0.1:$Port")
        $CodexEnv = @("--env", "FLUENTFLOW_API_BASE=http://127.0.0.1:$Port")
        $CodexToml += "`nenv = { FLUENTFLOW_API_BASE = 'http://127.0.0.1:$Port' }"
    }
    $Registered = $false
    if (Get-Command claude -ErrorAction SilentlyContinue) {
        if ((Invoke-FluentFlowTool claude @("mcp", "get", "fluentflow-local")).Code -eq 0) { Write-Host "Claude Code: fluentflow-local is already registered." }
        else {
            $Added = Invoke-FluentFlowTool claude (@("mcp", "add", "--scope", "user", "fluentflow-local") + $ClaudeEnv + @("--", $Python, $McpScript))
            if ($Added.Code -eq 0) { Write-Host "Claude Code: registered fluentflow-local." }
            else { Write-Host "Claude Code: registration failed ($($Added.Output.Trim())); run: claude mcp add --scope user fluentflow-local -- `"$Python`" `"$McpScript`"" }
        }
        $Registered = $true
    }
    if (Get-Command codex -ErrorAction SilentlyContinue) {
        $CodexConfig = Join-Path $env:USERPROFILE ".codex\config.toml"
        if ((Test-Path $CodexConfig) -and (Select-String -Path $CodexConfig -Pattern '^\[mcp_servers\.fluentflow-local\]' -Quiet)) {
            Write-Host "Codex: fluentflow-local is already registered."
        } else {
            $Added = Invoke-FluentFlowTool codex (@("mcp", "add", "fluentflow-local") + $CodexEnv + @("--", $Python, $McpScript))
            if ($Added.Code -eq 0) { Write-Host "Codex: registered fluentflow-local." }
            else {
                Write-Host "Codex: could not register automatically ($($Added.Output.Trim())); add these lines to $CodexConfig :"
                Write-Host $CodexToml
            }
        }
        $Registered = $true
    }
    if (-not $Registered) {
        Write-Host "Neither claude nor codex was found. Register this command as an MCP server in your AI tool:"
        Write-Host "  `"$Python`" `"$McpScript`""
        Write-Host $CodexToml
    }

    # The skill tells the agent how to make a note and how to verify a rule before
    # changing how notes are written. Copied (links need administrator rights on
    # Windows); rerunning this script refreshes it. A folder of the same name that
    # this script did not write is left alone.
    Write-Host "== Installing the agent skill =="
    $SkillSrc = Join-Path $Dir "agent\skills\fluentflow"
    foreach ($ToolDir in @((Join-Path $env:USERPROFILE ".claude"), (Join-Path $env:USERPROFILE ".codex"))) {
        if (-not (Test-Path $ToolDir)) { continue }
        $Target = Join-Path $ToolDir "skills\fluentflow"
        $Marker = Join-Path $Target ".installed-by-fluentflow"
        if ((Test-Path $Target) -and -not (Test-Path $Marker)) { Write-Host "$Target exists and was not installed by FluentFlow; left alone."; continue }
        New-Item -ItemType Directory -Force -Path $Target | Out-Null
        Copy-Item -Force -Recurse (Join-Path $SkillSrc "*") $Target
        Set-Content -Path $Marker -Value "" -Encoding UTF8
        Write-Host "Installed $Target"
    }

    Write-Host "== Checking the MCP server =="
    & $Python (Join-Path $Dir "scripts\check_mcp_server.py") | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "The MCP server check failed; run $Python scripts\check_mcp_server.py for details." }
} catch {
    if (-not $Updating) { throw }
    $Reason = $_.Exception.Message
    Write-Host "The update failed ($Reason); putting $Current back..." -ForegroundColor Yellow
    Stop-FolderPrograms $Dir
    if (Test-Path "$Dir.failed") { Remove-Item -Recurse -Force -LiteralPath "$Dir.failed" -ErrorAction SilentlyContinue }
    Set-Location (Split-Path -Parent $Dir)
    Move-Item -LiteralPath $Dir -Destination "$Dir.failed"
    Move-Item -LiteralPath "$Dir.old" -Destination $Dir
    $Launcher = Join-Path ([Environment]::GetFolderPath("Desktop")) "FluentFlow Local.cmd"
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dir "launchers\windows\install-local-to-desktop.ps1") | Out-Null
    if (Test-Path $Launcher) { Start-Process $Launcher }
    throw "Updating to $Latest failed: $Reason. FluentFlow Local $Current is back in $Dir (the failed copy is in $Dir.failed)."
}
if ($Updating) {
    Remove-Item -Recurse -Force -LiteralPath "$Dir.old" -ErrorAction SilentlyContinue
    Write-Host "Updated to $Latest; the previous version was removed."
}

Write-Host ""
Write-Host "FluentFlow Local is installed and running at http://127.0.0.1:$Port"
Write-Host "Open it later from 'FluentFlow Local' on the Desktop. For a note, give your agent a video file or link in a new conversation; it transcribes with FluentFlow and writes the note itself, no API key needed."
