"""A smaller copy of a recording the breath-gap cut left as it was.

When the cut renders, its re-encode is what makes the file small. When the cut is
declined (speech too faint to tell from the gaps, nothing worth removing) the
recording stays as it came, and a screen recording at 6 Mbps stays gigabytes:
one 52-minute meeting was 2.4 GB. This writes a compressed copy beside the
original, same picture size, same length, same sound, so the user has a file
they can keep, and FluentFlow never touches or removes the original.

Only for a video above ``MIN_SOURCE_BPS``. Below it there is little to gain, and
the one encoder setting measured fast enough (hardware HEVC at constant quality)
was measured to blur text on a 1.9 Mbps screen recording.

The settings are the measured ones (2026-10-12, 60 seconds of a 2940x1718, 60fps,
6 Mbps meeting recording, seconds to encode one minute): hardware HEVC at
``-q:v 50`` took 32 s keeping 60fps with ``-prio_speed 0``, 18 s with
``-prio_speed 1``, and 9 s at 30fps with ``-prio_speed 1``, the smallest file of
the three, with shared-document text indistinguishable from the source. The whole
52 minutes took 515 s and came out at 246 MB. A recording above 30fps is brought
to 30, one keyframe every 5 seconds as the rest of the product uses.

The copy is checked before it is offered: same duration within a second, same
picture size, audio present when the source had it. A copy that fails is deleted
and the reason recorded; the original is never at risk either way.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from backend.core import silence_cuts

logger = logging.getLogger(__name__)

MIN_SOURCE_BPS = 4_000_000
MAX_FPS = 30
KEYFRAME_SECONDS = 5
COMPRESSED_FILE_MARKER = "_压缩"
COMPRESSED_MARK = "fluentflow_compressed=1"

Runner = Callable[..., Any]


def _probe(source: Path, runner: Runner) -> dict[str, Any]:
    size = silence_cuts.probe_value(
        source, ["-select_streams", "v:0", "-show_entries", "stream=width,height"], runner=runner
    ).splitlines()
    width, height = (int(x) for x in size[0].split(",")[:2]) if size and "," in size[0] else (0, 0)
    audio = silence_cuts.probe_value(
        source, ["-select_streams", "a:0", "-show_entries", "stream=codec_type"], runner=runner
    ).strip().splitlines()[:1] == ["audio"]
    return {
        "has_video": silence_cuts.has_video_stream(source, runner=runner),
        "has_audio": audio,
        "width": width,
        "height": height,
        "bps": silence_cuts.container_bitrate_bps(source, runner=runner),
        "fps": silence_cuts.source_frame_rate(source, runner=runner),
        "duration": silence_cuts.probe_float(source, ["-show_entries", "format=duration"], runner=runner),
    }


def worth_compressing(probe: dict[str, Any]) -> bool:
    return bool(probe["has_video"]) and float(probe["bps"] or 0) >= MIN_SOURCE_BPS


def encode_args(probe: dict[str, Any]) -> list[str]:
    fps = int(probe.get("fps") or 0)
    out_fps = min(fps, MAX_FPS) if fps > 0 else MAX_FPS
    args = [
        "-c:v", "hevc_videotoolbox", "-tag:v", "hvc1", "-q:v", "50",
        "-spatial_aq", "1", "-prio_speed", "1",
    ]
    if fps > MAX_FPS:
        args += ["-r", str(MAX_FPS)]
    args += ["-g", str(out_fps * KEYFRAME_SECONDS), "-pix_fmt", "yuv420p"]
    return args


def hardware_encoder_available(runner: Runner = silence_cuts.run_command) -> bool:
    try:
        listed = runner([silence_cuts._ffmpeg_path(), "-hide_banner", "-encoders"])
    except Exception:  # noqa: BLE001 - no ffmpeg answers as "not available"
        return False
    return "hevc_videotoolbox" in (getattr(listed, "stdout", "") or "")


def target_path(source: Path) -> Path:
    base = f"{source.stem}{COMPRESSED_FILE_MARKER}"
    candidate = source.with_name(f"{base}.mp4")
    index = 2
    while candidate.exists():
        candidate = source.with_name(f"{base} ({index}).mp4")
        index += 1
    return candidate


def _verify(source_probe: dict[str, Any], output: Path, runner: Runner) -> str | None:
    """Why the copy cannot stand in for the original, or None."""
    copy = _probe(output, runner)
    if abs(float(copy["duration"]) - float(source_probe["duration"])) > 1.0:
        return f"压缩后时长 {copy['duration']:.1f} 秒，原片 {source_probe['duration']:.1f} 秒，对不上"
    if (copy["width"], copy["height"]) != (source_probe["width"], source_probe["height"]):
        return "压缩后画面尺寸和原片不一样"
    if source_probe["has_audio"] and not copy["has_audio"]:
        return "压缩后没有声音"
    return None


def compress_beside(source: Path, *, runner: Runner = silence_cuts.run_command) -> dict[str, Any]:
    """Write ``<name>_压缩.mp4`` beside ``source`` when it is worth it; say what happened."""
    try:
        probe = _probe(source, runner)
    except Exception as exc:  # noqa: BLE001 - an unreadable file simply is not compressed
        return {"compressed": False, "compress_skipped": f"读不出这个文件的码率（{exc}）"}
    if not worth_compressing(probe):
        return {"compressed": False, "compress_skipped": "码率不高，压缩省不了多少"}
    if not hardware_encoder_available(runner):
        return {"compressed": False, "compress_skipped": "这台电脑没有硬件 HEVC 编码器"}
    output = target_path(source)
    command = [
        silence_cuts._ffmpeg_path(), "-nostdin", "-v", "error", "-y", "-i", str(source),
        *encode_args(probe), "-c:a", "copy", "-metadata", f"comment={COMPRESSED_MARK}",
        "-movflags", "+faststart", str(output),
    ]
    try:
        result = runner(command)
        failed = getattr(result, "returncode", 0) != 0
        reason = (getattr(result, "stderr", "") or "").strip()[-300:] if failed else _verify(probe, output, runner)
    except Exception as exc:  # noqa: BLE001 - the original stands whatever goes wrong
        reason = str(exc)
    if reason:
        logger.warning("compressed copy of %s rejected: %s", source, reason)
        output.unlink(missing_ok=True)
        return {"compressed": False, "compress_error": f"压缩版没做成（{reason}），原文件不受影响。"}
    return {
        "compressed": True,
        "compressed_path": str(output),
        "compressed_name": output.name,
        "compressed_bytes": output.stat().st_size,
        "source_bytes": source.stat().st_size,
    }


__all__ = [
    "COMPRESSED_FILE_MARKER",
    "MIN_SOURCE_BPS",
    "compress_beside",
    "encode_args",
    "target_path",
    "worth_compressing",
]
