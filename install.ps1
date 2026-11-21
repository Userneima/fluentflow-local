# One-command install of FluentFlow Local on Windows, for agents (people can run it too):
#
#   powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/Userneima/fluentflow-local/main/install.ps1 | iex"
#
# The whole product goes into one folder, FluentFlow, on the drive chosen:
#   app\        the code and its Python environment
#   data\       task history, notes, media, the downloaded FFmpeg and Node.js
#   models\     the transcription models (the Hugging Face cache, HF_HOME)
#   downloads\  videos fetched from links, with their notes
#   cache\      pip and npm download caches
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
$ErrorActionPreference = "Stop"

# Python's UTF-8 mode for every Python this starts (the setup script, the
# service, the MCP check), and the console decoding their output as UTF-8.
# Without it, an English Windows prints in cp1252 and the first Chinese line
# raises UnicodeEncodeError.
$env:PYTHONUTF8 = "1"
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}

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
    $Candidates = @(Join-Path $env:USERPROFILE "FluentFlow") + @(Get-FixedDrives | ForEach-Object { "$($_.Name)\FluentFlow" })
    foreach ($Candidate in $Candidates) {
        if ((Test-Path (Join-Path $Candidate "app\backend\local_main.py")) -or (Test-Path (Join-Path $Candidate "data\fluentflow_jobs.sqlite"))) {
            return $Candidate
        }
    }
    return $null
}

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
}
Write-Host "Installing into $Root"

Write-Host "== Getting the code =="
if (Test-Path (Join-Path $Dir "backend\local_main.py")) {
    Write-Host "Using the existing code in $Dir (to upgrade, follow 'Updating' in its README)."
} else {
    if (Test-Path $Dir) { throw "$Dir exists but is not a FluentFlow Local folder. Set FLUENTFLOW_INSTALL_DIR to another place." }
    $Tag = $env:FLUENTFLOW_INSTALL_REF
    if (-not $Tag) {
        # The releases/latest redirect, not the GitHub API, which limits anonymous calls.
        $Request = [System.Net.WebRequest]::Create("https://github.com/$RepoSlug/releases/latest")
        $Request.AllowAutoRedirect = $false
        $Response = $Request.GetResponse()
        $Tag = ($Response.Headers["Location"] -split "/tag/")[-1]
        $Response.Close()
        if (-not $Tag) { throw "Could not find the latest release; check that github.com is reachable." }
    }
    Write-Host "Downloading $Tag..."
    $Work = Join-Path ([System.IO.Path]::GetTempPath()) ("fluentflow-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $Work | Out-Null
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/$RepoSlug/archive/refs/tags/$Tag.zip" -OutFile (Join-Path $Work "src.zip")
        Expand-Archive -Path (Join-Path $Work "src.zip") -DestinationPath (Join-Path $Work "src")
        $Unpacked = Get-ChildItem (Join-Path $Work "src") -Directory | Select-Object -First 1
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Dir) | Out-Null
        Move-Item $Unpacked.FullName $Dir
    } finally {
        Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
    }
    Write-Host "Placed in $Dir"
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
    Add-FluentFlowEnvLine $Dir "npm_config_cache" (Join-Path $Root "cache\npm")
}
$Port = Get-FluentFlowPort $Dir

& powershell -ExecutionPolicy Bypass -File (Join-Path $Dir "launchers\windows\setup-local.ps1") @args
if ($LASTEXITCODE -ne 0) { throw "The setup script failed; see the lines above." }

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
        Start-Process -WindowStyle Hidden -WorkingDirectory $Dir $Python @("-m", "uvicorn", "backend.local_main:app", "--host", "127.0.0.1", "--port", $Port)
    }
    for ($i = 0; $i -lt 90 -and -not (Test-Healthy); $i++) { Start-Sleep -Seconds 1 }
    if (-not (Test-Healthy)) { throw "The service did not start within 90 seconds." }
}
Write-Host "Running at http://127.0.0.1:$Port"

# The MCP server reads the access token from the repository .env, so registering
# it carries no secret.
Write-Host "== Registering the MCP tools =="
$CodexToml = "[mcp_servers.fluentflow-local]`ncommand = '$Python'`nargs = ['$McpScript']"
$Registered = $false
if (Get-Command claude -ErrorAction SilentlyContinue) {
    & claude mcp get fluentflow-local *> $null
    if ($LASTEXITCODE -eq 0) { Write-Host "Claude Code: fluentflow-local is already registered." }
    else {
        & claude mcp add --scope user fluentflow-local -- $Python $McpScript | Out-Null
        if ($LASTEXITCODE -eq 0) { Write-Host "Claude Code: registered fluentflow-local." }
        else { Write-Host "Claude Code: registration failed; run: claude mcp add --scope user fluentflow-local -- `"$Python`" `"$McpScript`"" }
    }
    $Registered = $true
}
if (Get-Command codex -ErrorAction SilentlyContinue) {
    $CodexConfig = Join-Path $env:USERPROFILE ".codex\config.toml"
    if ((Test-Path $CodexConfig) -and (Select-String -Path $CodexConfig -Pattern '^\[mcp_servers\.fluentflow-local\]' -Quiet)) {
        Write-Host "Codex: fluentflow-local is already registered."
    } else {
        & codex mcp add fluentflow-local -- $Python $McpScript *> $null
        if ($LASTEXITCODE -eq 0) { Write-Host "Codex: registered fluentflow-local." }
        else { Write-Host "Codex: could not register automatically; add these lines to $CodexConfig :"; Write-Host $CodexToml }
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

Write-Host ""
Write-Host "FluentFlow Local is installed and running at http://127.0.0.1:$Port"
Write-Host "Open it later from 'FluentFlow Local' on the Desktop. For a note, give your agent a video file or link in a new conversation; it transcribes with FluentFlow and writes the note itself, no API key needed."
