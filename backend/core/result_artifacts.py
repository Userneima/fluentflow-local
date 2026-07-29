"""Result artifacts, transcript/segment canonicalization, and edit backups,
extracted from server_helpers.py so both the hosted and local editions can
(re)generate downloadable artifacts and edit backups without the hosted helper
barrel. Depends only on shared modules (result_schema, subtitle_format,
storage_paths, storage_cleanup) plus stdlib; no server_helpers imports."""

from __future__ import annotations

import json
import logging
import shutil
import uuid
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
from backend.core.storage_cleanup import safe_filename_stem as _safe_filename_stem
from backend.core.storage_paths import (
    _artifact_storage_dir,
    _edited_transcript_dir,
    _transcript_edit_records_dir,
)
from backend.core.subtitle_format import _format_srt, _format_vtt

logger = logging.getLogger(__name__)


def _format_backup_timestamp(seconds: Any) -> str:
    try:
        total = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        total = 0
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_edited_transcript_backup(transcript: str, segments: list[dict[str, Any]]) -> str:
    usable_segments = [
        segment for segment in segments
        if isinstance(segment, dict) and str(segment.get("text") or "").strip()
    ]
    if not usable_segments:
        return transcript.rstrip() + "\n"
    lines = [
        f"[{_format_backup_timestamp(segment.get('start'))}] {str(segment.get('text') or '').strip()}"
        for segment in usable_segments
    ]
    return "\n".join(lines).rstrip() + "\n"


def _artifact_url(task_id: str, kind: str, *, filename: str | None = None) -> str:
    if kind == "frame" and filename:
        frame_name = Path(filename).name
        if frame_name:
            return f"/jobs/{task_id}/artifacts/frame?file={quote(frame_name)}"
    return f"/jobs/{task_id}/artifacts/{kind}"


def _artifact_filename(result: dict[str, Any], suffix: str) -> str:
    stem = _safe_filename_stem(
        result.get("display_title") or result.get("filename") or result.get("source_filename"),
        fallback="transcript",
    )
    return f"{stem}{suffix}"




def _bilingual_segments(
    source_segments: list[dict[str, Any]],
    translated_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
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


def _sanitize_bilingual_segments(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    segments: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text_en = str(item.get("text") or item.get("text_en") or "").strip()
        text_zh = str(item.get("text_zh") or item.get("zh") or "").strip()
        if not text_en or not text_zh:
            continue
        segment: dict[str, Any] = {
            "text": f"{text_en}\n{text_zh}",
        }
        for key in ("start", "end"):
            try:
                segment[key] = float(item.get(key) or 0)
            except (TypeError, ValueError):
                segment[key] = 0.0
        if item.get("speaker"):
            segment["speaker"] = str(item.get("speaker"))
        segments.append(segment)
    return segments


def _sanitize_display_segments(value: Any) -> list[dict[str, Any]]:
    return sanitize_display_segments(value)


def _canonical_raw_segments(result: dict[str, Any]) -> list[dict[str, Any]]:
    return canonical_raw_segments(result)


def _canonical_display_segments(result: dict[str, Any]) -> list[dict[str, Any]]:
    return canonical_display_segments(result)


def _with_canonical_result_segments(
    result: dict[str, Any],
    *,
    raw_segments: list[dict[str, Any]] | None = None,
    display_segments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    next_result = dict(result)
    raw = _sanitize_edit_segments(raw_segments) if raw_segments is not None else _canonical_raw_segments(next_result)
    if display_segments is not None:
        display = _sanitize_display_segments(display_segments)
    else:
        display_probe = dict(next_result)
        if raw and not display_probe.get("raw_segments"):
            display_probe["raw_segments"] = raw
        display = _canonical_display_segments(display_probe)
    if raw:
        next_result["raw_segments"] = raw
    if display:
        next_result["display_segments"] = display
        if any(str(segment.get("text_zh") or "").strip() for segment in display):
            next_result["subtitle_mode"] = "bilingual_zh"
        elif not next_result.get("subtitle_mode"):
            next_result["subtitle_mode"] = "source_only"
    return normalize_result_for_storage(next_result) or next_result


def _subtitle_segments_from_display(display_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    subtitles: list[dict[str, Any]] = []
    for segment in _sanitize_display_segments(display_segments):
        text = str(segment.get("text") or "").strip()
        text_zh = str(segment.get("text_zh") or "").strip()
        if not text_zh:
            continue
        next_segment = dict(segment)
        next_segment["text"] = "\n".join(part for part in (text, text_zh) if part)
        subtitles.append(next_segment)
    return subtitles


def _write_text_artifact(task_id: str, kind: str, filename: str, content: str) -> dict[str, Any]:
    target_dir = _artifact_storage_dir() / task_id
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
        "url": _artifact_url(task_id, kind),
        "size_bytes": path.stat().st_size,
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }


def _safe_artifact_relative_path(filename: str) -> Path:
    path = Path(filename)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Artifact filename must be a safe relative path")
    return path


def _write_file_artifact(task_id: str, kind: str, filename: str, source_path: Path | str) -> dict[str, Any]:
    target_dir = _artifact_storage_dir() / task_id
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
        "url": _artifact_url(task_id, kind, filename=str(relative_path)),
        "size_bytes": path.stat().st_size,
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }


def _artifact_filename_for_uploaded_media(filename: str | None, fallback: str = "source_audio") -> str:
    raw = Path(filename or "").name
    suffix = Path(raw).suffix.lower()
    if suffix not in {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus", ".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}:
        suffix = ".bin"
    return f"{_safe_filename_stem(raw, fallback=fallback)}{suffix}"


def _attach_playback_audio_artifact(
    task_id: str,
    result: dict[str, Any],
    audio_path: Path | str,
    source_filename: str | None = None,
) -> dict[str, Any]:
    path = Path(audio_path)
    if not path.is_file():
        return result
    artifact_filename = (
        _artifact_filename_for_uploaded_media(source_filename)
        if source_filename
        else _artifact_filename(result, "_audio.mp3")
    )
    try:
        artifact = _write_file_artifact(
            task_id,
            "playback_audio",
            artifact_filename,
            path,
        )
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


def _write_result_artifacts(task_id: str, result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    transcript = str(result.get("transcript_text") or "").strip()
    normalized_result = _with_canonical_result_segments(result)
    raw_segments = _canonical_raw_segments(normalized_result)
    display_segments = _canonical_display_segments(normalized_result)
    if transcript:
        artifacts["transcript_txt"] = _write_text_artifact(
            task_id,
            "transcript_txt",
            _artifact_filename(result, ".txt"),
            transcript.rstrip() + "\n",
        )
    if raw_segments:
        artifacts["transcript_srt"] = _write_text_artifact(
            task_id,
            "transcript_srt",
            _artifact_filename(result, ".srt"),
            _format_srt(raw_segments),
        )
        artifacts["transcript_vtt"] = _write_text_artifact(
            task_id,
            "transcript_vtt",
            _artifact_filename(result, ".vtt"),
            _format_vtt(raw_segments),
        )
        bilingual = _subtitle_segments_from_display(display_segments)
        if not bilingual:
            translated_segments = _sanitize_edit_segments(result.get("translated_segments_zh"))
            bilingual = _bilingual_segments(raw_segments, translated_segments)
        if bilingual:
            artifacts["transcript_bilingual_srt"] = _write_text_artifact(
                task_id,
                "transcript_bilingual_srt",
                _artifact_filename(result, "_bilingual_zh.srt"),
                _format_srt(bilingual),
            )
            artifacts["transcript_bilingual_vtt"] = _write_text_artifact(
                task_id,
                "transcript_bilingual_vtt",
                _artifact_filename(result, "_bilingual_zh.vtt"),
                _format_vtt(bilingual),
            )
    summary = str(result.get("summary_markdown") or "").strip()
    if summary:
        artifacts["summary_md"] = _write_text_artifact(
            task_id,
            "summary_md",
            _artifact_filename(result, "_summary.md"),
            summary.rstrip() + "\n",
        )
    frame_artifacts = result.get("frame_artifacts")
    if isinstance(frame_artifacts, list):
        for frame_artifact in frame_artifacts:
            if isinstance(frame_artifact, dict) and frame_artifact.get("kind") == "frame":
                key = f"frame_{Path(str(frame_artifact.get('filename') or '')).stem}"
                artifacts[key] = frame_artifact
    return artifacts


def _attach_result_artifacts(task_id: str, result: dict[str, Any]) -> dict[str, Any]:
    normalized_result = _with_canonical_result_segments(result)
    try:
        artifacts = _write_result_artifacts(task_id, normalized_result)
    except Exception as exc:
        logger.warning("Result artifact write failed for %s: %s", task_id, exc)
        return result
    if not artifacts:
        return normalized_result
    next_result = dict(normalized_result)
    next_result["artifacts"] = {**dict(normalized_result.get("artifacts") or {}), **artifacts}
    return next_result


def _write_edited_transcript_backup(task_id: str, result: dict[str, Any]) -> Path:
    stem = _safe_filename_stem(
        result.get("display_title") or result.get("filename") or result.get("source_filename"),
        fallback=task_id or "transcript",
    )
    task_suffix = _safe_filename_stem(task_id or "task")[:12]
    target_dir = _edited_transcript_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{stem}__{task_suffix}_edited.txt"
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    content = _format_edited_transcript_backup(
        str(result.get("transcript_text") or ""),
        _canonical_raw_segments(result),
    )
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(target)
    finally:
        if tmp.exists():
            tmp.unlink()
    return target


def _sanitize_edit_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    records: list[dict[str, Any]] = []
    allowed_keys = {
        "index",
        "start",
        "end",
        "before",
        "after",
        "previous_before",
        "next_before",
        "previous_after",
        "next_after",
        "created_at",
    }
    for item in value[:500]:
        if not isinstance(item, dict):
            continue
        record: dict[str, Any] = {}
        for key in allowed_keys:
            if key not in item:
                continue
            raw = item.get(key)
            if key in {"index"}:
                try:
                    record[key] = int(raw)
                except (TypeError, ValueError):
                    record[key] = 0
            elif key in {"start", "end"}:
                try:
                    record[key] = float(raw)
                except (TypeError, ValueError):
                    record[key] = 0.0
            else:
                record[key] = str(raw or "")[:4000]
        if str(record.get("before") or "").strip() != str(record.get("after") or "").strip():
            records.append(record)
    return records


def _write_transcript_edit_records_backup(task_id: str, result: dict[str, Any], records: list[dict[str, Any]]) -> Path:
    stem = _safe_filename_stem(
        result.get("display_title") or result.get("filename") or result.get("source_filename"),
        fallback=task_id or "transcript",
    )
    task_suffix = _safe_filename_stem(task_id or "task")[:12]
    target_dir = _transcript_edit_records_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{stem}__{task_suffix}_edit_records.json"
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    payload = {
        "task_id": task_id,
        "source_filename": result.get("filename") or result.get("source_filename"),
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "record_count": len(records),
        "records": records,
    }
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(target)
    finally:
        if tmp.exists():
            tmp.unlink()
    return target


def _sanitize_edit_segments(value: Any) -> list[dict[str, Any]]:
    return sanitize_raw_segments(value)
