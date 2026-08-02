# FluentFlow Local

本地运行的视频/音频转录与笔记工具。转录在本机完成；笔记生成和飞书导出只会连接你自行配置的服务。

## Windows 安装（含 NVIDIA GPU 转录运行库）

在 PowerShell 中执行一次：

```powershell
cd 你的\fluentflow-local
powershell -NoProfile -ExecutionPolicy Bypass -File .\launchers\windows\setup-local.ps1
```

它会创建 `.venv`、安装 Python/Node 依赖、构建前端，并在检测到 NVIDIA 显卡时把 CUDA 12 + cuDNN 8 运行库安装到项目虚拟环境。不需要安装机器级 CUDA Toolkit。完成后双击桌面的 `FluentFlow Local.cmd`，打开 `http://127.0.0.1:8000/`。

如果你已经完成基础安装，仅需补装 GPU 运行库：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-windows-gpu.txt
```

## 其他平台

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-local.txt
npm install
npm run build:frontend
.venv/bin/python -m uvicorn backend.local_main:app --host 127.0.0.1 --port 8000
```

## 数据存放位置

转录会完整保留源文件，所以运行数据默认不放系统盘：Windows 上选择可用空间最大的固定非系统盘（如 `D:\FluentFlow`），没有合适磁盘时回退到 `%APPDATA%\FluentFlow`。想指定目录就设 `FLUENTFLOW_DATA_DIR`。

已经在 `%APPDATA%` 下积累了记录的旧安装会继续读原目录，不会自动切换；需要搬到数据盘时先关闭应用，再执行：

```powershell
.\.venv\Scripts\python.exe scripts\migrate_data_dir.py
```

迁移完成后位置会记录在 `%LOCALAPPDATA%\FluentFlow\data-root.txt`，之后每次启动都直接读这份记录，不再按剩余空间重新推算——否则搬到"不是最大那块盘"的工作区会在下次启动时被悄悄绕开，界面看起来就像记录全丢了。

笔记和转录默认永久保留。源媒体在 `FLUENTFLOW_SOURCE_RETENTION_DAYS`（默认 7 天）后清理，笔记不受影响。

## 开发检查

```bash
npm ci
npm run lint:frontend
npm run test:frontend
npm run build:frontend
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -p 'test_*.py'
python -m pylint backend --errors-only --disable=import-error,no-member
```

需要 FFmpeg；AI 与飞书凭据可在应用设置中填写。

## License

FluentFlow Local 使用 GNU Affero General Public License v3.0 或更高版本（AGPL-3.0-or-later）。如果你修改本项目并将其作为网络服务提供，AGPL 可能要求向该服务用户提供相应源代码；这不是法律意见，请阅读仓库中的 `LICENSE` 并自行取得法律建议。
