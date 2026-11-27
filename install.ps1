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

# Reading .env and telling where a setting puts files: the exact text of the
# functions of the same name in launchers/windows/fluentflow-env.ps1
# (tests/test_install_places.py checks), repeated because until the update has
# swapped the folders neither copy's scripts can be relied on (the old one may
# predate them, the new one is not in place yet).
# ${NAME} and ${NAME:-default}, from the environment or an earlier line of
# the same .env, as the service's reader (python-dotenv's rule) does.
function Expand-FluentFlowReferences([string]$Value, [hashtable]$Earlier) {
    return [regex]::Replace($Value, '\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}', {
        param($Match)
        $Name = $Match.Groups[1].Value
        $Found = [Environment]::GetEnvironmentVariable($Name)
        if (-not $Found -and $Earlier -and $Earlier.ContainsKey($Name)) { $Found = $Earlier[$Name] }
        if ($Found) { return $Found }
        return $Match.Groups[2].Value
    })
}

# The text inside a leading quote, escapes decoded as the service decodes them
# (backend/core/runtime_paths.py, _quoted): \\ and \' in single quotes, the
# usual backslash escapes in double quotes ("D:\\notes" is D:\notes, "\t" a
# tab). $null when the quote is not closed on its line (a closing \' or \"):
# the service then reads the next line into the value.
function ConvertFrom-FluentFlowQuoted([string]$Value) {
    $Quote = $Value[0]
    $Out = New-Object System.Text.StringBuilder
    for ($i = 1; $i -lt $Value.Length; $i++) {
        $Char = $Value[$i]
        if ($Char -eq '\' -and $i + 1 -lt $Value.Length) {
            $Next = [string]$Value[$i + 1]
            $Decoded = $null
            if ($Quote -eq "'") {
                if ($Next -ceq '\' -or $Next -ceq "'") { $Decoded = $Next }
            } else {
                switch -CaseSensitive ($Next) {
                    'n' { $Decoded = "`n" } 'r' { $Decoded = "`r" } 't' { $Decoded = "`t" } 'a' { $Decoded = "`a" }
                    'b' { $Decoded = "`b" } 'f' { $Decoded = "`f" } 'v' { $Decoded = "`v" }
                    '\' { $Decoded = '\' } "'" { $Decoded = "'" } '"' { $Decoded = '"' }
                }
            }
            if ($null -ne $Decoded) { [void]$Out.Append($Decoded); $i++; continue }
        }
        if ($Char -eq $Quote) { return $Out.ToString() }
        [void]$Out.Append($Char)
    }
    return $null
}

# A value as the service reads it; $null when its quote is not closed.
function ConvertFrom-FluentFlowEnvValue([string]$Raw, [hashtable]$Earlier) {
    $Value = $Raw.Trim()
    if ($Value.Length -ge 1 -and ($Value[0] -eq '"' -or $Value[0] -eq "'")) {
        $Inner = ConvertFrom-FluentFlowQuoted $Value
        if ($null -eq $Inner) { return $null }
        return Expand-FluentFlowReferences $Inner $Earlier
    }
    return Expand-FluentFlowReferences (([regex]::Split($Value, '\s+#', 2)[0]).Trim()) $Earlier
}

# The value of a line in the repository's .env, ignoring the environment, read
# as the service reads it: every line in order, so a value can use an earlier
# one. What cannot be read the way the service reads it comes back starting
# with $? (Resolve-FluentFlowPlace then reports it unsure): a quote not closed
# on its line, and every line after it (the service reads them into that
# value), and anything in a file that is not UTF-8 (read with U+FFFD in it).
function Get-FluentFlowEnvFileValue([string]$Repo, [string]$Name) {
    $EnvFile = Join-Path $Repo ".env"
    if (-not (Test-Path -LiteralPath $EnvFile)) { return $null }
    $Lines = [System.IO.File]::ReadAllLines($EnvFile, [System.Text.Encoding]::UTF8)
    $Broken = @($Lines | Where-Object { $_.Contains([char]0xFFFD) }).Count -gt 0
    $Values = @{}
    foreach ($Line in $Lines) {
        if ($Line -match '^\s*(export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
            $Key = $Matches[2]
            $RawValue = $Matches[3]
            $Read = if ($Broken) { $null } else { ConvertFrom-FluentFlowEnvValue $RawValue $Values }
            if ($null -eq $Read) {
                $Broken = $true
                $Read = '$?' + $RawValue.Trim()
            }
            $Values[$Key] = $Read
        }
    }
    if ($Values[$Name]) { return $Values[$Name] }
    return $null
}

function Get-FluentFlowSetting([string]$Repo, [string]$Name) {
    $FromEnvironment = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ($FromEnvironment) { return $FromEnvironment }
    return Get-FluentFlowEnvFileValue $Repo $Name
}

# Where a full path really lands, the way the system opens it: each part that
# exists is followed through a link or junction, and ".." backs up from the
# folder the system is really in (after a link, ".." is the link target's
# parent, not where the text suggests). The service hands paths to the system as
# written, so a script that only normalized the text could put files where the
# service does not look (a symlinked code folder with "..\data", 2026-11-26).
# Anything unexpected leaves the path as it came in.
function Get-FluentFlowRealPath([string]$Path, [int]$Depth = 0) {
    if (-not $Path -or $Depth -gt 8) { return $Path }
    $Text = $Path -replace '/', '\'
    $Root = $null
    $Rest = $Text
    if ($Text -match '^([A-Za-z]:)\\') {
        $Root = $Matches[1] + '\'
        $Rest = $Text.Substring(3)
    } elseif ($Text -match '^\\\\([^\\]+)\\([^\\]+)') {
        $Root = "\\$($Matches[1])\$($Matches[2])"
        $Rest = $Text.Substring($Root.Length).TrimStart('\')
    }
    if (-not $Root) { return $Text }
    $Cur = $Root
    while ($Rest) {
        $Cut = $Rest.IndexOf('\')
        if ($Cut -lt 0) { $Part = $Rest; $Rest = '' } else { $Part = $Rest.Substring(0, $Cut); $Rest = $Rest.Substring($Cut + 1) }
        if (-not $Part -or $Part -eq '.') { continue }
        if ($Part -eq '..') {
            $Up = [System.IO.Path]::GetDirectoryName($Cur.TrimEnd('\'))
            $Cur = if ($Up) { $Up } else { $Root }
            continue
        }
        $Cur = if ($Cur.EndsWith('\')) { $Cur + $Part } else { "$Cur\$Part" }
        $Target = Get-FluentFlowLinkTarget $Cur
        if ($Target) {
            if (-not [System.IO.Path]::IsPathRooted($Target)) {
                $Target = Join-Path (Split-Path -Parent $Cur) $Target
            }
            $Cur = Get-FluentFlowRealPath ([System.IO.Path]::GetFullPath($Target)) ($Depth + 1)
        }
    }
    return $Cur
}

# Where a link or junction points, or $null for anything else. Windows
# PowerShell 5.1 does not always expose a junction's target as a property, so a
# reparse point without one is asked with fsutil (what the system itself has).
function Get-FluentFlowLinkTarget([string]$Path) {
    $Item = $null
    try { $Item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop } catch { return $null }
    $IsLink = $false
    try { $IsLink = [bool]($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) } catch {}
    if (-not $IsLink) { return $null }
    $Target = [string](@($Item.Target) | Where-Object { $_ } | Select-Object -First 1)
    if (-not $Target) {
        $Line = @(& fsutil.exe reparsepoint query "$Path" 2>$null | Where-Object { $_ -match 'Substitute Name:' } | Select-Object -First 1)
        if ($Line.Count -gt 0) { $Target = ($Line[0] -replace '.*Substitute Name:\s*', '').Trim() }
    }
    # fsutil prints the path as \??\C:\..., the form the system uses.
    if ($Target.StartsWith('\??\')) { $Target = $Target.Substring(4) }
    if ($Target) { return $Target }
    return $null
}

# Where the service puts a path setting: Path, the full path, and Sure,
# whether that can be told. The service reads the value with its ${NAME}
# references expanded (Get-FluentFlowSetting), turns a leading ~ into the
# user's folder, and takes a relative path from the code folder, which it runs
# in (scripts/run_service.py); a path starting with a single \ is on the code
# folder's drive. A $ left after expanding ($HOME, which the service takes
# literally), a drive-relative D:data or a path Windows rejects cannot be
# placed for sure: callers treat it as possibly inside the code folder.
function Resolve-FluentFlowPlace([string]$Repo, [string]$Value) {
    if (-not $Value) { return $null }
    $Path = $Value
    if ($Path -eq '~' -or $Path -match '^~[\\/]') { $Path = $env:USERPROFILE + $Path.Substring(1) }
    $Sure = -not $Path.Contains('$')
    if ($Path -match '^[A-Za-z]:(?![\\/])') { return [pscustomobject]@{ Path = $Path; Sure = $false } }
    if ($Path -match '^[\\/](?![\\/])') { $Path = [System.IO.Path]::GetPathRoot($Repo).TrimEnd('\', '/') + $Path }
    elseif (-not [System.IO.Path]::IsPathRooted($Path)) { $Path = $Repo.TrimEnd('\', '/') + '\' + $Path }
    # Walked as written: GetFullPath would collapse ".." by the text, past a link
    # to somewhere else, and hand the walker a path that no link is left in.
    try { $Path = Get-FluentFlowRealPath $Path } catch { return [pscustomobject]@{ Path = $Path; Sure = $false } }
    if ($Path.Length -gt 3) { $Path = $Path.TrimEnd('\') }
    return [pscustomobject]@{ Path = $Path; Sure = $Sure }
}

# Whether a full path is the folder or inside it, ignoring case as Windows
# does; when either cannot be read as a path, it counts as inside.
function Test-FluentFlowInside([string]$Path, [string]$Folder) {
    if (-not $Path -or -not $Folder) { return $false }
    try {
        $Base = [System.IO.Path]::GetFullPath($Folder).TrimEnd('\')
        $Full = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    } catch { return $true }
    return $Full.Equals($Base, [StringComparison]::OrdinalIgnoreCase) -or $Full.StartsWith($Base + '\', [StringComparison]::OrdinalIgnoreCase)
}

# An update's copies beside the install (.new, .old, .failed) carry a marker
# naming the place the update put them. Only a copy whose marker names where
# it is now is the update's to remove: a copy or rename the user made of the
# code folder names another place, or has no marker (the copy in use has none).
function Set-FluentFlowUpdateCopyAs([string]$Folder, [string]$PlacedAt) {
    $Lines = @("Set aside by a FluentFlow Local update; the next update or the uninstaller removes it.", [System.IO.Path]::GetFullPath($PlacedAt))
    [System.IO.File]::WriteAllLines((Join-Path $Folder ".fluentflow-update"), [string[]]$Lines, (New-Object System.Text.UTF8Encoding $false))
}

function Set-FluentFlowUpdateCopy([string]$Folder) {
    Set-FluentFlowUpdateCopyAs $Folder $Folder
}

function Test-FluentFlowUpdateCopy([string]$Folder) {
    $Marker = Join-Path $Folder ".fluentflow-update"
    if (-not (Test-Path -LiteralPath $Marker)) { return $false }
    $Said = @([System.IO.File]::ReadAllLines($Marker, [System.Text.Encoding]::UTF8) | Where-Object { $_.Trim() }) | Select-Object -Last 1
    if (-not $Said) { return $false }
    try {
        return [System.IO.Path]::GetFullPath($Said.Trim()).TrimEnd('\').Equals([System.IO.Path]::GetFullPath($Folder).TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)
    } catch { return $false }
}

function Get-FluentFlowUpdatingMarker([string]$InstallFolder) {
    return $InstallFolder.TrimEnd('\') + ".fluentflow-updating"
}

function Start-FluentFlowUpdating([string]$InstallFolder, [string]$What) {
    $Marker = Get-FluentFlowUpdatingMarker $InstallFolder
    $Busy = "Another update is running (or one was just killed): $Marker is still there. Let it finish, or delete that file once you are sure nothing is running; nothing was changed."
    $Text = @("FluentFlow Local is being updated ($What); do not start the service from this install while this file exists.", (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"))
    $Bytes = (New-Object System.Text.UTF8Encoding $false).GetBytes(($Text -join "`r`n") + "`r`n")
    for ($Try = 1; $Try -le 2; $Try++) {
        try {
            # CreateNew: making the file is the claim, in one step, so two updates
            # cannot both pass a check and then write (2026-11-27).
            $Stream = [System.IO.File]::Open($Marker, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
            try { $Stream.Write($Bytes, 0, $Bytes.Length) } finally { $Stream.Close() }
            return $Marker
        } catch {
            if (-not (Test-Path -LiteralPath $Marker)) { throw "Could not write ${Marker}: $($_.Exception.Message). Nothing was changed." }
            $Age = (Get-Date) - (Get-Item -LiteralPath $Marker).LastWriteTime
            if ($Age.TotalMinutes -lt 60 -or $Try -eq 2) { throw $Busy }
            Remove-Item -Force -LiteralPath $Marker -ErrorAction SilentlyContinue
            Write-Host "An earlier update was interrupted and left $Marker; this run installs it properly."
        }
    }
}

function Stop-FluentFlowUpdating([string]$Marker) {
    if ($Marker) { Remove-Item -Force -LiteralPath $Marker -ErrorAction SilentlyContinue }
}

# The folder asked for in FLUENTFLOW_INSTALL_DIR, as a full path with no
# trailing separator. Agents set it in PowerShell, which expands neither
# %USERPROFILE% nor '$env:USERPROFILE' in single quotes: both are expanded
# here, and a relative path is taken from the current folder. Taken as given, a
# relative folder put the data inside the code folder (the service runs there),
# and a trailing \ ('D:\FF\' in .env) made the service read the next line
# into the value. A drive's root is refused: uninstalling empties the folder.
function Get-FluentFlowInstallRoot([string]$Asked) {
    $Path = $Asked.Trim().Trim('"').Trim("'")
    $Path = [regex]::Replace($Path, '\$\{?env:([A-Za-z_][A-Za-z0-9_]*)\}?', {
        param($Match)
        $Found = [Environment]::GetEnvironmentVariable($Match.Groups[1].Value)
        if ($Found) { return $Found }
        return $Match.Value
    })
    $Path = [Environment]::ExpandEnvironmentVariables($Path)
    if ($Path -eq '~' -or $Path -match '^~[\\/]') { $Path = $env:USERPROFILE + $Path.Substring(1) }
    if ($Path.Contains('$') -or $Path -match '%[A-Za-z_][A-Za-z0-9_]*%') {
        throw "FLUENTFLOW_INSTALL_DIR ($Asked) names a variable that is not set; give the folder's full path, for example D:\FluentFlow."
    }
    # Through a link or a ".." the folder this really is, as the service will
    # open it (a symlinked parent with ".." in it, 2026-11-26). Walked as
    # written: GetFullPath would collapse ".." by the text first.
    if (-not [System.IO.Path]::IsPathRooted($Path)) { $Path = Join-Path (Get-Location).ProviderPath $Path }
    $Path = (Get-FluentFlowRealPath $Path).TrimEnd('\', '/')
    if (-not $Path -or $Path -match '^[A-Za-z]:$' -or $Path -match '^\\\\[^\\]+\\[^\\]+$') {
        throw "FLUENTFLOW_INSTALL_DIR ($Asked) is a whole drive; give a folder on it, for example $Path\FluentFlow."
    }
    return $Path
}

# Windows sometimes refuses to move a folder for a few seconds after the programs
# in it were stopped (a handle still closing, the indexer or antivirus reading
# it). Three tries a second apart; the last failure is the one reported.
function Move-FluentFlowFolder([string]$From, [string]$To) {
    for ($Try = 1; $Try -le 3; $Try++) {
        try {
            Move-Item -LiteralPath $From -Destination $To -ErrorAction Stop
            return
        } catch {
            if ($Try -eq 3) { throw }
            Start-Sleep -Seconds 1
        }
    }
}

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

# An install already here is found without being asked for: an earlier one-folder
# install's own folder, or the one the running service says it started from.
# This one keeps a second copy from landing in the default place when the user
# (or their agent) runs the install command again without the folder it was
# installed into (the page and README tell people to just run it again).
function Find-RunningRoot([string]$ProbePort) {
    try {
        $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$ProbePort/health" -TimeoutSec 3
    } catch {
        return $null
    }
    if (-not $Health -or $Health.edition -ne "local") { return $null }
    $Code = [string]$Health.install.code_dir
    if (-not $Code -or -not (Test-Path (Join-Path $Code "backend\local_main.py"))) { return $null }
    # A developer's own git checkout is theirs to update with git; installing the
    # release over it, or running its setup, is not what this command was asked to do.
    if (Test-Path -LiteralPath (Join-Path $Code ".git")) {
        Write-Host "The FluentFlow answering here runs from a git checkout ($Code); it is not taken over (update it with git pull)."
        return $null
    }
    $Code = (Get-FluentFlowRealPath ([System.IO.Path]::GetFullPath($Code))).TrimEnd('\')
    # The one-folder install's code is <root>\app, with the installer's marker beside it.
    $Parent = Split-Path -Parent $Code
    if ($Parent -and (Test-Path -LiteralPath (Join-Path $Parent ".fluentflow-home"))) { return $Parent }
    return $Code
}

$CurrentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$LegacyDir = Join-Path $env:USERPROFILE "fluentflow-local"
$Existing = $null
if (-not $env:FLUENTFLOW_INSTALL_DIR) {
    $Existing = Find-ExistingRoot
    if (-not $Existing) {
        $ProbePort = if ($env:FLUENTFLOW_LOCAL_PORT) { $env:FLUENTFLOW_LOCAL_PORT } else { "8000" }
        $Running = Find-RunningRoot $ProbePort
        if ($Running) {
            Write-Host "FluentFlow is running from $Running; updating that install (set FLUENTFLOW_INSTALL_DIR to install elsewhere)."
            $Existing = $Running
        }
    }
}
if ($env:FLUENTFLOW_INSTALL_DIR) {
    $Root = Get-FluentFlowInstallRoot $env:FLUENTFLOW_INSTALL_DIR
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
    if ((Test-Path $Marker) -and -not ((Get-Content -Raw $Marker) -match [regex]::Escape($CurrentSid))) {
        throw "$Root is another Windows account's FluentFlow (its tasks and access token). Install into a folder of your own (FLUENTFLOW_INSTALL_DIR)."
    }
    if ((Test-Path $Root) -and -not (Test-Path $Marker) -and -not $HasApp -and (Get-ChildItem -Force -LiteralPath $Root | Select-Object -First 1)) {
        throw "$Root already holds other files. Choose a new or empty folder for FluentFlow (FLUENTFLOW_INSTALL_DIR), for example $Root\FluentFlow."
    }
    # A folder this installer takes for the first time: new, or one that was
    # already there but empty (checked above). Either way it is made private.
    $FirstTake = -not (Test-Path $Marker)
    New-Item -ItemType Directory -Force -Path $Root | Out-Null
    if ($FirstTake) {
        # A drive's root lets every account on the computer read what is
        # created there, and an empty folder made beforehand inherits the same;
        # tasks, transcripts, the access token and saved keys are this user's.
        # Only this account, SYSTEM and Administrators get in, as in the user's
        # own profile folder.
        & icacls.exe $Root /inheritance:r /grant:r "*${CurrentSid}:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Host "Could not make $Root private to this account; other accounts on this computer may read it." }
    }
    if (-not (Test-Path $Marker)) { Set-Content -Path $Marker -Value "FluentFlow Local lives here; uninstalling removes this folder. Installed by $CurrentSid" -Encoding ASCII }
}
Write-Host "Installing into $Root"

# An update interrupted while the folders were swapped: the old version had
# been moved aside and the new one never got in, so the code folder is missing.
# Put the old version back; otherwise this run takes it for a fresh install and
# leaves the previous version, and the version the user was running, beside it.
if (-not (Test-Path -LiteralPath $Dir) -and (Test-FluentFlowUpdateCopy "$Dir.old")) {
    Write-Host "The previous update stopped while swapping folders; putting $Dir.old back as $Dir..."
    Move-FluentFlowFolder "$Dir.old" $Dir
    Remove-Item -Force -LiteralPath (Join-Path $Dir ".fluentflow-update") -ErrorAction SilentlyContinue
}

# The update's working folders beside the install. One already there is
# removed only when an earlier update put it there (Test-FluentFlowUpdateCopy):
# a folder the user copied or renamed to app.old is theirs.
function Assert-OwnLeftover([string]$Path) {
    if ((Test-Path $Path) -and -not (Test-FluentFlowUpdateCopy $Path)) {
        throw "$Path exists and was not made by a FluentFlow update. A copy an interrupted update left behind has no marker either: if it is this install's, delete it and run this again; if it is yours, move or rename it first"
    }
}

Write-Host "== Getting the code =="
# Held from the moment this run is an update; both staging paths claim it before
# touching $Dir.new (see Start-FluentFlowUpdating).
$UpdatingMarker = $null
function Claim-FluentFlowUpdating {
    if ($Updating -and -not $UpdatingMarker) { $script:UpdatingMarker = Start-FluentFlowUpdating $Root "to $Latest from $Current" }
}
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
  $Unzipped = $null
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
        else {
            Write-Host "FluentFlow Local $Current in $Dir is not older than $Latest in $LocalSource."
            if ($Unzipped) { Remove-Item -Recurse -Force -LiteralPath $Unzipped -ErrorAction SilentlyContinue }
        }
    }
    if (-not $HaveCode -or $Updating) {
        # Before $Dir.new is touched: a second run must not delete the copy this
        # one has already staged.
        Claim-FluentFlowUpdating
        $Target = if ($Updating) { "$Dir.new" } else { $Dir }
        if ($Updating) { Assert-OwnLeftover $Target }
        if (Test-Path $Target) { Remove-Item -Recurse -Force -LiteralPath $Target }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Target) | Out-Null
        # A copy that stops part way (disk full, window closed) is removed here: it
        # carries no marker yet (Set-FluentFlowUpdateCopy comes last), so the next
        # run would stop and ask about it.
        try {
            Copy-Item -Recurse -LiteralPath $LocalSource -Destination $Target -ErrorAction Stop
        } catch {
            Remove-Item -Recurse -Force -LiteralPath $Target -ErrorAction SilentlyContinue
            throw "Copying the code into $Target did not finish ($($_.Exception.Message)). Check the free space and run this again; nothing was changed."
        }
        Remove-Item -Recurse -Force -LiteralPath (Join-Path $Target ".git"), (Join-Path $Target ".venv"), (Join-Path $Target ".env"), (Join-Path $Target ".fluentflow-update") -ErrorAction SilentlyContinue
        if ($Unzipped) { Remove-Item -Recurse -Force -LiteralPath $Unzipped -ErrorAction SilentlyContinue }
        if ($Updating) { Set-FluentFlowUpdateCopy $Target }
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
    Claim-FluentFlowUpdating
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
    if ($Updating) { Set-FluentFlowUpdateCopy $Target }
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
# under both scopes the local edition uses. -1 when either scope cannot say.
function Get-BusyTaskCount([string]$Port) {
    $Counts = @()
    foreach ($Scope in @($null, "local-single-user")) {
        try {
            $Headers = @{}
            if ($Scope) { $Headers["X-FluentFlow-Client-Id"] = $Scope }
            $Jobs = (Invoke-RestMethod -Uri "http://127.0.0.1:$Port/jobs?view=activity&limit=100" -Headers $Headers -TimeoutSec 10).jobs
            $Counts += @($Jobs | Where-Object {
                if ($_.activity -and ($_.activity.busy -is [bool])) { $_.activity.busy }
                else { ($_.status -in @("queued", "processing", "running", "pending")) -or ($_.summary_status -eq "pending") -or ($_.result.debreath.status -eq "running") }
            }).Count
        } catch { return -1 }
    }
    return ($Counts | Measure-Object -Maximum).Maximum
}

$StoppedOld = $false
if ($Updating) {
  # Normally claimed before staging; kept here so a run that somehow got past
  # that point still holds it. Before anything is stopped: another agent's MCP
  # server that starts the service now would run a half-built copy.
  try {
    Claim-FluentFlowUpdating
  } catch {
    Remove-Item -Recurse -Force -LiteralPath "$Dir.new" -ErrorAction SilentlyContinue
    throw
  }
  try {
    foreach ($Leftover in @("$Dir.old", "$Dir.failed")) { Assert-OwnLeftover $Leftover }
    # Every place a setting can put the user's files: the data folder and each
    # of its parts (task database, sources, notes, edits...), link downloads,
    # the models. Placed as the service places them (Resolve-FluentFlowPlace: a
    # relative path, a ${NAME} reference, ~); one that cannot be placed for sure
    # counts as inside. FLUENTFLOW_INSTALL_DIR is where this install is.
    $Names = @("HF_HOME") + @(Get-ChildItem Env: | Where-Object { $_.Name -match '^FLUENTFLOW_\w*(DIR|PATH|CACHE)$' } | ForEach-Object { $_.Name })
    if (Test-Path (Join-Path $Dir ".env")) {
        $Names += @([System.IO.File]::ReadAllLines((Join-Path $Dir ".env"), [System.Text.Encoding]::UTF8) | ForEach-Object { if ($_ -match '^\s*(export\s+)?(FLUENTFLOW_\w*(DIR|PATH|CACHE))\s*=') { $Matches[2] } })
    }
    # The window's variable and the .env line are both checked: the service a
    # launcher starts later need not have this window's variables.
    foreach ($Name in ($Names | Where-Object { $_ -ne "FLUENTFLOW_INSTALL_DIR" } | Sort-Object -Unique)) {
        foreach ($Where in @([Environment]::GetEnvironmentVariable($Name, "Process"), (Get-FluentFlowEnvFileValue $Dir $Name))) {
            $Place = Resolve-FluentFlowPlace $Dir $Where
            if (-not $Place) { continue }
            if (-not $Place.Sure) {
                throw "$Name ($Where) cannot be placed the way the service reads it (a `$ that is not a `${NAME} reference, a quote not closed on its line, a file that is not UTF-8, or a drive without a folder); an update replaces the code folder, so it stops here. Write it as a full path outside $Dir"
            }
            if (Test-FluentFlowInside $Place.Path $Dir) {
                throw "$Name ($Where, that is $($Place.Path)) is inside the code folder, which an update replaces; move it out of $Dir first"
            }
        }
    }
    $OldPort = Get-InstalledPort $Dir
    $Listening = @(Get-NetTCPConnection -LocalPort ([int]$OldPort) -State Listen -ErrorAction SilentlyContinue).Count -gt 0
    if ($Listening -and -not (Test-ServiceUp $OldPort) -and $env:FLUENTFLOW_UPDATE_FORCE -ne "1") {
        throw "Something on port $OldPort is not answering like FluentFlow (busy, or another program); run this again later, or set FLUENTFLOW_UPDATE_FORCE=1 once you know it may be stopped"
    }
    if (Test-ServiceUp $OldPort) {
        $Busy = Get-BusyTaskCount $OldPort
        if ($Busy -ne 0 -and $env:FLUENTFLOW_UPDATE_FORCE -ne "1") {
            $What = if ($Busy -lt 0) { "The running FluentFlow could not say whether tasks are being processed" } else { "$Busy task(s) are still being processed" }
            throw "$What; run this again when they finish (or set FLUENTFLOW_UPDATE_FORCE=1 to stop them; they carry on after the restart)"
        }
        Write-Host "Stopping the running FluentFlow Local..."
        $StoppedOld = $true
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
    # The marker is written into the copy first, naming where it is going: losing
    # power between the two moves would otherwise leave the previous version
    # beside a missing code folder with no marker, and the next run would take
    # that for a fresh install and write a default .env over the lost settings.
    try { Set-FluentFlowUpdateCopyAs $Dir "$Dir.old" } catch {}
    try {
        Move-FluentFlowFolder $Dir "$Dir.old"
    } catch {
        Remove-Item -Force -LiteralPath (Join-Path $Dir ".fluentflow-update") -ErrorAction SilentlyContinue
        throw "Could not move $Dir aside ($($_.Exception.Message)). A window or program is using it; close it (or run this from another folder) and run this again"
    }
  } catch {
    $Why = $_.Exception.Message
    Remove-Item -Recurse -Force -LiteralPath "$Dir.new" -ErrorAction SilentlyContinue
    if ($StoppedOld) {
        # It was stopped for the swap that did not happen: start it again.
        $Launcher = Join-Path ([Environment]::GetFolderPath("Desktop")) "FluentFlow Local.cmd"
        if (Test-Path $Launcher) { Start-Process $Launcher }
    }
    Stop-FluentFlowUpdating $UpdatingMarker
    throw "$Why. Nothing was changed; FluentFlow Local $Current is still in $Dir."
  }
    try {
        Move-FluentFlowFolder "$Dir.new" $Dir
    } catch {
        Move-FluentFlowFolder "$Dir.old" $Dir
        Remove-Item -Force -LiteralPath (Join-Path $Dir ".fluentflow-update") -ErrorAction SilentlyContinue
        Stop-FluentFlowUpdating $UpdatingMarker
        throw "Could not put the new version in place ($($_.Exception.Message)); FluentFlow Local $Current is back in $Dir."
    }
    # The copy in use carries no marker: a copy the user makes of it later is not an update's.
    Remove-Item -Force -LiteralPath (Join-Path $Dir ".fluentflow-update") -ErrorAction SilentlyContinue
}

$Port = "8000"
try {
    # A release from before this installer (v0.11.0 and older) has neither its
    # scripts nor its layout: it is installed by the installer it came with.
    if (-not (Test-Path (Join-Path $Dir "launchers\windows\fluentflow-env.ps1"))) {
        Write-Host "FluentFlow Local $((Get-Content -Raw (Join-Path $Dir 'VERSION')).Trim()) predates this installer; running the installer that came with it."
        $env:FLUENTFLOW_INSTALL_DIR = $Dir
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dir "install.ps1")
        if ($LASTEXITCODE -ne 0) { throw "Its installer failed; see the lines above." }
        $Port = if ($env:FLUENTFLOW_LOCAL_PORT) { $env:FLUENTFLOW_LOCAL_PORT } else { "8000" }
    } else {
        . (Join-Path $Dir "launchers\windows\fluentflow-env.ps1")
        # The whole FluentFlow folder may have been moved to another drive since
        # the last run: .env still names the old place. Point it here again
        # before anything reads it (a value written as ${FLUENTFLOW_HOME}\… then
        # follows on its own).
        $Repaired = @(Repair-FluentFlowHome $Dir)
        if ($Repaired.Count -gt 0) { Write-Host "The FluentFlow folder moved; .env now points at $Root again ($($Repaired -join ', '))." }
        Add-FluentFlowFolderLines $Dir $Root $OneFolder
        $Port = Get-FluentFlowPort $Dir
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
        # it carries no secret. An entry already there is kept when it starts this copy
        # or another install that still exists; one whose files are gone (that install
        # was removed, or moved) is unregistered and registered again here, so an agent
        # never gets a dead entry. Removing uses the scope it was registered under
        # (`claude mcp get` prints it); only "user" would leave a project-level entry.
        Write-Host "== Registering the MCP tools =="
        $CodexToml = "[mcp_servers.fluentflow-local]`ncommand = '$Python'`nargs = ['$McpScript']"
        # The MCP server reaches 8000 by itself; another port has to be told.
        $ClaudeEnv = @()
        $CodexEnv = @()
        $ExpectedApiBase = ""
        if ($Port -ne "8000") {
            $ExpectedApiBase = "http://127.0.0.1:$Port"
            $ClaudeEnv = @("-e", "FLUENTFLOW_API_BASE=http://127.0.0.1:$Port")
            $CodexEnv = @("--env", "FLUENTFLOW_API_BASE=http://127.0.0.1:$Port")
            $CodexToml += "`nenv = { FLUENTFLOW_API_BASE = 'http://127.0.0.1:$Port' }"
        }
        # An entry of this copy that names another port (or none, when the port is
        # not 8000) is registered again: left as it is, the agent dials the old port.
        function Test-FluentFlowApiBaseDiffers([string]$Have) {
            if ($Have -eq "http://127.0.0.1:8000") { $Have = "" }
            return $Have -ne $ExpectedApiBase
        }
        $Registered = $false
        if (Get-Command claude -ErrorAction SilentlyContinue) {
            $Described = Invoke-FluentFlowTool claude @("mcp", "get", "fluentflow-local")
            $ExistingEntry = if ($Described.Code -eq 0) { $Described.Output } else { "" }
            # The file the entry starts (see Get-FluentFlowEntryScript). Empty when
            # the line cannot be understood: then nothing is removed.
            $Starts = Get-FluentFlowEntryScript $ExistingEntry
            $ClaudeApiBase = [regex]::Match($ExistingEntry, '(?m)^\s*FLUENTFLOW_API_BASE=(\S*)').Groups[1].Value
            $ThisCopy = $ExistingEntry -and $ExistingEntry.IndexOf($McpScript, [StringComparison]::OrdinalIgnoreCase) -ge 0
            if ($ThisCopy -and (Test-FluentFlowApiBaseDiffers $ClaudeApiBase)) {
                $Scope = "user"
                $ScopeLine = @($ExistingEntry -split "`n" | Where-Object { $_ -match 'claude mcp remove fluentflow-local -s ' }) | Select-Object -Last 1
                if ($ScopeLine -match '-s\s+([A-Za-z]+)') { $Scope = $Matches[1] }
                Invoke-FluentFlowTool claude @("mcp", "remove", "--scope", $Scope, "fluentflow-local") | Out-Null
                $Added = Invoke-FluentFlowTool claude (@("mcp", "add", "--scope", "user", "fluentflow-local") + $ClaudeEnv + @("--", $Python, $McpScript))
                if ($Added.Code -eq 0) { Write-Host "Claude Code: the port is now $Port; registered fluentflow-local again." }
                else { Write-Host "Claude Code: registration failed ($($Added.Output.Trim())); run: claude mcp add --scope user fluentflow-local $($ClaudeEnv -join ' ') -- `"$Python`" `"$McpScript`"" }
            } elseif ($ThisCopy) {
                Write-Host "Claude Code: fluentflow-local is already registered (this copy); left as it is."
            } elseif ($ExistingEntry -and -not $Starts) {
                Write-Host "Claude Code: fluentflow-local is registered but the files it starts cannot be read from its entry; left as it is."
            } elseif ($ExistingEntry -and (Test-Path -LiteralPath $Starts)) {
                Write-Host "Claude Code: fluentflow-local is registered to another install ($Starts); left as it is. Rerun after uninstalling that one to register this copy."
            } else {
                if ($ExistingEntry) {
                    $Scope = "user"
                    $ScopeLine = @($ExistingEntry -split "`n" | Where-Object { $_ -match 'claude mcp remove fluentflow-local -s ' }) | Select-Object -Last 1
                    if ($ScopeLine -match '-s\s+([A-Za-z]+)') { $Scope = $Matches[1] }
                    Invoke-FluentFlowTool claude @("mcp", "remove", "--scope", $Scope, "fluentflow-local") | Out-Null
                }
                $Added = Invoke-FluentFlowTool claude (@("mcp", "add", "--scope", "user", "fluentflow-local") + $ClaudeEnv + @("--", $Python, $McpScript))
                if ($Added.Code -eq 0) {
                    $What = if ($ExistingEntry) { "the old one started files that are gone; registered again" } else { "registered fluentflow-local" }
                    Write-Host "Claude Code: $What."
                } else { Write-Host "Claude Code: registration failed ($($Added.Output.Trim())); run: claude mcp add --scope user fluentflow-local -- `"$Python`" `"$McpScript`"" }
            }
            $Registered = $true
        }
        if (Get-Command codex -ErrorAction SilentlyContinue) {
            $CodexConfig = Join-Path (Get-FluentFlowCodexHome) "config.toml"
            $Section = ""
            if (Test-Path $CodexConfig) {
                $Toml = [System.IO.File]::ReadAllText($CodexConfig)
                $Section = [regex]::Match($Toml, '(?ms)^\[mcp_servers\.fluentflow-local\]\s*$.*?(?=^\[|\z)').Value
            }
            $Stale = $false
            $PortChanged = $false
            if ($Section -and $Section.IndexOf($McpScript, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                # codex mcp add --env writes a [mcp_servers.fluentflow-local.env] table;
                # a hand-written entry may say env = { FLUENTFLOW_API_BASE = "..." }.
                $EnvTable = [regex]::Match($Toml, '(?ms)^\[mcp_servers\.fluentflow-local\.env\]\s*$.*?(?=^\[|\z)').Value
                $CodexApiBase = [regex]::Match($EnvTable + "`n" + $Section, 'FLUENTFLOW_API_BASE\s*=\s*["'']([^"'']*)').Groups[1].Value
                $PortChanged = Test-FluentFlowApiBaseDiffers $CodexApiBase
            }
            if ($Section -and $Section.IndexOf($McpScript, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
                # Only a target that can be read and is gone counts: an entry this
                # cannot read may be another install's.
                $Target = [regex]::Match($Section, '(?m)^\s*args\s*=\s*\[\s*"([^"]*)"').Groups[1].Value
                if ($Target -and -not (Test-Path -LiteralPath $Target)) { $Stale = $true }
            }
            if ($Section -and -not $Stale -and -not $PortChanged) {
                Write-Host "Codex: fluentflow-local is already registered (this copy, or another install that still exists); left as it is."
            } else {
                if ($Stale -or $PortChanged) { Invoke-FluentFlowTool codex @("mcp", "remove", "fluentflow-local") | Out-Null }
                $Added = Invoke-FluentFlowTool codex (@("mcp", "add", "fluentflow-local") + $CodexEnv + @("--", $Python, $McpScript))
                if ($Added.Code -eq 0) {
                    $What = if ($PortChanged) { "the port is now $Port; registered again" } elseif ($Stale) { "the old one started files that are gone; registered again" } else { "registered fluentflow-local" }
                    Write-Host "Codex: $What."
                } else {
                    Write-Host "Codex: could not register automatically ($($Added.Output.Trim())); add these lines to $CodexConfig :"
                    Write-Host $CodexToml
                }
            }
            $Registered = $true
        }
        # OpenCode: only when it is here (the opencode command or its config folder),
        # and only the fluentflow-local entry is written (scripts\opencode_mcp_config.py;
        # exit 3 means OpenCode is not installed).
        & $Python (Join-Path $Dir "scripts\opencode_mcp_config.py") register --python $Python --script $McpScript --port $Port
        $OpenCodeExit = $LASTEXITCODE
        if ($OpenCodeExit -ne 3) { $Registered = $true }
        if ($OpenCodeExit -eq 1) { Write-Host "OpenCode: registration failed (see above); add this command to its MCP config: `"$Python`" `"$McpScript`"" }
        if (-not $Registered) {
            Write-Host "Neither claude, codex nor opencode was found. Register this command as an MCP server in your AI tool:"
            Write-Host "  `"$Python`" `"$McpScript`""
            Write-Host $CodexToml
        }

        # The skill tells the agent how to make a note and how to verify a rule before
        # changing how notes are written. Copied (links need administrator rights on
        # Windows); rerunning this script refreshes it. A folder of the same name that
        # this script did not write is left alone.
        Write-Host "== Installing the agent skill =="
        $SkillSrc = Join-Path $Dir "agent\skills\fluentflow"
        foreach ($ToolDir in @((Join-Path $env:USERPROFILE ".claude"), (Get-FluentFlowCodexHome))) {
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
    }
} catch {
    if (-not $Updating) { throw }
    $Reason = $_.Exception.Message
    Write-Host "The update failed ($Reason); putting $Current back..." -ForegroundColor Yellow
    Stop-FolderPrograms $Dir
    if (Test-Path "$Dir.failed") { Remove-Item -Recurse -Force -LiteralPath "$Dir.failed" -ErrorAction SilentlyContinue }
    Set-Location (Split-Path -Parent $Dir)
    # A folder that was just in use sometimes refuses to move for a few seconds;
    # Move-FluentFlowFolder tries again instead of leaving the install in two
    # halves (the swap below and the one before setup use it too).
    Move-FluentFlowFolder $Dir "$Dir.failed"
    try { Set-FluentFlowUpdateCopy "$Dir.failed" } catch {}
    Move-FluentFlowFolder "$Dir.old" $Dir
    Remove-Item -Force -LiteralPath (Join-Path $Dir ".fluentflow-update") -ErrorAction SilentlyContinue
    $Launcher = Join-Path ([Environment]::GetFolderPath("Desktop")) "FluentFlow Local.cmd"
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dir "launchers\windows\install-local-to-desktop.ps1") | Out-Null
    if (Test-Path $Launcher) { Start-Process $Launcher }
    Stop-FluentFlowUpdating $UpdatingMarker
    throw "Updating to $Latest failed: $Reason. FluentFlow Local $Current is back in $Dir (the failed copy is in $Dir.failed)."
}
if ($Updating) {
    Remove-Item -Recurse -Force -LiteralPath "$Dir.old" -ErrorAction SilentlyContinue
    Write-Host "Updated to $Latest; the previous version was removed."
} elseif ((Test-Path "$Dir.old") -and (Test-FluentFlowUpdateCopy "$Dir.old")) {
    # An earlier update swapped the folders and was interrupted afterwards; this
    # run found it working, so the old copy beside it goes.
    Remove-Item -Recurse -Force -LiteralPath "$Dir.old" -ErrorAction SilentlyContinue
    Write-Host "Removed the previous version an earlier update left beside the install."
}

Stop-FluentFlowUpdating $UpdatingMarker

Write-Host ""
Write-Host "FluentFlow Local is installed and running at http://127.0.0.1:$Port"
$DesktopLauncher = Join-Path ([Environment]::GetFolderPath("Desktop")) "FluentFlow Local.cmd"
if (Test-Path -LiteralPath $DesktopLauncher) { Write-Host -NoNewline "Open it later from 'FluentFlow Local' on the Desktop. " }
else { Write-Host -NoNewline "There is no Desktop icon; that is fine: the agent's tools start FluentFlow in the background when they need it. " }
Write-Host "For a note, give your agent a video file or link (in this conversation or a later one); it transcribes with FluentFlow and writes the note itself, no API key needed."
