"""Extract candidate frames from video for multimodal note generation."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _ffmpeg_path() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("ffmpeg not found")
    return path


def _ffprobe_path() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise RuntimeError("ffprobe not found")
    return path


def _video_duration_seconds(video_path: str) -> float:
    result = subprocess.run(
        [
            _ffprobe_path(),
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        check=True, capture_output=True, text=True, timeout=15,
    )
    return max(0.0, float((result.stdout or "").strip()))


def _parse_showinfo_timestamps(stderr: str) -> list[float]:
    timestamps: list[float] = []
    for match in re.finditer(r"pts_time:([0-9]+(?:\.[0-9]+)?)", stderr or ""):
        try:
            timestamps.append(float(match.group(1)))
        except ValueError:
            continue
    return timestamps


def _selected_even_indices(total: int, keep: int) -> list[int]:
    if total <= 0 or keep <= 0:
        return []
    if total <= keep:
        return list(range(total))
    if keep == 1:
        return [0]
    return [round(i * (total - 1) / (keep - 1)) for i in range(keep)]


def _frame_quality_metadata(path: Path) -> dict[str, Any]:
    try:
        from PIL import Image, ImageFilter, ImageStat

        with Image.open(path) as image:
            gray = image.convert("L")
            stat = ImageStat.Stat(gray)
            edge_stat = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES))
            small = gray.resize((9, 8))
            pixels = list(small.getdata())
            bits = []
            for row in range(8):
                offset = row * 9
                for col in range(8):
                    bits.append("1" if pixels[offset + col] > pixels[offset + col + 1] else "0")
            visual_hash = f"{int(''.join(bits), 2):016x}"
            contrast = float(stat.stddev[0] or 0)
            edge_contrast = float(edge_stat.stddev[0] or 0)
            return {
                "visual_hash": visual_hash,
                "brightness": round(float(stat.mean[0] or 0), 2),
                "contrast": round(contrast, 2),
                "edge_contrast": round(edge_contrast, 2),
                "low_information": contrast < 2.0 and edge_contrast < 1.0,
            }
    except Exception:
        return {}


def _frame_record(path: Path, timestamp: float, source: str) -> dict[str, Any]:
    return {
        "path": str(path),
        "timestamp_seconds": round(timestamp, 1),
        "source": source,
        **_frame_quality_metadata(path),
    }


def _extract_scene_frames(
    video_path: str,
    output_dir: Path,
    *,
    threshold: float,
    max_frames: int,
) -> list[dict[str, Any]]:
    """Run ffmpeg scene detection and extract key frames."""
    for stale in output_dir.glob("scene_*.jpg"):
        stale.unlink(missing_ok=True)
    output_pattern = output_dir / "scene_%04d.jpg"
    cmd = [
        _ffmpeg_path(),
        "-y",
        "-i", str(video_path),
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-vsync", "vfr",
        "-q:v", "2",
        str(output_pattern),
    ]
    completed = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=120)
    frames = sorted(output_dir.glob("scene_*.jpg"))
    if int(getattr(completed, "returncode", 0) or 0) != 0 and not frames:
        return []
    duration = _video_duration_seconds(video_path)
    timestamps = _parse_showinfo_timestamps(completed.stderr or "")
    frame_records: list[tuple[Path, float]] = []
    for idx, path in enumerate(frames):
        fallback = duration * idx / max(len(frames), 1)
        timestamp = timestamps[idx] if idx < len(timestamps) else fallback
        frame_records.append((path, timestamp))

    selected_indices = set(_selected_even_indices(len(frame_records), max_frames))
    selected_records: list[tuple[Path, float]] = []
    for idx, record in enumerate(frame_records):
        if idx in selected_indices:
            selected_records.append(record)
        else:
            record[0].unlink(missing_ok=True)

    results: list[dict[str, Any]] = []
    for path, timestamp in selected_records:
        if path.is_file():
            results.append(_frame_record(path, timestamp, "scene"))
    return results


def _segment_capture_timestamps(segment: dict[str, Any]) -> list[float]:
    try:
        start = max(0.0, float(segment.get("start") or 0))
    except (TypeError, ValueError):
        return []
    try:
        end = float(segment.get("end")) if segment.get("end") is not None else start
    except (TypeError, ValueError):
        end = start
    end = max(start, end)
    if end - start < 1.5:
        return [round(start, 1)]
    mid = start + (end - start) / 2
    tail = max(start, end - 0.4)
    timestamps = [start, mid, tail]
    deduped: list[float] = []
    for ts in timestamps:
        rounded = round(ts, 1)
        if not deduped or abs(rounded - deduped[-1]) >= 1.0:
            deduped.append(rounded)
    return deduped


def _extract_timepoint_frames(
    video_path: str,
    output_dir: Path,
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract local candidate frames around requested transcript time windows."""
    results: list[dict[str, Any]] = []
    seen_timestamps: set[float] = set()
    for segment_index, segment in enumerate(segments, start=1):
        for ts in _segment_capture_timestamps(segment):
            if ts in seen_timestamps:
                continue
            seen_timestamps.add(ts)
            mm = int(ts // 60)
            ss = int(ts % 60)
            tenth = int(round((ts - int(ts)) * 10))
            filename = f"ts_{mm:02d}{ss:02d}_{tenth:d}.jpg"
            output = output_dir / filename
            cmd = [
                _ffmpeg_path(),
                "-y",
                "-ss", str(ts),
                "-i", str(video_path),
                "-frames:v", "1",
                "-q:v", "2",
                str(output),
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
            except subprocess.CalledProcessError:
                continue
            if output.is_file():
                record = _frame_record(output, ts, "timepoint")
                for key in ("visual_request_id", "note_section", "query", "reason"):
                    if segment.get(key):
                        record[key] = segment.get(key)
                record["segment_index"] = segment_index
                results.append(record)
    return results


def _fallback_timestamps(duration: float, max_frames: int) -> list[float]:
    if duration <= 0 or max_frames <= 0:
        return []
    count = 1 if duration < 2 else min(max_frames, 5)
    if count == 1:
        return [round(max(0.0, duration / 2), 1)]
    return [round(duration * (index + 1) / (count + 1), 1) for index in range(count)]


def _extract_fallback_frames(
    video_path: str,
    output_dir: Path,
    *,
    max_frames: int,
) -> list[dict[str, Any]]:
    """Extract evenly spaced frames when scene and transcript cues find nothing."""
    duration = _video_duration_seconds(video_path)
    results: list[dict[str, Any]] = []
    for index, ts in enumerate(_fallback_timestamps(duration, max_frames), start=1):
        output = output_dir / f"fallback_{index:04d}.jpg"
        cmd = [
            _ffmpeg_path(),
            "-y",
            "-ss", str(ts),
            "-i", str(video_path),
            "-frames:v", "1",
            "-q:v", "2",
            str(output),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
        except subprocess.CalledProcessError:
            continue
        if output.is_file():
            results.append(_frame_record(output, ts, "fallback"))
    return results


def _deduplicate_frames(
    scene_frames: list[dict[str, Any]],
    timepoint_frames: list[dict[str, Any]],
    *,
    min_gap_seconds: float = 2.0,
) -> list[dict[str, Any]]:
    """Merge scene and timepoint frames, dropping near-duplicates within min_gap_seconds."""
    merged = sorted(scene_frames + timepoint_frames, key=lambda f: f["timestamp_seconds"])
    if not merged:
        return []
    deduped: list[dict[str, Any]] = [merged[0]]
    for frame in merged[1:]:
        if frame["timestamp_seconds"] - deduped[-1]["timestamp_seconds"] >= min_gap_seconds:
            deduped.append(frame)
        else:
            # Transcript-derived timepoints map better to note sections than raw scene changes.
            if frame["source"] == "timepoint" and deduped[-1]["source"] == "scene":
                deduped[-1] = frame
    return deduped


def extract_candidate_frames(
    video_path: str,
    output_dir: Path,
    segments: list[dict[str, Any]] | None = None,
    *,
    scene_threshold: float = 0.3,
    max_scene_frames: int = 30,
    min_gap_seconds: float = 2.0,
) -> list[dict[str, Any]]:
    """Extract candidate still frames from a video file.

    Uses ffmpeg scene-detection to find significant visual changes, optionally
    supplemented by per-segment timepoint captures. Returns a deduplicated list
    of frame metadata sorted by timestamp.

    Args:
        video_path: Path to the source video file.
        output_dir: Directory to write JPEG frames into.
        segments: Optional transcript segments with ``start`` timestamps.
        scene_threshold: ffmpeg scene change sensitivity (0.0-1.0).
        max_scene_frames: Upper bound on scene frames kept.
        min_gap_seconds: Minimum gap between consecutive output frames.

    Returns:
        List of dicts with keys ``path``, ``timestamp_seconds``, and ``source``.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    scene_frames: list[dict[str, Any]] = []
    if max_scene_frames > 0:
        try:
            scene_frames = _extract_scene_frames(
                video_path, output_dir, threshold=scene_threshold, max_frames=max_scene_frames
            )
        except Exception as exc:
            logger.warning("Scene frame extraction failed: %s", exc)

    timepoint_frames: list[dict[str, Any]] = []
    if segments:
        try:
            timepoint_frames = _extract_timepoint_frames(video_path, output_dir, segments)
        except Exception as exc:
            logger.warning("Timepoint frame extraction failed: %s", exc)

    fallback_frames: list[dict[str, Any]] = []
    if not scene_frames and not timepoint_frames:
        try:
            fallback_frames = _extract_fallback_frames(video_path, output_dir, max_frames=max_scene_frames)
        except Exception as exc:
            logger.warning("Fallback frame extraction failed: %s", exc)

    merged = _deduplicate_frames(scene_frames, timepoint_frames, min_gap_seconds=min_gap_seconds)
    if fallback_frames and not merged:
        return _deduplicate_frames([], fallback_frames, min_gap_seconds=min_gap_seconds)
    return merged


__all__ = ["extract_candidate_frames"]
