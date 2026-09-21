"""Local-edition startup readiness checks.

One source of truth for "can this machine actually run FluentFlow Local":
the composition root logs failures at startup, and the launcher / CLI script
(`scripts/check_local_readiness.py`) prints the same report before booting.

Checks must not load a model, download anything, or reach the network: this
report runs on every launch, and a check that fetched 3GB to answer a question
would be worse than the failure it was meant to report. The transcription
checks below do import the engine module — asking a lighter question elsewhere
is how an installer ends up downloading one model while a run loads another.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from dataclasses import dataclass

from backend.core.frontend_paths import local_frontend_index_path
from backend.core.runtime_paths import ensure_workspace_recorded
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
    # Reports *where and why*, not just "ok". A workspace resolved to the wrong
    # place, or scattered by per-path overrides, presents as an empty task list —
    # indistinguishable from having lost every record. Startup is the one moment
    # that can say so out loud, so it does.
    workspace = ensure_workspace_recorded()
    root = workspace.root
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".fluentflow-write-check"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, f"数据目录可写：{workspace.describe()}"
    except OSError as exc:
        return False, f"数据目录不可写（{root}）：{exc}。可用 FLUENTFLOW_DATA_DIR 指定其他目录。"


def _ffmpeg_install_hint() -> str:
    if sys.platform.startswith("win"):
        return "未找到 ffmpeg：请安装 FFmpeg（例如执行 winget install Gyan.FFmpeg），然后重新打开启动器。"
    return "未找到 ffmpeg：请先安装（macOS 执行 brew install ffmpeg）。"


def _transcription_checks() -> list[ReadinessCheck]:
    """Which lane will run, and whether its weights are already here.

    Both are reported rather than inferred. "Is it using the Apple GPU" was
    answerable only from a line the installer printed once and nobody can see
    again, and "why is my first video taking so long" had no answer at all
    while a multi-gigabyte download was the reason.

    Neither is required: a missing model downloads itself on first use, and the
    slow lane still transcribes. They are here so that both facts are visible
    before a user waits on them.
    """
    try:
        from backend.core.local_stt import resolve_local_stt_engine
        from backend.core.local_stt_assets import is_downloaded, planned_model
    except Exception as exc:  # noqa: BLE001 - the faster-whisper check above already said so
        return [ReadinessCheck(
            name="stt-engine",
            ok=False,
            required=False,
            detail=f"无法确定本地转录引擎：{exc}",
        )]

    checks: list[ReadinessCheck] = []
    lane, lane_reason = resolve_local_stt_engine()
    if lane == "mlx":
        engine_detail = "Apple 芯片加速（mlx-whisper）"
    elif lane_reason:
        engine_detail = f"CPU（faster-whisper）：{lane_reason}"
    else:
        engine_detail = "faster-whisper"
    checks.append(ReadinessCheck(name="stt-engine", ok=True, required=False, detail=engine_detail))

    try:
        plan = planned_model()
        present = is_downloaded(plan)
    except Exception as exc:  # noqa: BLE001 - never fail a launch over a status line
        checks.append(ReadinessCheck(
            name="stt-model",
            ok=False,
            required=False,
            detail=f"无法确定转录模型状态：{exc}",
        ))
        return checks

    if present:
        detail = f"{plan.model_size} 已下载（{plan.repo_id}）"
        if plan.downgraded:
            detail += f"；这台机器跑不动 {plan.requested_size}，已按 {plan.model_size} 准备"
    else:
        detail = (
            f"转录模型 {plan.model_size} 尚未下载（{plan.repo_id}）。"
            "第一次转录会自动下载，需要几 GB 空间和一段等待；"
            "也可以先运行 python scripts/stt_model.py fetch 下好"
            "（网络到 huggingface.co 不通时它会自动改用镜像源）。"
        )
    checks.append(ReadinessCheck(name="stt-model", ok=present, required=False, detail=detail))
    return checks


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
                else "本地前端未构建：请运行 npm run build:frontend（API 仍可用）。"),
    ))

    writable, detail = _data_dir_writable()
    checks.append(ReadinessCheck(name="data-dir", ok=writable, required=True, detail=detail))

    checks.extend(_transcription_checks())

    # Not required: without it transcription still runs, just on the CPU. It is
    # reported so that "why is this so slow" has an answer here rather than in
    # a log line nobody reads.
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
                f"({', '.join(gpu_runtime.missing_dlls)}). Transcription will use the CPU; "
                f"run {gpu_runtime.install_hint} to enable GPU transcription."
            ),
        ))

    return checks


def failed_required_checks(checks: list[ReadinessCheck]) -> list[ReadinessCheck]:
    return [check for check in checks if check.required and not check.ok]


def format_report(checks: list[ReadinessCheck]) -> str:
    lines = []
    for check in checks:
        mark = "OK " if check.ok else ("FAIL" if check.required else "WARN")
        lines.append(f"[{mark}] {check.name}: {check.detail}")
    return "\n".join(lines)
