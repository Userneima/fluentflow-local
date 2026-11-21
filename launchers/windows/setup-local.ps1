# One-time Windows setup for FluentFlow Local.
# Creates the project virtual environment, installs project-owned GPU runtime
# DLLs when an NVIDIA adapter is present, builds the frontend, downloads the
# transcription model, and creates the Desktop launcher. It never installs a
# machine-wide CUDA Toolkit.
#
# It asks nothing along the way. Running this script is the decision; every
# question it could ask (Python, Node.js, FFmpeg, the model) has one sensible answer, and asking
# only chops the wait into pieces the user has to sit through. Missing pieces
# are installed, each step says what it is doing, and only a failure stops it.
#
# Nothing here asks for administrator rights, so an agent can run it unattended:
# Python is installed for the current user only, Node.js (needed only to build
# the interface) is a portable copy in the data folder, and FFmpeg is a
# download into the data folder's bin.
#
# Every download has a source in China (fluentflow-env.ps1): the original host
# is probed for a few seconds and, when it fails or is plainly slow, the mirror
# goes first. Python's installer and Node.js come from python.org / nodejs.org
# or npmmirror and are checked against pinned SHA-256s; pip uses Tsinghua's
# index, npm npmmirror's registry, the transcription model hf-mirror, the
# speaker models ModelScope and hf-mirror. A failed pip or npm run is retried
# once from the other source. A PIP_INDEX_URL or npm registry you set yourself
# is left alone.
#
# Where things go follows the repository's .env as the service reads it: the
# one-folder install (install.ps1) writes the data folder, the model cache and
# the pip and npm caches there, so nothing large lands on drive C.
#
# Python dependencies are installed from requirements-local.txt (lower bounds,
# newest matching versions). There is no tested lock file for Windows yet; the
# only lock in the repository, requirements-local.lock.macos-arm64.txt, is for
# Apple Silicon and must not be used here.
#
#   ... -SkipModel   do not download the transcription model
param([switch]$SkipModel)

$ErrorActionPreference = "Stop"

# Python's UTF-8 mode for every Python started here, and the console decoding
# what they print as UTF-8. With output going to a pipe (an agent running this
# script), Python otherwise prints in the code page: on an English Windows,
# cp1252, the readiness check's first Chinese line raised UnicodeEncodeError
# and failed the install at its last step.
$env:PYTHONUTF8 = "1"
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$Venv = Join-Path $Repo ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"

. (Join-Path $ScriptDir "fluentflow-env.ps1")
Import-FluentFlowEnv $Repo

function Invoke-Checked([string]$FilePath, [string[]]$Arguments) {
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "命令失败（退出码 ${LASTEXITCODE}）：$FilePath $($Arguments -join ' ')"
    }
}

# A package winget just installed is not on this session's PATH until it is
# re-read from the registry; without this the next Get-Command misses it and the
# script tells the user to install the thing it installed a moment ago.
function Update-SessionPath {
    $env:Path = @(
        [Environment]::GetEnvironmentVariable("Path", "Machine"),
        [Environment]::GetEnvironmentVariable("Path", "User")
    ) -join ";"
}

# Same treatment as FFmpeg below: required, one sensible answer, so install it
# with winget instead of stopping to send the user off to a download page.
function Install-WithWinget([string]$Id, [string]$What, [string[]]$Extra = @()) {
    $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $Winget) {
        throw "没找到 ${What}，也没有 winget 可以自动安装。请自己安装 ${What}，然后重新运行本脚本。"
    }
    Write-Host "没找到 ${What}，正在用 winget 安装…"
    Invoke-Checked $Winget.Source (@(
        "install", "--id", $Id, "-e", "--source", "winget",
        "--accept-source-agreements", "--accept-package-agreements"
    ) + $Extra)
    Update-SessionPath
}

# pip's index: PyPI, or Tsinghua's mirror when PyPI is unreachable or slow
# (decided once, before the first pip run); a failed run is retried once from
# the other. An index the user set is used as it is.
$PypiIndex = "https://pypi.org/simple"
$TunaIndex = "https://pypi.tuna.tsinghua.edu.cn/simple"
$UserPipIndex = [bool]$env:PIP_INDEX_URL
$PipIndex = $null

function Select-PipIndex {
    if ($UserPipIndex) { return }
    # A 2 MB wheel, at the same path on both hosts.
    $Wheel = "packages/8a/6a/19e9fe04fca059ccf770861c7d5721ab4c2aebc539889e97c7977528a53b/pip-24.0-py3-none-any.whl"
    $First = (Get-FluentFlowSourceOrder @("https://files.pythonhosted.org/$Wheel", "https://pypi.tuna.tsinghua.edu.cn/$Wheel"))[0]
    $script:PipIndex = if ($First -like "*tuna*") { $TunaIndex } else { $PypiIndex }
    if ($script:PipIndex -eq $TunaIndex) { Write-Host "PyPI 连不上或太慢，Python 依赖从国内镜像（清华源）下载。" }
}

function Invoke-Pip([string[]]$Arguments) {
    if ($UserPipIndex) {
        Invoke-Checked $Python (@("-m", "pip") + $Arguments)
        return
    }
    if (-not $script:PipIndex) { Select-PipIndex }
    & $Python -m pip @Arguments --index-url $script:PipIndex
    if ($LASTEXITCODE -eq 0) { return }
    $Other = if ($script:PipIndex -eq $TunaIndex) { $PypiIndex } else { $TunaIndex }
    Write-Host "下载失败，换 $(Get-FluentFlowHost $Other) 重试…"
    Invoke-Checked $Python (@("-m", "pip") + $Arguments + @("--index-url", $Other))
}

function Test-NvidiaAdapter {
    try {
        return @(
            Get-CimInstance Win32_VideoController -ErrorAction Stop |
                Where-Object { $_.Name -match "NVIDIA" }
        ).Count -gt 0
    } catch {
        return $false
    }
}

# Checked before anything downloads. FFmpeg is required, and the pip install
# below takes a long time: finding out at the readiness check means the user
# waited through the whole install to be told one thing was missing.
#
# A missing FFmpeg is downloaded from npmmirror (Alibaba's mirror of the
# ffmpeg-static binaries, which are gyan.dev's essentials build, the same
# builder as winget's Gyan.FFmpeg) into the data folder's bin, the folder the
# service puts first on PATH (runtime_paths.put_bundled_tools_on_path). winget
# fetches Gyan.FFmpeg from GitHub, which can crawl for many minutes in China
# with nothing on screen (a user's friend, 2026-11-21); it stays as the fallback
# when the mirror cannot be reached.
# The data folder as the service reads it (environment, then .env).
$FfmpegDataDir = Get-FluentFlowDataDir $Repo
$FfmpegBin = Join-Path $FfmpegDataDir "bin"
$FfmpegMirror = "https://registry.npmmirror.com/-/binary/ffmpeg-static/b6.1.1"
# SHA-256 of the unpacked programs, checked before either is run or put on PATH.
# Taken 2026-11-21; the mirror's ffmpeg .gz was byte-identical to the one on the
# ffmpeg-static GitHub release b6.1.1.
$FfmpegSha256 = @{
    "ffmpeg"  = "04e1307997530f9cf2fe35cba2ca7e8875ca91da02f89d6c7243df819c94ad00"
    "ffprobe" = "3a7e2dc003dc2cd1472827e4c7c4f056ae1ae0ae7c5bbc580c99b49827351ba4"
}
if (Test-Path (Join-Path $FfmpegBin "ffmpeg.exe")) { $env:Path = "$FfmpegBin;$env:Path" }

function Test-Ffmpeg {
    return (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue) -and
        (Get-Command ffprobe.exe -ErrorAction SilentlyContinue)
}

function Install-FfmpegFromMirror {
    # Both programs are unpacked and checked in a staging folder and moved into
    # bin only once both pass, so a broken download never sits where the
    # service looks first.
    $Staging = Join-Path ([System.IO.Path]::GetTempPath()) ("fluentflow-ffmpeg-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $Staging | Out-Null
    $Saved = $ProgressPreference
    # Windows PowerShell 5.1 redraws its progress bar for every block it reads,
    # which makes a 30 MB download take minutes; the size is printed instead.
    $ProgressPreference = "SilentlyContinue"
    try {
        foreach ($Tool in @("ffmpeg", "ffprobe")) {
            Write-Host "  下载 $Tool（约 30 MB）…"
            $Gz = Join-Path $Staging "$Tool.exe.gz"
            Invoke-WebRequest -UseBasicParsing -TimeoutSec 300 -Uri "$FfmpegMirror/$Tool-win32-x64.gz" -OutFile $Gz
            $Exe = Join-Path $Staging "$Tool.exe"
            $In = [System.IO.File]::OpenRead($Gz)
            try {
                $Unzip = New-Object System.IO.Compression.GZipStream($In, [System.IO.Compression.CompressionMode]::Decompress)
                $Out = [System.IO.File]::Create($Exe)
                try { $Unzip.CopyTo($Out) } finally { $Out.Close(); $Unzip.Close() }
            } finally { $In.Close() }
            Remove-Item $Gz
            $Hash = (Get-FileHash -Algorithm SHA256 -Path $Exe).Hash.ToLower()
            if ($Hash -ne $FfmpegSha256[$Tool]) { throw "下载的 $Tool 校验不对（$Hash）。" }
        }
        New-Item -ItemType Directory -Force -Path $FfmpegBin | Out-Null
        foreach ($Tool in @("ffmpeg", "ffprobe")) {
            Move-Item -Force (Join-Path $Staging "$Tool.exe") (Join-Path $FfmpegBin "$Tool.exe")
        }
    } finally {
        $ProgressPreference = $Saved
        Remove-Item -Recurse -Force $Staging -ErrorAction SilentlyContinue
    }
    $env:Path = "$FfmpegBin;$env:Path"
}

if (-not (Test-Ffmpeg)) {
    Write-Host "未找到 FFmpeg（转码和抽帧都要用它），正在从国内镜像下载到 $FfmpegBin…"
    try {
        Install-FfmpegFromMirror
    } catch {
        Write-Host "国内镜像下载失败（$($_.Exception.Message)），改用 winget 安装…" -ForegroundColor Yellow
        $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
        if (-not $Winget) {
            throw "FFmpeg 下载失败，这台电脑也没有 winget。请检查网络后重新运行本脚本，或自己装好 FFmpeg（确认 ffmpeg.exe 和 ffprobe.exe 在 PATH 里）再运行。"
        }
        # The agreement switches are what make this unattended: without them
        # winget stops on a prompt the user did not ask to be shown.
        Invoke-Checked $Winget.Source @(
            "install", "--id", "Gyan.FFmpeg", "-e", "--source", "winget",
            "--accept-source-agreements", "--accept-package-agreements"
        )
        # Re-read PATH now and prove ffmpeg is reachable before the long pip
        # install. Without this the script ran for twenty minutes and then
        # failed at the readiness check over the thing it had itself installed.
        Update-SessionPath
    }
    if (-not (Test-Ffmpeg)) {
        throw "FFmpeg 装好了，但 PATH 里还找不到 ffmpeg.exe 或 ffprobe.exe。请关掉这个 PowerShell 窗口，开一个新窗口，重新运行本脚本。"
    }
    Write-Host "已安装 FFmpeg：$((Get-Command ffmpeg.exe).Source)"
}

# The dependencies have prebuilt packages for Python 3.10 to 3.14 only. A newer
# one (3.15, the default on a machine that just installed the latest Python)
# fails deep inside pip, "onnxruntime: no matching distribution", with nothing
# saying the version is the cause. Raise the upper bound here once
# onnxruntime, ctranslate2 and faster-whisper publish packages for the next
# Python; launchers/macos/setup-local.sh has the same bound.
$PythonVersions = @("3.12", "3.13", "3.11", "3.14", "3.10")
$PythonSupported = "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 14) else 1)"

function Find-SupportedPython {
    $Launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if (-not $Launcher) { return $null }
    foreach ($Version in $PythonVersions) {
        # try/catch because Windows PowerShell 5.1 turns a native command's
        # stderr into a terminating error under ErrorActionPreference=Stop, and
        # py.exe asked for a version it does not have writes exactly that.
        try {
            & $Launcher.Source "-$Version" -c $PythonSupported 2>$null
            if ($LASTEXITCODE -eq 0) { return @($Launcher.Source, "-$Version") }
        } catch {}
    }
    return $null
}

# A venv left by an earlier attempt with an unsupported Python would fail the
# same way on every rerun; it holds nothing but packages, so it is rebuilt.
if (Test-Path $Python) {
    try {
        & $Python -c $PythonSupported 2>$null
        $VenvOk = ($LASTEXITCODE -eq 0)
    } catch {
        $VenvOk = $false
    }
    if (-not $VenvOk) {
        Write-Host "已有的虚拟环境是 $(& $Python -V 2>&1)，依赖装不上，删掉重建…"
        Remove-Item -Recurse -Force $Venv
    }
}

# Python's own installer, for the current user only (no administrator rights),
# from python.org or its byte-identical copy on npmmirror, whichever answers
# faster. winget fetched the same installer from python.org only, which can
# crawl in China; it stays as the fallback when neither host can be reached.
# The per-user install goes to its usual place on drive C, like any Python.
$PythonInstallerVersion = "3.12.10"
$PythonInstallerSha256 = "67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb"
$UserPythonDir = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312"
$UserPython = Join-Path $UserPythonDir "python.exe"

function Install-PythonFromInstaller {
    $Name = "python-$PythonInstallerVersion-amd64.exe"
    $Work = Join-Path ([System.IO.Path]::GetTempPath()) ("fluentflow-python-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $Work | Out-Null
    try {
        $Installer = Join-Path $Work $Name
        Save-FluentFlowVerifiedDownload @(
            "https://www.python.org/ftp/python/$PythonInstallerVersion/$Name",
            "https://registry.npmmirror.com/-/binary/python/$PythonInstallerVersion/$Name"
        ) $Installer $PythonInstallerSha256
        Write-Host "  安装 Python $PythonInstallerVersion（只给当前用户，约一分钟）…"
        $Process = Start-Process -FilePath $Installer -Wait -PassThru -ArgumentList @(
            "/quiet", "InstallAllUsers=0", "TargetDir=`"$UserPythonDir`"", "PrependPath=0",
            "Include_launcher=0", "Include_test=0", "Include_doc=0", "Shortcuts=0"
        )
        if ($Process.ExitCode -ne 0) { throw "Python 安装程序退出码 $($Process.ExitCode)" }
    } finally {
        Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
    }
}

function Test-UserPython {
    if (-not (Test-Path $UserPython)) { return $false }
    try {
        & $UserPython -c $PythonSupported 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

if (-not (Test-Path $Python)) {
    $BasePython = Find-SupportedPython
    if (-not $BasePython -and (Test-UserPython)) { $BasePython = @($UserPython) }
    if (-not $BasePython) {
        Write-Host "没找到 3.10 到 3.14 之间的 Python，正在安装 Python $PythonInstallerVersion…"
        try {
            Install-PythonFromInstaller
        } catch {
            Write-Host "Python 安装包下载或安装失败（$($_.Exception.Message)），改用 winget…" -ForegroundColor Yellow
            # User scope: the machine-wide installer asks for administrator rights.
            Install-WithWinget "Python.Python.3.12" "3.10 到 3.14 之间的 Python" @("--scope", "user")
        }
        if (Test-UserPython) { $BasePython = @($UserPython) } else { $BasePython = Find-SupportedPython }
        if (-not $BasePython) {
            throw "装好了 Python 3.12，但找不到它。请开一个新的 PowerShell 窗口，重新运行本脚本。"
        }
    }
    Write-Host "用 $($BasePython -join ' ') 创建项目虚拟环境…"
    Invoke-Checked $BasePython[0] (@($BasePython | Select-Object -Skip 1) + @("-m", "venv", $Venv))
}

Write-Host "安装 Python 依赖（体积较大，首次约需十几分钟）…"
Invoke-Pip @("install", "-r", (Join-Path $Repo "requirements-local.txt"))

# yt-dlp is kept at its newest release: Douyin, Bilibili and YouTube change their
# pages often and an old copy fails a whole platform. Not fatal: it only affects
# video links, and every launch retries through scripts\update_yt_dlp.py.
Write-Host "安装/更新 yt-dlp（视频链接下载器）…"
try {
    Invoke-Pip @("install", "-U", "--disable-pip-version-check", "yt-dlp")
    $YtDlpOk = $true
} catch {
    $YtDlpOk = $false
}
if (-not $YtDlpOk) {
    Write-Host "! yt-dlp 没有装上或没能更新到最新版（多半是网络问题）。上传本地文件不受影响，视频链接功能要等它装好。之后每次启动会自动再试，也可以手动运行：.\.venv\Scripts\python.exe scripts\update_yt_dlp.py --force" -ForegroundColor Yellow
}

if (Test-NvidiaAdapter) {
    Write-Host "检测到 NVIDIA 显卡，正在安装 FluentFlow 的 GPU 运行库…"
    Invoke-Pip @("install", "-r", (Join-Path $Repo "requirements-windows-gpu.txt"))
} else {
    Write-Host "没有检测到 NVIDIA 显卡，FluentFlow 将用 CPU 转录。"
}

# Only building the interface needs Node.js. Without one on the machine, a
# portable copy goes into the data folder for this step alone: the winget
# package is a machine-wide installer that asks for administrator rights.
$NodeVersion = "v24.20.0"
# From the official SHASUMS256.txt of this release; npmmirror's copy is the same file.
$NodeZipSha256 = "6cac9ffbca8f6a47091e4b5c772e0606049c3871cb67d900c0cedde630e545ba"
$Npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $Npm) {
    $ToolsDir = Join-Path (Get-FluentFlowDataDir $Repo) "tools"
    New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null
    $NodeName = "node-$NodeVersion-win-x64"
    $NodeHome = Join-Path $ToolsDir $NodeName
    if (-not (Test-Path (Join-Path $NodeHome "npm.cmd"))) {
        Write-Host "未找到 Node.js（构建界面要用它），正在下载免安装版…"
        $Zip = Join-Path $ToolsDir "$NodeName.zip"
        Save-FluentFlowVerifiedDownload @(
            "https://nodejs.org/dist/$NodeVersion/$NodeName.zip",
            "https://registry.npmmirror.com/-/binary/node/$NodeVersion/$NodeName.zip"
        ) $Zip $NodeZipSha256
        Expand-Archive -Force -Path $Zip -DestinationPath $ToolsDir
        Remove-Item $Zip
    }
    $env:Path = "$NodeHome;$env:Path"
    $Npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $Npm) {
        throw "Node.js 已下载，但运行不了 ${NodeHome} 里的 npm.cmd。"
    }
}

# The npm registry: npmjs, or npmmirror when npmjs is unreachable or slow; a
# failed run is retried once from the other. A registry the user configured
# (npm config or npm_config_registry) is used as it is.
$NpmjsRegistry = "https://registry.npmjs.org"
$NpmmirrorRegistry = "https://registry.npmmirror.com"
$UserNpmRegistry = $null
try {
    $Configured = (& $Npm.Source config get registry 2>$null | Select-Object -First 1)
    if ($Configured -and ($Configured.TrimEnd('/') -ne $NpmjsRegistry)) { $UserNpmRegistry = $Configured }
} catch {}
$NpmRegistry = $NpmjsRegistry
if (-not $UserNpmRegistry) {
    # A 5 MB package, at the same path on both registries.
    $Tarball = "typescript/-/typescript-5.4.5.tgz"
    $First = (Get-FluentFlowSourceOrder @("$NpmjsRegistry/$Tarball", "$NpmmirrorRegistry/$Tarball"))[0]
    if ($First -like "$NpmmirrorRegistry*") {
        $NpmRegistry = $NpmmirrorRegistry
        Write-Host "npmjs 连不上或太慢，前端依赖从国内镜像（npmmirror）下载。"
    }
}

function Invoke-Npm([string[]]$Arguments) {
    if ($UserNpmRegistry) {
        Invoke-Checked $Npm.Source $Arguments
        return
    }
    & $Npm.Source @Arguments "--registry=$NpmRegistry"
    if ($LASTEXITCODE -eq 0) { return }
    $Other = if ($NpmRegistry -eq $NpmmirrorRegistry) { $NpmjsRegistry } else { $NpmmirrorRegistry }
    Write-Host "下载失败，换 $(Get-FluentFlowHost $Other) 重试…"
    Invoke-Checked $Npm.Source ($Arguments + @("--registry=$Other"))
}

# Picked by what package.json declares rather than hard-coded, so a renamed
# build script fails loudly here instead of silently building nothing.
$BuildScript = "build:frontend"
$PackageJson = Join-Path $Repo "package.json"
if (Test-Path $PackageJson) {
    $Scripts = (Get-Content $PackageJson -Raw | ConvertFrom-Json).scripts
    if ($Scripts.PSObject.Properties.Name -contains "build:frontend:local") {
        $BuildScript = "build:frontend:local"
    }
}

Push-Location $Repo
try {
    Write-Host "安装前端依赖…"
    Invoke-Npm @("ci")
    Write-Host "构建前端：npm run ${BuildScript}"
    Invoke-Checked $Npm.Source @("run", $BuildScript)
} finally {
    Pop-Location
}

# Two small speaker-diarization models (about 47 MB), so the speaker separation
# switch works right after install. From GitHub or a mirror in China (the script
# picks); a failure costs only that switch, so it is reported and the install
# continues.
Write-Host "准备讲话人区分模型…"
& $Python (Join-Path $Repo "scripts\fetch_diarization_models.py")
if ($LASTEXITCODE -ne 0) {
    Write-Host "提示：讲话人区分模型没有下载成功，其余功能不受影响。网络恢复后运行这一句补上：.\.venv\Scripts\python.exe scripts\fetch_diarization_models.py"
}

# The transcription model (1.5 to 3 GB) downloads in the background, so the
# install finishes and the app opens without waiting for it (王愉超, 2026-11-21:
# on a real connection in China it was the longest step). A recording
# submitted before it is done shows 「正在下载转录模型」 and carries on once it
# is (local_stt._download_model_if_missing); the download picks the faster of
# huggingface.co and hf-mirror.com itself. Its output goes to the data folder.
if ($SkipModel) {
    Write-Host "按要求跳过转录模型，第一次转录时会自动下载。"
} else {
    $ModelLog = Join-Path (Get-FluentFlowDataDir $Repo) "logs\model-download.log"
    & $Python (Join-Path $Repo "scripts\stt_model.py") "fetch" "--background" $ModelLog
    if ($LASTEXITCODE -ne 0) { Write-Host "转录模型没能开始后台下载，第一次转录时会自动下载。" }
    Write-Host "转录模型（1.5 到 3 GB）不用等它：装完就能打开页面，下完之前提交的录像会等它下完再转。"
}

# The user's agent reaches this app through the MCP tools, and that interface
# opens only with a token. One is generated into .env, which the backend and the
# MCP server both read, so nobody has to copy it anywhere.
$EnvFile = Join-Path $Repo ".env"
$HasToken = (Test-Path $EnvFile) -and (Select-String -Path $EnvFile -Pattern '^\s*FLUENTFLOW_ACCESS_TOKEN=\S+' -Quiet)
if (-not $HasToken) {
    $Bytes = New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($Bytes)
    $Token = -join ($Bytes | ForEach-Object { $_.ToString("x2") })
    Add-Content -Path $EnvFile -Value "`nFLUENTFLOW_ACCESS_TOKEN=$Token" -Encoding UTF8
    Write-Host "已生成访问令牌，写入 ${EnvFile}。"
}

Write-Host "启动前检查…"
Invoke-Checked $Python @((Join-Path $Repo "scripts\check_local_readiness.py"))
Invoke-Checked (Join-Path $ScriptDir "install-local-to-desktop.ps1") @()
Write-Host "安装完成。双击桌面上的「FluentFlow Local.cmd」即可启动。"
