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
# the interface) is a portable copy in the data folder, and FFmpeg comes from
# winget's portable package.
#
# A failed download is retried once from a mirror in China: pip from Tsinghua,
# npm from npmmirror, the transcription model from hf-mirror. A PIP_INDEX_URL or
# npm registry you set yourself is left alone.
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

# Retried once from a mirror in China when the default index fails.
function Invoke-Pip([string[]]$Arguments) {
    & $Python -m pip @Arguments
    if ($LASTEXITCODE -eq 0) { return }
    if ($env:PIP_INDEX_URL) { throw "pip 失败：$($Arguments -join ' ')" }
    Write-Host "下载失败，换国内镜像（清华源）重试…"
    $env:PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"
    try {
        Invoke-Checked $Python (@("-m", "pip") + $Arguments)
    } finally {
        Remove-Item Env:PIP_INDEX_URL -ErrorAction SilentlyContinue
    }
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
# Same data folder rule as the Node.js step below and the uninstaller.
$FfmpegDataDir = if ($env:FLUENTFLOW_DATA_DIR) { $env:FLUENTFLOW_DATA_DIR } else { Join-Path $env:APPDATA "FluentFlow" }
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

if (-not (Test-Path $Python)) {
    $BasePython = Find-SupportedPython
    if (-not $BasePython) {
        # User scope: the machine-wide installer asks for administrator rights.
        Install-WithWinget "Python.Python.3.12" "3.10 到 3.14 之间的 Python" @("--scope", "user")
        if (-not (Get-Command py.exe -ErrorAction SilentlyContinue)) {
            throw "Python 已经装好，但 PATH 里还找不到 py.exe。请开一个新的 PowerShell 窗口，重新运行本脚本。"
        }
        $BasePython = Find-SupportedPython
        if (-not $BasePython) {
            throw "装好了 Python 3.12，但 py.exe 找不到它。请开一个新的 PowerShell 窗口，重新运行本脚本。"
        }
    }
    Write-Host "用 Python $($BasePython[1].TrimStart('-')) 创建项目虚拟环境…"
    Invoke-Checked $BasePython[0] @($BasePython[1], "-m", "venv", $Venv)
}

Write-Host "安装 Python 依赖（体积较大，首次约需十几分钟）…"
Invoke-Pip @("install", "-r", (Join-Path $Repo "requirements-local.txt"))

# yt-dlp is kept at its newest release: Douyin, Bilibili and YouTube change their
# pages often and an old copy fails a whole platform. Not fatal: it only affects
# video links, and every launch retries through scripts\update_yt_dlp.py.
Write-Host "安装/更新 yt-dlp（视频链接下载器）…"
try {
    & $Python -m pip install -U --disable-pip-version-check yt-dlp
    $YtDlpOk = ($LASTEXITCODE -eq 0)
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
$Npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $Npm) {
    $DataDir = if ($env:FLUENTFLOW_DATA_DIR) { $env:FLUENTFLOW_DATA_DIR } else { Join-Path $env:APPDATA "FluentFlow" }
    $ToolsDir = Join-Path $DataDir "tools"
    New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null
    $NodeName = "node-$NodeVersion-win-x64"
    $NodeHome = Join-Path $ToolsDir $NodeName
    if (-not (Test-Path (Join-Path $NodeHome "npm.cmd"))) {
        Write-Host "未找到 Node.js（构建界面要用它），正在下载免安装版…"
        $Zip = Join-Path $ToolsDir "$NodeName.zip"
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "https://nodejs.org/dist/$NodeVersion/$NodeName.zip" -OutFile $Zip
        } catch {
            Invoke-WebRequest -UseBasicParsing -Uri "https://npmmirror.com/mirrors/node/$NodeVersion/$NodeName.zip" -OutFile $Zip
        }
        Expand-Archive -Force -Path $Zip -DestinationPath $ToolsDir
        Remove-Item $Zip
    }
    $env:Path = "$NodeHome;$env:Path"
    $Npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $Npm) {
        throw "Node.js 已下载，但运行不了 ${NodeHome} 里的 npm.cmd。"
    }
}

# Retried once from npmmirror when the default registry fails.
function Invoke-Npm([string[]]$Arguments) {
    & $Npm.Source @Arguments
    if ($LASTEXITCODE -eq 0) { return }
    Write-Host "下载失败，换国内镜像（npmmirror）重试…"
    Invoke-Checked $Npm.Source ($Arguments + @("--registry=https://registry.npmmirror.com"))
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
# switch works right after install. They exist only on GitHub, with no mirror; a
# failure costs only that switch, so it is reported and the install continues.
Write-Host "准备讲话人区分模型…"
& $Python (Join-Path $Repo "scripts\fetch_diarization_models.py")
if ($LASTEXITCODE -ne 0) {
    Write-Host "提示：讲话人区分模型没有下载成功，其余功能不受影响。网络能打开 github.com 后运行这一句补上：.\.venv\Scripts\python.exe scripts\fetch_diarization_models.py"
}

# Downloaded here rather than inside the user's first transcription, where the
# wait has no progress and no explanation. A failure is not fatal: the model can
# be fetched later, and the first run falls back to downloading it itself.
if ($SkipModel) {
    Write-Host "按要求跳过转录模型，第一次转录时会自动下载。"
} else {
    Write-Host "准备转录模型…"
    & $Python (Join-Path $Repo "scripts\stt_model.py") "fetch"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "下载失败，换镜像源（hf-mirror.com）重试…"
        & $Python (Join-Path $Repo "scripts\stt_model.py") "fetch" "--mirror"
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "转录模型没有下载成功。第一次转录时会自动下载（连不上 huggingface.co 时换 hf-mirror.com 镜像），也可以现在运行：.\.venv\Scripts\python.exe scripts\stt_model.py fetch --mirror"
    }
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
