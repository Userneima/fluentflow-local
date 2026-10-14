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
if (-not (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue) -or
    -not (Get-Command ffprobe.exe -ErrorAction SilentlyContinue)) {
    $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($Winget) {
        Write-Host "未找到 FFmpeg（转码和抽帧都要用它），正在安装…"
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
        if (-not (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue) -or
            -not (Get-Command ffprobe.exe -ErrorAction SilentlyContinue)) {
            throw "winget 已装好 FFmpeg，但 PATH 里还找不到 ffmpeg.exe 或 ffprobe.exe。请关掉这个 PowerShell 窗口，开一个新窗口，重新运行本脚本。还是找不到的话，把 FFmpeg 的 bin 目录手动加进 PATH。"
        }
        Write-Host "已安装 FFmpeg：$((Get-Command ffmpeg.exe).Source)"
    } else {
        throw "未找到 FFmpeg。请先安装（winget install Gyan.FFmpeg），确认 ffmpeg.exe 和 ffprobe.exe 在 PATH 里，再重新运行本脚本。"
    }
}

if (-not (Test-Path $Python)) {
    # The dependencies need 3.10 or newer; an older `py -3` builds a venv that
    # then fails deep inside pip with an error that never mentions the version.
    $PyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    $PythonOk = $false
    if ($PyLauncher) {
        # try/catch because Windows PowerShell 5.1 turns a native command's
        # stderr into a terminating error under ErrorActionPreference=Stop, and
        # py.exe with no Python installed writes exactly that.
        try {
            & $PyLauncher.Source -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
            $PythonOk = ($LASTEXITCODE -eq 0)
        } catch {
            $PythonOk = $false
        }
    }
    if (-not $PythonOk) {
        # User scope: the machine-wide installer asks for administrator rights.
        Install-WithWinget "Python.Python.3.12" "3.10 以上的 Python" @("--scope", "user")
        $PyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
        if (-not $PyLauncher) {
            throw "Python 已经装好，但 PATH 里还找不到 py.exe。请开一个新的 PowerShell 窗口，重新运行本脚本。"
        }
    }
    Write-Host "创建项目虚拟环境…"
    Invoke-Checked $PyLauncher.Source @("-3", "-m", "venv", $Venv)
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
