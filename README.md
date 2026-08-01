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

需要 FFmpeg；AI 与飞书凭据可在应用设置中填写。

## License

FluentFlow Local 使用 GNU Affero General Public License v3.0 或更高版本（AGPL-3.0-or-later）。如果你修改本项目并将其作为网络服务提供，AGPL 可能要求向该服务用户提供相应源代码；这不是法律意见，请阅读仓库中的 `LICENSE` 并自行取得法律建议。
