# Shared by the Windows installer, setup script, launcher and uninstaller.
# Dot-source it:  . (Join-Path $PSScriptRoot "fluentflow-env.ps1")
#
# Two things are decided here so every script agrees:
#
# Settings. A setting comes from the environment first, then from the last
# matching line of the repository's .env, the same rule as the service
# (backend/core/runtime_paths.py, load_project_env) and the macOS scripts
# (launchers/macos/fluentflow-env.sh). Where the data lives (FLUENTFLOW_DATA_DIR)
# and the models (HF_HOME) may be written only into .env by the installer; a
# script that looked at the environment alone downloaded FFmpeg into one folder
# while the service read another.
#
# Download hosts. Each download has its original host and a mirror in China
# serving the same bytes. The original is probed for a few seconds; when it
# fails or is plainly slow, the faster host goes first (the same rule as
# backend/core/download_source.py). The other is still tried when the first
# fails, and every executable is checked against a pinned SHA-256.

# Windows PowerShell 5.1 on an older .NET offers only TLS 1.0/1.1 by default.
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {}

# Runs an agent's command line tool (claude, codex) and returns its exit code
# and everything it printed. npm installs them on Windows as PowerShell shims,
# and under ErrorActionPreference=Stop a line such a shim writes to stderr
# ("No MCP server named ...", the normal answer before registering) becomes a
# terminating error: the installer stopped at its last step, and the
# uninstaller took a registered tool for an absent one.
function Invoke-FluentFlowTool([string]$Name, [string[]]$Arguments) {
    $Saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        # The program (claude.cmd, codex.exe), not the .ps1 shim PowerShell
        # prefers: a script's caller swallows the "--" that separates the MCP
        # server's command from the tool's own options.
        $Program = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        $Target = if ($Program) { $Program.Source } else { $Name }
        $Output = & $Target @Arguments 2>&1 | ForEach-Object { "$_" }
        return [pscustomobject]@{ Code = $LASTEXITCODE; Output = ($Output -join "`n") }
    } catch {
        return [pscustomobject]@{ Code = 1; Output = $_.Exception.Message }
    } finally {
        $ErrorActionPreference = $Saved
    }
}

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

function ConvertFrom-FluentFlowEnvValue([string]$Raw, [hashtable]$Earlier) {
    $Value = $Raw.Trim()
    if ($Value.Length -ge 1 -and ($Value[0] -eq '"' -or $Value[0] -eq "'")) {
        $Quote = $Value[0]
        $End = $Value.IndexOf($Quote, 1)
        if ($End -gt 0) {
            $Inner = $Value.Substring(1, $End - 1)
            if ($Quote -eq '"') { $Inner = $Inner.Replace('\n', "`n").Replace('\"', '"').Replace('\\', '\') }
            return Expand-FluentFlowReferences $Inner $Earlier
        }
    }
    return Expand-FluentFlowReferences (([regex]::Split($Value, '\s+#', 2)[0]).Trim()) $Earlier
}

# The value of a line in the repository's .env, ignoring the environment.
function Get-FluentFlowEnvFileValue([string]$Repo, [string]$Name) {
    $EnvFile = Join-Path $Repo ".env"
    if (-not (Test-Path -LiteralPath $EnvFile)) { return $null }
    # Every line in order, so a value can use an earlier one.
    $Values = @{}
    foreach ($Line in [System.IO.File]::ReadAllLines($EnvFile, [System.Text.Encoding]::UTF8)) {
        if ($Line -match '^\s*(export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
            $Values[$Matches[2]] = ConvertFrom-FluentFlowEnvValue $Matches[3] $Values
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

function Get-FluentFlowDataDir([string]$Repo) {
    $Setting = Get-FluentFlowSetting $Repo "FLUENTFLOW_DATA_DIR"
    if ($Setting) { return $Setting }
    return Join-Path $env:APPDATA "FluentFlow"
}

function Get-FluentFlowPort([string]$Repo) {
    $Setting = Get-FluentFlowSetting $Repo "FLUENTFLOW_LOCAL_PORT"
    if ($Setting -match '^\d+$') { return $Setting }
    return "8000"
}

# The settings the installer may have written into .env that the tools this
# process starts read from the environment: the data folder, the model cache,
# and the pip and npm caches (kept in the install folder, not on drive C).
function Import-FluentFlowEnv([string]$Repo) {
    foreach ($Name in @("FLUENTFLOW_DATA_DIR", "HF_HOME", "PIP_CACHE_DIR", "UV_CACHE_DIR", "npm_config_cache")) {
        $Value = Get-FluentFlowSetting $Repo $Name
        if ($Value) { [Environment]::SetEnvironmentVariable($Name, $Value, "Process") }
    }
}

# Appends NAME=value to .env unless the file already has the name. Single
# quotes keep a Windows path literal (no escapes, a " #" in it stays).
function Add-FluentFlowEnvLine([string]$Repo, [string]$Name, [string]$Value) {
    if (Get-FluentFlowEnvFileValue $Repo $Name) { return }
    $EnvFile = Join-Path $Repo ".env"
    $Written = if ($Value.Contains("'")) { $Value } else { "'$Value'" }
    $Lead = ""
    if ((Test-Path -LiteralPath $EnvFile) -and (Get-Item -LiteralPath $EnvFile).Length -gt 0) {
        $Text = [System.IO.File]::ReadAllText($EnvFile)
        if (-not $Text.EndsWith("`n")) { $Lead = "`r`n" }
    }
    [System.IO.File]::AppendAllText($EnvFile, "$Lead$Name=$Written`r`n", (New-Object System.Text.UTF8Encoding $false))
}

$script:FluentFlowProbeBytes = 1MB
$script:FluentFlowProbeSeconds = 5

# How many bytes the host hands over within five seconds, at most 1 MB; a
# smaller file read to its end in time counts as the full 1 MB.
function Measure-FluentFlowSource([string]$Url) {
    $Budget = $script:FluentFlowProbeBytes
    $Got = 0
    $Watch = [Diagnostics.Stopwatch]::StartNew()
    $Response = $null
    try {
        $Request = [System.Net.HttpWebRequest]::Create($Url)
        $Request.Timeout = $script:FluentFlowProbeSeconds * 1000
        $Request.ReadWriteTimeout = $script:FluentFlowProbeSeconds * 1000
        $Request.UserAgent = "FluentFlow-Local"
        $Request.AddRange(0, $Budget - 1)
        $Response = $Request.GetResponse()
        $Stream = $Response.GetResponseStream()
        $Buffer = New-Object byte[] 65536
        while ($Got -lt $Budget -and $Watch.Elapsed.TotalSeconds -lt $script:FluentFlowProbeSeconds) {
            $Read = $Stream.Read($Buffer, 0, $Buffer.Length)
            if ($Read -le 0) {
                if ($Got -gt 0) { $Got = $Budget }
                break
            }
            $Got += $Read
        }
    } catch {
    } finally {
        if ($Response) { $Response.Close() }
    }
    return $Got
}

# The URLs, original first, reordered so the one to try first leads.
function Get-FluentFlowSourceOrder([string[]]$Urls) {
    if ($Urls.Count -lt 2) { return ,$Urls }
    $First = Measure-FluentFlowSource $Urls[0]
    if ($First -ge $script:FluentFlowProbeBytes) { return ,$Urls }
    $Delivered = @{ $Urls[0] = $First }
    foreach ($Url in $Urls[1..($Urls.Count - 1)]) { $Delivered[$Url] = Measure-FluentFlowSource $Url }
    # Sort-Object -Stable is PowerShell 7 only; the index keeps ties in order.
    $Ordered = for ($i = 0; $i -lt $Urls.Count; $i++) {
        [pscustomobject]@{ Url = $Urls[$i]; Got = $Delivered[$Urls[$i]]; Index = $i }
    }
    return ,@($Ordered | Sort-Object @{ Expression = "Got"; Descending = $true }, Index | ForEach-Object { $_.Url })
}

function Get-FluentFlowHost([string]$Url) { return ([Uri]$Url).Host }

# Downloads the first host's copy that matches the SHA-256 into $OutFile;
# throws when none does.
function Save-FluentFlowVerifiedDownload([string[]]$Urls, [string]$OutFile, [string]$Sha256) {
    $Errors = @()
    $Saved = $ProgressPreference
    # Windows PowerShell 5.1 redraws its progress bar for every block it reads,
    # which makes a 30 MB download take minutes.
    $ProgressPreference = "SilentlyContinue"
    try {
        foreach ($Url in (Get-FluentFlowSourceOrder $Urls)) {
            $Partial = "$OutFile.part"
            try {
                Write-Host "  从 $(Get-FluentFlowHost $Url) 下载…"
                Invoke-WebRequest -UseBasicParsing -TimeoutSec 600 -Uri $Url -OutFile $Partial
                $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Partial).Hash.ToLower()
                if ($Hash -ne $Sha256) { throw "校验不对（$Hash）" }
                Move-Item -Force -LiteralPath $Partial -Destination $OutFile
                return
            } catch {
                $Errors += "$(Get-FluentFlowHost $Url)：$($_.Exception.Message)"
                Remove-Item -Force -LiteralPath $Partial -ErrorAction SilentlyContinue
            }
        }
    } finally {
        $ProgressPreference = $Saved
    }
    throw ($Errors -join "；")
}

# Starts the service in the background and returns its process id. The process
# is created by Windows' own process service (WMI), not as a child of this
# script: closing the launcher window does not stop it, and neither does an
# agent's shell ending (a friend's agent started the service in a temporary
# window, the window was reclaimed, and the agent took FluentFlow for broken,
# 2026-11). Such a process gets the user's saved environment, so this window's
# (keys set in it, a PATH with FFmpeg, a data folder, a test copy) goes over in
# a file in the user's own temp folder that scripts/run_service.py reads and
# deletes; on the command line, keys would show in any process listing.
# Output goes to the log; stop it with stop-local.ps1.
function Start-FluentFlowService([string]$Repo, [string]$Python, [string]$Port, [string]$LogFile) {
    $EnvFile = Join-Path ([System.IO.Path]::GetTempPath()) ("fluentflow-env-" + [guid]::NewGuid() + ".json")
    $Snapshot = @{}
    Get-ChildItem Env: | ForEach-Object { $Snapshot[$_.Name] = $_.Value }
    [System.IO.File]::WriteAllText($EnvFile, ($Snapshot | ConvertTo-Json -Compress), (New-Object System.Text.UTF8Encoding $false))
    $Runner = Join-Path $Repo "scripts\run_service.py"
    $Command = "cmd.exe /d /s /c `"set PYTHONUTF8=1&& `"$Python`" `"$Runner`" --env-file `"$EnvFile`" --port $Port >> `"$LogFile`" 2>&1`""
    try {
        $Startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
        $Result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
            CommandLine = $Command; CurrentDirectory = $Repo; ProcessStartupInformation = $Startup
        }
        if ($Result.ReturnValue -ne 0) { throw "Windows 没能启动服务（Win32_Process.Create 返回 $($Result.ReturnValue)）" }
        # The service deletes the file once it has read it; one still there
        # after half a minute was never read, and it holds this window's keys.
        for ($i = 0; $i -lt 60 -and (Test-Path $EnvFile); $i++) { Start-Sleep -Milliseconds 500 }
        return $Result.ProcessId
    } finally {
        Remove-Item -Force $EnvFile -ErrorAction SilentlyContinue
    }
}

function Test-FluentFlowHealthy([string]$Port) {
    try {
        $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
        return ($Health.edition -eq "local" -or $Health.runtime.execution -eq "local")
    } catch { return $false }
}

# The proxy set in Windows' Internet settings, for the tools this process starts
# when no HTTP(S)_PROXY is set. Downloads made by PowerShell itself already use
# it; npm, uv and git do not, and a friend behind a local proxy
# (127.0.0.1:7897) had to add it to every command by hand (2026-11). Local
# addresses stay direct.
function Use-FluentFlowSystemProxy {
    if ($env:HTTPS_PROXY -or $env:HTTP_PROXY -or $env:https_proxy -or $env:http_proxy) { return }
    try {
        $Settings = Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" -ErrorAction Stop
    } catch { return }
    if (-not $Settings.ProxyEnable -or -not $Settings.ProxyServer) { return }
    # "host:port" for every scheme, or "http=host:port;https=host:port;...".
    $Server = $Settings.ProxyServer
    if ($Server -match '=') {
        $Parts = @{}
        foreach ($Part in $Server -split ';') { $Pair = $Part -split '=', 2; if ($Pair.Count -eq 2) { $Parts[$Pair[0].Trim().ToLower()] = $Pair[1].Trim() } }
        $Server = if ($Parts["https"]) { $Parts["https"] } elseif ($Parts["http"]) { $Parts["http"] } else { $null }
    }
    if (-not $Server) { return }
    if ($Server -notmatch '://') { $Server = "http://$Server" }
    $env:HTTP_PROXY = $Server
    $env:HTTPS_PROXY = $Server
    $env:NO_PROXY = "127.0.0.1,localhost,::1"
    Write-Host "用系统设置里的代理：$Server"
}

# How many tasks are still working, as launchers/macos/count_active_jobs.py
# counts them: each task's activity.busy (a task is "completed" once its
# transcript is stored while its cut and note may run for many minutes), asked
# under both scopes the local edition uses. -1 when the service cannot say.
function Get-FluentFlowBusyCount([string]$Port) {
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
