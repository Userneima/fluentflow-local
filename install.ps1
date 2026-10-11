# One-command install of FluentFlow Local on Windows, for agents (people can run it too):
#
#   powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/Userneima/fluentflow-local/main/install.ps1 | iex"
#
# Downloads the latest release to %USERPROFILE%\fluentflow-local, runs the setup
# script, starts FluentFlow, registers its MCP tools with Claude Code and Codex when
# they are installed, and checks the MCP server. Nothing asks for administrator
# rights. An existing folder keeps its code; only the later steps run again.
#
# Optional environment variables:
#   FLUENTFLOW_INSTALL_DIR   where to install, default %USERPROFILE%\fluentflow-local
#   FLUENTFLOW_INSTALL_REF   which release tag (for example v0.7.0), default the latest
$ErrorActionPreference = "Stop"

$RepoSlug = "Userneima/fluentflow-local"
$Dir = if ($env:FLUENTFLOW_INSTALL_DIR) { $env:FLUENTFLOW_INSTALL_DIR } else { Join-Path $env:USERPROFILE "fluentflow-local" }
$Port = if ($env:FLUENTFLOW_LOCAL_PORT) { $env:FLUENTFLOW_LOCAL_PORT } else { "8000" }

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
