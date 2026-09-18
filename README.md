# FluentFlow Local

本地运行的视频/音频转录与笔记工具。转录在本机完成；笔记生成和飞书导出只会连接你自行配置的服务。

这是本地版的独立开发仓库。它不再由其他仓库导出或覆盖；本地版的功能、构建、测试、启动器和文档均在此维护。
与 FluentFlow Hosted 的完整边界和跨仓库移植规则见 `docs/edition_boundaries.md`。

## 安装

先把代码拿到本地：

```bash
git clone https://github.com/Userneima/fluentflow-local.git
cd fluentflow-local
```

### macOS

```bash
bash launchers/macos/setup-local.sh
```

它会建虚拟环境、装 Python 和前端依赖、构建前端、下载转录模型，最后在桌面生成
「FluentFlow Local.app」。装完双击桌面图标即可使用，不需要再开终端。

整个过程不需要你回答任何问题。FFmpeg 和 Node.js 缺失时会用 Homebrew 直接装上；
Homebrew 本身需要你自己先装（https://brew.sh），那一步要输密码。`--skip-desktop`
不生成桌面图标，`--skip-model` 不下转录模型（留到第一次转录时再下）。

### Windows

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\launchers\windows\setup-local.ps1
```

同样是一次装完，同样不问问题。检测到 NVIDIA 显卡时，它会把 CUDA 12 与 cuDNN 9
运行库装进这个虚拟环境；不需要装机器级 CUDA Toolkit，但显卡驱动要在。FFmpeg 缺失
时会用 winget 直接装上。`-SkipModel` 不下转录模型。

已经装好、只想补 GPU 运行库：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-windows-gpu.txt
```

有显卡时转录用 large-v3，没有时自动降到 medium。运行库缺失会退回 CPU，并在启动
检查里说明缺什么。

### 手动安装

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-local.txt
npm install
npm run build:frontend
.venv/bin/python scripts/stt_model.py fetch
.venv/bin/python -m uvicorn backend.local_main:app --host 127.0.0.1 --port 8000
```

需要 Python 3.10 以上和 FFmpeg。AI 与飞书凭据在应用的设置页里填写。运行数据默认
保存在系统应用数据目录（macOS 是 `~/Library/Application Support/FluentFlow`），
不在仓库中。

## 笔记要接你自己的模型账号

转录在本机跑，不需要任何账号，也不花钱。笔记不一样，它要调模型：

- 填了 Anthropic API Key，笔记由 Claude 看着画面写，能引用幻灯片和白板上的内容。
- 没填，笔记退回 `AI_PROVIDER` 指定的服务（默认 DeepSeek）写纯文字版，只看转录稿。
  这一步同样需要那家的 Key。
- 一个 Key 都没有，你会拿到转录稿、字幕和剪好的音视频，但没有笔记。

Key 在应用的设置页里填，不必编辑文件。可配置的项在 `distribution/local.env.example`
里都有说明。

## 需要多少磁盘空间

在一台 Apple Silicon Mac 上实测：

| | 占用 |
| --- | --- |
| Python 依赖（`.venv`） | 1.7 GB |
| 前端依赖与构建产物 | 0.2 GB |
| 转录模型 large-v3 | 3.1 GB |

产品自己占约 5 GB。加上系统层的 Xcode 命令行工具、Homebrew、FFmpeg 和 Node.js
（这台机器上量到约 2.8 GB），一台全新的 Mac 从零到跑完第一个任务约 8 GB。之后每个
任务还会在应用数据目录里留下媒体、抽帧和中间产物，那部分随使用增长，没有上限。

只用 CPU 转录的机器会自动改用 medium（约 1.5 GB），不会下载跑不动的那个模型。

模型从 Hugging Face 下载。那里连不上时安装脚本会自动改用镜像 `hf-mirror.com`，也可以
用 `HF_ENDPOINT` 指定自己的源，或者给 `scripts/stt_model.py fetch` 加 `--mirror`。

## 卸载

```bash
bash launchers/macos/uninstall-local.sh            # 只卸程序
bash launchers/macos/uninstall-local.sh --models   # 连转录模型一起
bash launchers/macos/uninstall-local.sh --data     # 连任务数据一起
bash launchers/macos/uninstall-local.sh --all --dry-run   # 先看看要删什么
```

Windows 用 `launchers\windows\uninstall-local.ps1`，开关是 `-Models`、`-Data`、
`-All`、`-DryRun`。

默认只删虚拟环境、前端依赖、桌面启动器和日志，这些重装就回来。转录模型和任务
数据各自需要显式开口：模型住在 Hugging Face 的公共缓存里，脚本只删本产品下载过
的那几个目录；任务数据删掉无法恢复，所以会再确认一次。

FFmpeg、Node.js、Homebrew 和 Python 不会被动，它们是系统工具。代码目录留给你自己
删——卸载脚本就在里面。

## Development

```bash
npm run lint:frontend
npm run build:frontend
npm run test:frontend
.venv/bin/python -m pytest tests/ -q
```

`npm run build:frontend` 生成本地版由 `backend.local_main` 提供的 `frontend/dist-local`。
不要提交该构建目录、`.env`、媒体、任务数据库或导出内容。

启动前的环境检查可以单独跑，它会说明转录会走哪条路、模型在不在本机：

```bash
.venv/bin/python scripts/check_local_readiness.py
```

## License

FluentFlow Local 使用 GNU Affero General Public License v3.0 或更高版本（AGPL-3.0-or-later）。如果你修改本项目并将其作为网络服务提供，AGPL 可能要求向该服务用户提供相应源代码；这不是法律意见，请阅读仓库中的 `LICENSE` 并自行取得法律建议。
