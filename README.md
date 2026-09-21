# FluentFlow Local

本地运行的视频/音频转录与笔记工具。贴一条抖音、Bilibili、YouTube 链接，或者选一个本机
文件，它把音视频转成文字，再写成一份可以编辑和导出的笔记。转录在本机完成，不上传；笔记
生成和飞书导出只会连接你自行配置的服务。

![主界面](docs/images/home.png)

转录用的是本机的 Whisper 模型，Apple 芯片走 GPU 加速。讲话人区分、人声增强、转录速度都
在设置里，处理过程中的判断会写进每个任务的记录，而不是藏在日志里。

![设置](docs/images/settings.png)

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

**这需要 API Key，不是 Claude Pro/Max 订阅**——订阅无法作为 API 凭证给第三方程序使用。
想用订阅写笔记，走下面的 MCP 路线。

纯文字笔记也可以交给 Claude：设置页的「服务商」多了 `Claude` 一项，默认模型
`claude-opus-5`，也可选 `claude-sonnet-5` / `claude-haiku-4-5`；用 `ANTHROPIC_MODEL` 和
`ANTHROPIC_MAX_TOKENS`（默认 64000）覆盖。选它之后建议把「笔记生成模式」设成**直接上下文**
（`FLUENTFLOW_NOTE_MODE=direct`）：

| | DeepSeek + 自动选择 | Claude + 直接上下文 |
|---|---|---|
| 13 万字转录 | 走完整覆盖模式，20+ 次模型调用 | **1 次调用** |
| 连贯性 | 分章写完再拼接 | 一次读完全文再写 |

那套多步流水线（切段抽证据 → 大纲 → 逐章 → 统一文风 → 覆盖度检查 → 按需返修）是为上下文
装不下长转录的模型准备的补偿机制，默认阈值 2 万字。Claude 的上下文足够长，可以直接一次成文；
DeepSeek 用户仍然需要多步模式，所以它原样保留。

另外自动配图不再必须配置阿里云百炼：笔记服务商本身能看图时（Claude、Qwen）就用它选帧，
只有 DeepSeek 这类纯文本服务商才回退到 Qwen。

## 用 Claude Desktop 等 MCP 客户端写笔记

除了本机 AI 凭证生成笔记，也可以让 MCP 客户端读走转录、用它自己的模型写笔记再存回来——
这样走的是那个客户端的订阅，不消耗你配置的 AI 凭证。

先设访问令牌启用 Agent API（不设则 `/agent/v1` 整体关闭）：

```powershell
$env:FLUENTFLOW_ACCESS_TOKEN = "自己起一个足够长的随机串"
```

再把 `scripts/fluentflow_mcp_server.py` 注册为 MCP server（stdio），需要时用
`FLUENTFLOW_API_BASE`、`FLUENTFLOW_CLIENT_ID` 指定后端地址与客户端标识。

单条的用法是：`get_task_package` 读全文转录 → 客户端写笔记 → `save_note` 存回，笔记随即
出现在编辑器里，导出飞书照常。

攒一批处理时不用逐个报 task_id，一句话就够：

> 把 FluentFlow 里所有还没有笔记的任务列出来，逐个读转录写笔记，写完存回去

客户端会用 `list_tasks(note="missing")` 找出待办、逐个 `get_task_package` + `save_note`。
每行都带 `note.source`，所以第二次跑批能跳过自己写过的（`agent`）、也不会去覆盖用户手改过
的（`editor`）。改写已有笔记时把读到的原文作为 `expected_summary_markdown` 一起传回：期间
用户在编辑器改过笔记的话会返回 409，而不是静默覆盖。

自检：

```powershell
.\.venv\Scripts\python.exe scripts\check_mcp_server.py --access-token $env:FLUENTFLOW_ACCESS_TOKEN --backend-e2e
```

注意转录文本会发送给该 MCP 客户端背后的服务商；原始音视频仍然只留在本机。

## 数据存放位置

Windows 上转录会完整保留源文件，所以运行数据默认不放系统盘：选择可用空间最大的固定非系统盘
（如 `D:\FluentFlow`），没有合适磁盘时回退到 `%APPDATA%\FluentFlow`。想指定目录就设
`FLUENTFLOW_DATA_DIR`。已经在 `%APPDATA%` 下积累了记录的旧安装会继续读原目录，不会自动切换；
需要搬到数据盘时先关闭应用，再执行 `python scripts\migrate_data_dir.py`。

位置只由一处决定，优先级从高到低：

| 依据 | 何时生效 |
|---|---|
| `FLUENTFLOW_DATA_DIR` | 显式指定某一次运行；不会被写入记录 |
| `%LOCALAPPDATA%\FluentFlow\data-root.txt` | 迁移脚本写入，或首次运行时自动记录 |
| 按剩余空间选盘 | 只在还没有记录时用一次，随即记录下来 |

启发式只在首次运行跑一次就被固化，之后启动都是读文件——否则搬到"不是最大那块盘"的工作区会在
下次启动时被悄悄绕开，界面看起来就像记录全丢了。启动时的 `data-dir` 检查会打印当前位置**和
判定依据**，以及被 `FLUENTFLOW_*_PATH` / `FLUENTFLOW_*_DIR` 单独指到别处的项。

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

Node 版本以 `.nvmrc` 为准（CI 读同一个文件）。改动依赖时请用这个版本重新生成
`package-lock.json`：npm 10 和 npm 11 对可选依赖的 peer 条目该不该写进 lock 有分歧，而
`npm ci` 遇到差异是直接失败而不是自行修补。

启动前的环境检查可以单独跑，它会说明转录会走哪条路、模型在不在本机：

```bash
.venv/bin/python scripts/check_local_readiness.py
```

## License

FluentFlow Local 使用 GNU Affero General Public License v3.0 或更高版本（AGPL-3.0-or-later）。如果你修改本项目并将其作为网络服务提供，AGPL 可能要求向该服务用户提供相应源代码；这不是法律意见，请阅读仓库中的 `LICENSE` 并自行取得法律建议。
