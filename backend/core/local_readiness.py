"""Local-edition startup readiness checks.

One source of truth for "can this machine actually run FluentFlow Local":
the composition root logs failures at startup, and the launcher / CLI script
(`scripts/check_local_readiness.py`) prints the same report before booting.

Checks stay import-light on purpose: probing availability must not pull heavy
models or start subprocess work beyond `shutil.which`.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from dataclasses import dataclass

from backend.core.frontend_paths import local_frontend_index_path
from backend.core.runtime_paths import app_data_root
from backend.core.windows_gpu_runtime import inspect_windows_gpu_runtime


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    ok: bool
    required: bool
    detail: str


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def _data_dir_writable() -> tuple[bool, str]:
    root = app_data_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".fluentflow-write-check"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, f"数据目录可写：{root}"
    except OSError as exc:
        return False, f"数据目录不可写（{root}）：{exc}。可用 FLUENTFLOW_DATA_DIR 指定其他目录。"


def _ffmpeg_install_hint() -> str:
    if sys.platform.startswith("win"):
        return "未找到 ffmpeg：请安装 FFmpeg（例如执行 winget install Gyan.FFmpeg），然后重新打开启动器。"
    return "未找到 ffmpeg：请先安装（macOS 执行 brew install ffmpeg）。"


def run_readiness_checks() -> list[ReadinessCheck]:
    checks: list[ReadinessCheck] = []

    ffmpeg = shutil.which("ffmpeg")
    checks.append(ReadinessCheck(
        name="ffmpeg",
        ok=bool(ffmpeg),
        required=True,
        detail=(f"已找到：{ffmpeg}" if ffmpeg
                else _ffmpeg_install_hint()),
    ))

    ffprobe = shutil.which("ffprobe")
    checks.append(ReadinessCheck(
        name="ffprobe",
        ok=bool(ffprobe),
        required=True,
        detail=(f"已找到：{ffprobe}" if ffprobe
                else f"未找到 ffprobe：随 ffmpeg 一起安装（{_ffmpeg_install_hint()}）"),
    ))

    fw = _module_available("faster_whisper")
    checks.append(ReadinessCheck(
        name="faster-whisper",
        ok=fw,
        required=True,
        detail=("本地转录引擎可用" if fw
                else "未安装 faster-whisper：请运行 pip install -r requirements-local.txt。"),
    ))

    if sys.platform.startswith("win"):
        gpu_runtime = inspect_windows_gpu_runtime()
        checks.append(ReadinessCheck(
            name="windows-gpu-runtime",
            ok=gpu_runtime.ready,
            required=False,
            detail=(
                "NVIDIA GPU transcription runtime is ready in this project environment."
                if gpu_runtime.ready
                else "NVIDIA GPU runtime DLLs are missing "
                f"({', '.join(gpu_runtime.missing_dlls)}). Automatic mode will use CPU; "
                f"run {gpu_runtime.install_hint} to enable GPU transcription."
            ),
        ))

    ytdlp = _module_available("yt_dlp")
    checks.append(ReadinessCheck(
        name="yt-dlp",
        ok=ytdlp,
        required=False,
        detail=("视频链接解析可用" if ytdlp
                else "未安装 yt-dlp：视频链接功能不可用；上传本地文件不受影响。"),
    ))

    bundle = local_frontend_index_path()
    checks.append(ReadinessCheck(
        name="frontend-bundle",
        ok=bundle.exists(),
        required=False,
        detail=(f"本地前端已构建：{bundle}" if bundle.exists()
                else "本地前端未构建：请运行 npm run build:frontend:local（API 仍可用）。"),
    ))

    writable, detail = _data_dir_writable()
    checks.append(ReadinessCheck(name="data-dir", ok=writable, required=True, detail=detail))

    return checks


def failed_required_checks(checks: list[ReadinessCheck]) -> list[ReadinessCheck]:
    return [check for check in checks if check.required and not check.ok]


def format_report(checks: list[ReadinessCheck]) -> str:
    lines = []
    for check in checks:
        mark = "OK " if check.ok else ("FAIL" if check.required else "WARN")
        lines.append(f"[{mark}] {check.name}: {check.detail}")
    return "\n".join(lines)
