"""Result artifact generation helpers."""

from __future__ import annotations

import logging
import shutil
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from backend.core.result_schema import (
    canonical_display_segments,
    canonical_raw_segments,
    normalize_result_for_storage,
    sanitize_display_segments,
    sanitize_raw_segments,
)
from backend.core.runtime_paths import default_artifact_dir

logger = logging.getLogger(__name__)


def artifact_storage_dir() -> Path:
    return default_artifact_dir()


def safe_filename_stem(value: str | None, fallback: str = "transcript") -> str:
    raw_stem = Path(value or fallback).stem or fallback
    safe_stem = "".join(
        ch if ch.isalnum() or ch in {" ", "-", "_", "."} else "_"
        for ch in raw_stem
    ).strip(" ._")
    return (safe_stem or fallback)[:96]


def artifact_url(task_id: str, kind: str, *, filename: str | None = None) -> str:
    if kind == "frame" and filename:
        frame_name = Path(filename).name
        if frame_name:
            return f"/jobs/{task_id}/artifacts/frame?file={quote(frame_name)}"
    return f"/jobs/{task_id}/artifacts/{kind}"


def artifact_filename(result: dict[str, Any], suffix: str) -> str:
    stem = safe_filename_stem(
        result.get("display_title") or result.get("filename") or result.get("source_filename"),
        fallback="transcript",
    )
    return f"{stem}{suffix}"


def _fmt_timestamp(seconds: Any, *, comma: bool) -> str:
    try:
        total_ms = max(0, int(round(float(seconds or 0) * 1000)))
    except (TypeError, ValueError):
        total_ms = 0
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    sep = "," if comma else "."
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{ms:03d}"


def format_srt(segments: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for index, segment in enumerate(segments, 1):
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        blocks.append(
            f"{index}\n{_fmt_timestamp(segment.get('start'), comma=True)} --> {_fmt_timestamp(segment.get('end'), comma=True)}\n{text}"
        )
    return "\n\n".join(blocks).rstrip() + ("\n" if blocks else "")


def format_vtt(segments: list[dict[str, Any]]) -> str:
    blocks = ["WEBVTT"]
    for segment in segments:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        blocks.append(
            f"{_fmt_timestamp(segment.get('start'), comma=False)} --> {_fmt_timestamp(segment.get('end'), comma=False)}\n{text}"
        )
    return "\n\n".join(blocks).rstrip() + "\n"


def bilingual_segments(source_segments: list[dict[str, Any]], translated_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not source_segments or not translated_segments:
        return []
    output: list[dict[str, Any]] = []
    for source, translated in zip(source_segments, translated_segments):
        source_text = str(source.get("text") or "").strip()
        zh_text = str(translated.get("text") or translated.get("text_zh") or "").strip()
        if not source_text or not zh_text:
            continue
        segment: dict[str, Any] = {
            "start": source.get("start"),
            "end": source.get("end"),
            "text": f"{source_text}\n{zh_text}",
        }
        if source.get("speaker"):
            segment["speaker"] = source.get("speaker")
        output.append(segment)
    return output


def with_canonical_result_segments(
    result: dict[str, Any],
    *,
    raw_segments: list[dict[str, Any]] | None = None,
    display_segments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    next_result = dict(result)
    raw = sanitize_raw_segments(raw_segments) if raw_segments is not None else canonical_raw_segments(next_result)
    if display_segments is not None:
        display = sanitize_display_segments(display_segments)
    else:
        display_probe = dict(next_result)
        if raw and not display_probe.get("raw_segments"):
            display_probe["raw_segments"] = raw
        display = canonical_display_segments(display_probe)
    if raw:
        next_result["raw_segments"] = raw
    if display:
        next_result["display_segments"] = display
        if any(str(segment.get("text_zh") or "").strip() for segment in display):
            next_result["subtitle_mode"] = "bilingual_zh"
        elif not next_result.get("subtitle_mode"):
            next_result["subtitle_mode"] = "source_only"
    return normalize_result_for_storage(next_result) or next_result


def subtitle_segments_from_display(display_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    subtitles: list[dict[str, Any]] = []
    for segment in sanitize_display_segments(display_segments):
        text = str(segment.get("text") or "").strip()
        text_zh = str(segment.get("text_zh") or "").strip()
        if not text_zh:
            continue
        next_segment = dict(segment)
        next_segment["text"] = "\n".join(part for part in (text, text_zh) if part)
        subtitles.append(next_segment)
    return subtitles


def write_text_artifact(task_id: str, kind: str, filename: str, content: str) -> dict[str, Any]:
    target_dir = artifact_storage_dir() / task_id
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / filename
    tmp = target_dir / f".{filename}.{uuid.uuid4().hex}.tmp"
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return {
        "kind": kind,
        "filename": filename,
        "url": artifact_url(task_id, kind),
        "size_bytes": path.stat().st_size,
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }


def _safe_artifact_relative_path(filename: str) -> Path:
    path = Path(filename)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Artifact filename must be a safe relative path")
    return path


def write_file_artifact(task_id: str, kind: str, filename: str, source_path: Path | str) -> dict[str, Any]:
    target_dir = artifact_storage_dir() / task_id
    target_dir.mkdir(parents=True, exist_ok=True)
    relative_path = _safe_artifact_relative_path(filename)
    path = target_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        shutil.copyfile(str(source_path), tmp)
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return {
        "kind": kind,
        "filename": str(relative_path).replace("\\", "/"),
        "url": artifact_url(task_id, kind, filename=str(relative_path)),
        "size_bytes": path.stat().st_size,
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }


def artifact_filename_for_uploaded_media(filename: str | None, fallback: str = "source_audio") -> str:
    raw = Path(filename or "").name
    suffix = Path(raw).suffix.lower()
    if suffix not in {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus", ".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}:
        suffix = ".bin"
    return f"{safe_filename_stem(raw, fallback=fallback)}{suffix}"


def attach_playback_audio_artifact(
    task_id: str,
    result: dict[str, Any],
    audio_path: Path | str,
    source_filename: str | None = None,
) -> dict[str, Any]:
    path = Path(audio_path)
    if not path.is_file():
        return result
    artifact_name = (
        artifact_filename_for_uploaded_media(source_filename)
        if source_filename
        else artifact_filename(result, "_audio.mp3")
    )
    try:
        artifact = write_file_artifact(task_id, "playback_audio", artifact_name, path)
    except Exception as exc:
        logger.warning("Playback audio artifact write failed for %s: %s", task_id, exc)
        return result
    next_result = dict(result)
    artifacts = dict(next_result.get("artifacts") or {})
    artifacts["playback_audio"] = artifact
    next_result["artifacts"] = artifacts
    next_result["playback_audio_available"] = True
    next_result["playback_audio_storage"] = "local"
    return next_result


def write_result_artifacts(task_id: str, result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    transcript = str(result.get("transcript_text") or "").strip()
    normalized_result = with_canonical_result_segments(result)
    raw_segments = canonical_raw_segments(normalized_result)
    display_segments = canonical_display_segments(normalized_result)
    if transcript:
        artifacts["transcript_txt"] = write_text_artifact(
            task_id,
            "transcript_txt",
            artifact_filename(result, ".txt"),
            transcript.rstrip() + "\n",
        )
    if raw_segments:
        artifacts["transcript_srt"] = write_text_artifact(
            task_id,
            "transcript_srt",
            artifact_filename(result, ".srt"),
            format_srt(raw_segments),
        )
        artifacts["transcript_vtt"] = write_text_artifact(
            task_id,
            "transcript_vtt",
            artifact_filename(result, ".vtt"),
            format_vtt(raw_segments),
        )
        bilingual = subtitle_segments_from_display(display_segments)
        if not bilingual:
            translated_segments = sanitize_raw_segments(result.get("translated_segments_zh"))
            bilingual = bilingual_segments(raw_segments, translated_segments)
        if bilingual:
            artifacts["transcript_bilingual_srt"] = write_text_artifact(
                task_id,
                "transcript_bilingual_srt",
                artifact_filename(result, "_bilingual_zh.srt"),
                format_srt(bilingual),
            )
            artifacts["transcript_bilingual_vtt"] = write_text_artifact(
                task_id,
                "transcript_bilingual_vtt",
                artifact_filename(result, "_bilingual_zh.vtt"),
                format_vtt(bilingual),
            )
    summary = str(result.get("summary_markdown") or "").strip()
    if summary:
        artifacts["summary_md"] = write_text_artifact(
            task_id,
            "summary_md",
            artifact_filename(result, "_summary.md"),
            summary.rstrip() + "\n",
        )
    frame_artifacts = result.get("frame_artifacts")
    if isinstance(frame_artifacts, list):
        for fa in frame_artifacts:
            if isinstance(fa, dict) and fa.get("kind") == "frame":
                key = f"frame_{Path(fa['filename']).stem}"
                artifacts[key] = fa
    return artifacts


def attach_result_artifacts(
    task_id: str,
    result: dict[str, Any],
    *,
    enrich_result: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_result = with_canonical_result_segments(result)
    if enrich_result is not None:
        normalized_result = enrich_result(normalized_result)
    try:
        artifacts = write_result_artifacts(task_id, normalized_result)
    except Exception as exc:
        logger.warning("Result artifact write failed for %s: %s", task_id, exc)
        return result
    if not artifacts:
        return normalized_result
    next_result = dict(normalized_result)
    next_result["artifacts"] = {**dict(normalized_result.get("artifacts") or {}), **artifacts}
    return next_result
