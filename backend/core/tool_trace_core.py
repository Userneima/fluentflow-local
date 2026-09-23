"""Edition-neutral tool-trace builder from completed job result + metadata.

The trace maps real execution events into an inspectable ordered list of
steps derived from result fields, job metadata, and stage markers that
already exist in the system. Transcription in this edition always runs on the
local machine, so the STT step is always the local engine.
"""

from __future__ import annotations

from typing import Any

TOOL_TRACE_VERSION = "1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _tool_label(tool_id: str, result: dict[str, Any], job: dict[str, Any]) -> str:
    labels = {
        "resolve_link": "解析链接",
        "download_video": "下载视频",
        "save_source": "保存源文件",
        "extract_audio": "提取音频",
        "local_stt": "本地转录",
        "diarize_speakers": "说话人区分",
        "cleanup_transcript": "清洗转录",
        "rebuild_paragraphs": "重组段落",
        "generate_note": "生成笔记",
        "save_artifacts": "保存产物",
        "export_lark": "导出飞书",
        "parse_subtitles": "解析字幕",
    }
    return labels.get(tool_id, tool_id)


def _tool_vendor(tool_id: str, result: dict[str, Any], job: dict[str, Any]) -> str | None:
    if tool_id == "local_stt":
        return "faster-whisper"
    if tool_id == "diarize_speakers":
        return "pyannote"
    if tool_id in ("generate_note", "plan_note_mode"):
        return result.get("prompt_preset_label") or result.get("ai_provider")
    return None


def _step_status(step: dict[str, Any]) -> str:
    error = _text(step.get("error_reason"))
    if error:
        return "failed"
    if step.get("started_at"):
        return "completed"
    return "pending"


def _event_duration_seconds(event: dict[str, Any]) -> float | None:
    value = event.get("duration_seconds")
    if isinstance(value, (int, float)) and value >= 0:
        return round(float(value), 3)
    return None


def _step_metadata(tool_id: str, result: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if tool_id == "local_stt":
        meta["model"] = result.get("stt_model")
        meta["language"] = result.get("detected_language") or result.get("source_language")
        meta["realtime_factor"] = result.get("stt_realtime_factor")
        meta["device"] = result.get("stt_device")
    elif tool_id == "diarize_speakers":
        speaker_info = result.get("speaker_diarization")
        if isinstance(speaker_info, dict):
            meta["speaker_count"] = speaker_info.get("speaker_count")
    elif tool_id == "cleanup_transcript":
        cleanup_info = result.get("transcript_cleanup")
        if isinstance(cleanup_info, dict):
            meta["issues"] = cleanup_info.get("issues")
            meta["removed_segments"] = cleanup_info.get("removed_segment_count")
    elif tool_id == "generate_note":
        meta["note_mode"] = result.get("resolved_note_mode") or result.get("requested_note_mode")
        meta["provider"] = result.get("ai_provider")
        meta["model"] = result.get("ai_model")
        meta["prompt_preset"] = result.get("prompt_preset")
        meta["chunk_count"] = result.get("note_mode_chunk_count")
    elif tool_id == "export_lark":
        lark_resp = result.get("lark_response")
        if isinstance(lark_resp, dict):
            meta["doc_url"] = lark_resp.get("url")
    meta = {key: value for key, value in meta.items() if value is not None and value != ""}
    return meta


def _make_step(
    tool_id: str,
    result: dict[str, Any],
    job: dict[str, Any],
    *,
    status: str = "completed",
    duration_seconds: float | None = None,
    error_reason: str | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
) -> dict[str, Any]:
    step = {
        "id": tool_id,
        "label": _tool_label(tool_id, result, job),
        "tool": tool_id,
        "status": status,
    }
    vendor = _tool_vendor(tool_id, result, job)
    if vendor:
        step["vendor"] = vendor
    meta = _step_metadata(tool_id, result, job)
    if meta:
        step["metadata"] = meta
    if duration_seconds is not None:
        step["duration_seconds"] = duration_seconds
    if started_at:
        step["started_at"] = started_at
    if ended_at:
        step["ended_at"] = ended_at
    if error_reason:
        step["error_reason"] = error_reason
    return step


def _has_audio_stages(result: dict[str, Any], job: dict[str, Any]) -> bool:
    source_type = _text(result.get("source") or job.get("source_type"))
    if source_type in {"transcript_file", "transcript_text"}:
        return False
    if source_type in {"video", "audio", "video_link", "douyin", "youtube", "media"}:
        return True
    stt_provider = _text(result.get("stt_provider") or job.get("stt_provider"))
    if stt_provider:
        return True
    if isinstance(result.get("raw_segments"), list) or isinstance(result.get("segments"), list):
        return True
    return bool(result.get("audio_duration_seconds"))


def build_tool_trace(
    result: dict[str, Any],
    *,
    job: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = result if isinstance(result, dict) else {}
    job = job if isinstance(job, dict) else {}
    source_type = _text(result.get("source") or job.get("source_type"))
    has_audio = _has_audio_stages(result, job)
    steps: list[dict[str, Any]] = []
    error_steps: list[str] = []

    # Step 1: Source ingestion
    job_created = _text(job.get("created_at"))
    if source_type == "video_link":
        steps.append(_make_step("resolve_link", result, job, started_at=job_created))
        steps.append(_make_step("download_video", result, job, started_at=job_created))
    elif source_type == "transcript_file":
        steps.append(_make_step("parse_subtitles", result, job, started_at=job_created))
    else:
        steps.append(_make_step("save_source", result, job, started_at=job_created))

    # Step 2: Audio extraction (audio/video/link only)
    if has_audio and source_type != "transcript_file":
        steps.append(_make_step("extract_audio", result, job,
            status="completed",
            started_at=job_created,
        ))

    # Step 3: STT (audio/video/link only)
    if has_audio:
        stt_tool = "local_stt"
        stt_error = _text(result.get("stt_error"))
        stt_dur = result.get("stt_elapsed_seconds")
        stt_step = _make_step(stt_tool, result, job,
            status="failed" if stt_error else "completed",
            duration_seconds=round(float(stt_dur), 3) if isinstance(stt_dur, (int, float)) else None,
            error_reason=stt_error or None,
        )
        if stt_error:
            error_steps.append(stt_tool)
        steps.append(stt_step)

    # Step 4: Speaker diarization (optional)
    speaker_info = result.get("speaker_diarization")
    has_transcript = bool(
        _text(result.get("transcript_text"))
        or isinstance(result.get("raw_segments"), list)
        or isinstance(result.get("segments"), list)
    )
    if has_audio and has_transcript and isinstance(speaker_info, dict) and speaker_info.get("requested"):
        dia_status = "completed" if speaker_info.get("applied") else "failed"
        dia_error = speaker_info.get("error_reason") if not speaker_info.get("applied") else None
        dia_step = _make_step("diarize_speakers", result, job,
            status=dia_status,
            error_reason=dia_error,
        )
        if dia_status == "failed":
            error_steps.append("diarize_speakers")
        steps.append(dia_step)

    # Step 5: Cleanup + rebuild (audio paths only)
    if has_audio and has_transcript:
        cleanup_info = result.get("transcript_cleanup")
        if isinstance(cleanup_info, dict) and (cleanup_info.get("applied_count") or cleanup_info.get("issues")):
            steps.append(_make_step("cleanup_transcript", result, job))
        steps.append(_make_step("rebuild_paragraphs", result, job))

    # Step 6: Note generation
    summary_skipped = result.get("summary_skipped") or result.get("summary_status") == "skipped"
    summary_error = _text(result.get("summary_error"))
    summary_md = _text(result.get("summary_markdown"))
    note_in_progress = not summary_md and not summary_skipped and not summary_error and has_transcript

    if not summary_skipped:
        if summary_md:
            steps.append(_make_step("generate_note", result, job))
        elif summary_error:
            steps.append(_make_step("generate_note", result, job,
                status="failed",
                error_reason=summary_error,
            ))
            error_steps.append("generate_note")
        elif note_in_progress:
            steps.append(_make_step("generate_note", result, job, status="pending"))
            error_steps.append("generate_note")

    # Step 7: Artifacts
    artifacts = result.get("artifacts")
    if isinstance(artifacts, dict) and artifacts:
        steps.append(_make_step("save_artifacts", result, job))

    # Step 8: Lark export
    lark_resp = result.get("lark_response")
    lark_error = result.get("lark_error")
    if isinstance(lark_resp, dict) or lark_error:
        lark_status = "completed" if isinstance(lark_resp, dict) else "failed"
        lark_err = _text(lark_error) if lark_status == "failed" else None
        steps.append(_make_step("export_lark", result, job,
            status=lark_status,
            error_reason=lark_err,
        ))
        if lark_status == "failed":
            error_steps.append("export_lark")

    # Build overall trace
    trace_status = "completed"
    if error_steps:
        trace_status = "partial"
    if job.get("status") in ("queued", "running"):
        trace_status = "pending"
    if job.get("status") == "failed" or job.get("status") == "cancelled":
        trace_status = "failed"

    return {
        "tool_trace_version": TOOL_TRACE_VERSION,
        "status": trace_status,
        "step_count": len(steps),
        "failed_step_ids": sorted(set(error_steps)),
        "steps": steps,
    }
