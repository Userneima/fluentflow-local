# FluentFlow Local

本地运行的视频/音频转录与笔记工具。转录在本机完成；笔记生成和飞书导出只会连接你自行配置的服务。

这是本地版的独立开发仓库。它不再由其他仓库导出或覆盖；本地版的功能、构建、测试、启动器和文档均在此维护。
与 FluentFlow Hosted 的完整边界和跨仓库移植规则见 `docs/edition_boundaries.md`。

## 安装

需要 Python 3.10 或更高版本、Node.js 和 FFmpeg。AI 与飞书凭据装完后在应用的设置页里填写。

### macOS

在仓库目录里执行一次：

```bash
bash launchers/macos/setup-local.sh
```

它会创建虚拟环境、安装依赖、构建前端、跑一遍启动前检查，并在桌面生成「FluentFlow Local.app」。之后双击桌面图标即可启动，不必再打开终端。缺 FFmpeg 时它会先问一句要不要用 Homebrew 安装，再继续后面耗时的步骤。

只想装好运行环境、不要桌面图标，加 `--skip-desktop`。

Apple Silicon 上会自动装 mlx-whisper 走加速转录，其他机器用 faster-whisper 在 CPU 上转。

### Windows（含 NVIDIA 显卡转录）

在 PowerShell 里执行一次：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\launchers\windows\setup-local.ps1
```

它会建 `.venv`、装依赖、构建前端，并且在检测到 NVIDIA 显卡时把 CUDA 12 与 cuDNN 9 运行库装进这个虚拟环境。不需要装机器级 CUDA Toolkit，但显卡驱动要在。

已经装好、只想补 GPU 运行库：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-windows-gpu.txt
```

有显卡时转录用 large-v3，没有时用 medium。运行库缺失会退回 CPU 并在启动检查里说明缺什么。

### 手动安装

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-local.txt
npm install
npm run build:frontend
.venv/bin/python -m uvicorn backend.local_main:app --host 127.0.0.1 --port 8000
```

运行数据默认保存在系统应用数据目录（例如 macOS 的 `~/Library/Application Support/FluentFlow`），不在仓库中。

## Development

```bash
npm run lint:frontend
npm run build:frontend
npm run test:frontend
.venv/bin/python -m pytest tests/ -q
```

`npm run build:frontend` 生成本地版由 `backend.local_main` 提供的 `frontend/dist-local`。不要提交该构建目录、`.env`、媒体、任务数据库或导出内容。

## License

FluentFlow Local 使用 GNU Affero General Public License v3.0 或更高版本（AGPL-3.0-or-later）。如果你修改本项目并将其作为网络服务提供，AGPL 可能要求向该服务用户提供相应源代码；这不是法律意见，请阅读仓库中的 `LICENSE` 并自行取得法律建议。
