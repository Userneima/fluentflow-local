"""Edition-neutral Agent task-package core.

Holds the pure package shaping (transcript, note, artifacts, visuals) shared
by every edition. Edition-owned semantics — processing plan, decision log,
error diagnostics, usage/quota, execution location, and extra next actions —
are injected through :class:`AgentPackagePolicy` by the edition facades
(hosted ``agent_package.py``; the local edition builds its own policy).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from backend.core.result_schema import canonical_display_segments, canonical_raw_segments, sanitize_raw_segments
from backend.core.title_display import display_title_for_user
from backend.core.note_diagnosis import build_note_generation_diagnosis


AGENT_TASK_PACKAGE_VERSION = "1"


@dataclass(frozen=True)
class AgentPackagePolicy:
    """Edition-owned behavior injected into the package core.

    ``usage`` and ``execution`` may return ``None`` to omit their section
    (the local edition has no quota or desktop-sync concepts).
    ``extra_next_actions`` results are placed BEFORE the shared
    regenerate/wait actions, preserving the hosted ordering.
    """

    ensure_processing_plan: Callable[..., dict[str, Any]]
    build_processing_plan: Callable[..., Any]
    build_decision_log: Callable[..., Any]
    build_tool_trace: Callable[..., Any]
    diagnose_error: Callable[[Any], dict[str, Any]]
    usage: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], Optional[dict[str, Any]]]
    execution: Callable[[dict[str, Any]], Optional[dict[str, Any]]]
    extra_next_actions: Callable[[dict[str, Any], dict[str, Any]], list[dict[str, Any]]]


def _text(value: Any) -> str:
    return str(value or "").strip()


def note_generation_diagnosis(
    job: dict[str, Any],
    result: dict[str, Any],
    *,
    diagnose_error: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    return build_note_generation_diagnosis(
        job,
        result,
        diagnose_error=diagnose_error,
    )


def _artifact_local_path(task_id: str, artifact: dict[str, Any], artifact_root: Path | None) -> str | None:
    if artifact_root is None:
        return None
    filename = _text(artifact.get("filename"))
    if not filename:
        return None
    relative_path = Path(filename)
    if relative_path.is_absolute() or any(part in {"", ".", ".."} for part in relative_path.parts):
        return None
    return str((artifact_root / task_id / relative_path).expanduser())


def _agent_artifacts(task_id: str, result: dict[str, Any], artifact_root: Path | None) -> dict[str, dict[str, Any]]:
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
    payload: dict[str, dict[str, Any]] = {}
    for kind, artifact in artifacts.items():
        if not isinstance(artifact, dict):
            continue
        url = _text(artifact.get("url")) or f"/jobs/{task_id}/artifacts/{kind}"
        item = {
            "kind": str(kind),
            "filename": _text(artifact.get("filename")),
            "url": url,
            "download_url": url,
            "content_type": artifact.get("content_type"),
            "size_bytes": artifact.get("size_bytes"),
        }
        local_path = _artifact_local_path(task_id, artifact, artifact_root)
        if local_path:
            item["local_path"] = local_path
        payload[str(kind)] = {key: value for key, value in item.items() if value is not None and value != ""}
    return payload


def _agent_visual_artifacts(task_id: str, result: dict[str, Any], artifact_root: Path | None) -> dict[str, dict[str, Any]]:
    visual_artifacts = result.get("visual_artifacts")
    if not isinstance(visual_artifacts, dict):
        return {}
    payload: dict[str, dict[str, Any]] = {}
    for kind, artifact in visual_artifacts.items():
        if not isinstance(artifact, dict):
            continue
        key = str(kind)
        url = _text(artifact.get("url") or artifact.get("artifact_url"))
        item = {
            "kind": key,
            "filename": _text(artifact.get("filename")),
            "url": url,
            "download_url": url,
            "content_type": artifact.get("content_type"),
            "size_bytes": artifact.get("size_bytes"),
            "timestamp_seconds": artifact.get("timestamp_seconds"),
            "provider": _text(artifact.get("provider")),
        }
        local_path = _artifact_local_path(task_id, artifact, artifact_root)
        if local_path:
            item["local_path"] = local_path
        payload[key] = {field: value for field, value in item.items() if value not in (None, "")}
    return payload


def _agent_visual_evidence(result: dict[str, Any], visual_artifacts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    evidence = result.get("visual_evidence")
    if not isinstance(evidence, list):
        return []
    payload: list[dict[str, Any]] = []
    for index, item in enumerate(evidence, 1):
        if not isinstance(item, dict):
            continue
        artifact_kind = _text(item.get("artifact_kind"))
        artifact = visual_artifacts.get(artifact_kind, {}) if artifact_kind else {}
        entry = {
            "id": _text(item.get("id")) or f"visual_{index:03d}",
            "timestamp_seconds": item.get("timestamp_seconds"),
            "reason": _text(item.get("reason")),
            "note_section": _text(item.get("note_section")),
            "source": _text(item.get("source")),
            "confidence": _text(item.get("confidence")),
            "provider": _text(item.get("provider") or artifact.get("provider")),
            "artifact_kind": artifact_kind,
            "artifact_url": _text(item.get("artifact_url") or artifact.get("url")),
        }
        payload.append({field: value for field, value in entry.items() if value not in (None, "")})
    return payload


def _agent_visual_requests(result: dict[str, Any]) -> list[dict[str, Any]]:
    requests = result.get("visual_requests")
    if not isinstance(requests, list):
        return []
    payload: list[dict[str, Any]] = []
    for item in requests:
        if not isinstance(item, dict):
            continue
        entry = {
            "id": _text(item.get("id")),
            "note_section": _text(item.get("note_section")),
            "start_seconds": item.get("start_seconds"),
            "end_seconds": item.get("end_seconds"),
            "reason": _text(item.get("reason")),
            "query": _text(item.get("query")),
            "purpose": _text(item.get("purpose")),
            "priority": _text(item.get("priority")),
            "max_images": item.get("max_images"),
        }
        payload.append({field: value for field, value in entry.items() if value not in (None, "")})
    return payload


def _agent_visual_frame_selections(result: dict[str, Any]) -> list[dict[str, Any]]:
    selections = result.get("visual_frame_selections")
    if not isinstance(selections, list):
        return []
    payload: list[dict[str, Any]] = []
    for item in selections:
        if not isinstance(item, dict):
            continue
        entry = {
            "request_id": _text(item.get("request_id")),
            "note_section": _text(item.get("note_section")),
            "filename": _text(item.get("filename")),
            "caption": _text(item.get("caption")),
            "reason": _text(item.get("reason")),
            "confidence": _text(item.get("confidence")),
            "purpose": _text(item.get("purpose")),
            "timestamp_seconds": item.get("timestamp_seconds"),
        }
        payload.append({field: value for field, value in entry.items() if value not in (None, "")})
    return payload


def _agent_visual_key_moments(result: dict[str, Any]) -> list[dict[str, Any]]:
    moments = result.get("visual_key_moments")
    if not isinstance(moments, list):
        return []
    payload: list[dict[str, Any]] = []
    for index, item in enumerate(moments, 1):
        if not isinstance(item, dict):
            continue
        entry = {
            "id": _text(item.get("id")) or f"key_visual_{index:03d}",
            "request_id": _text(item.get("request_id")),
            "timestamp_seconds": item.get("timestamp_seconds"),
            "caption": _text(item.get("caption")),
            "reason": _text(item.get("reason")),
            "note_section": _text(item.get("note_section")),
            "confidence": _text(item.get("confidence")),
            "purpose": _text(item.get("purpose")) or "key_moment",
            "source": _text(item.get("source")),
            "provider": _text(item.get("provider")),
            "artifact_url": _text(item.get("artifact_url")),
            "filename": _text(item.get("filename")),
        }
        payload.append({field: value for field, value in entry.items() if value not in (None, "")})
    return payload


def _next_actions(
    job: dict[str, Any],
    diagnosis: dict[str, Any],
    extra_next_actions: Callable[[dict[str, Any], dict[str, Any]], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = list(extra_next_actions(job, diagnosis) or [])
    if diagnosis.get("retryable") and diagnosis.get("code") != "note_completed":
        actions.append({
            "action": "regenerate_note",
            "method": "POST",
            "path": f"/agent/v1/tasks/{job.get('task_id')}/note/regenerate",
            "reason": diagnosis.get("next_action") or diagnosis.get("title"),
        })
    if job.get("status") in {"queued", "running"}:
        actions.append({
            "action": "wait",
            "method": "GET",
            "path": f"/agent/v1/tasks/{job.get('task_id')}/package",
            "reason": "任务仍在处理，稍后再次读取任务包。",
        })
    return actions


def build_agent_task_package(
    job: dict[str, Any],
    *,
    policy: AgentPackagePolicy,
    artifact_root: Path | None = None,
) -> dict[str, Any]:
    task_id = _text(job.get("task_id"))
    raw_result = job.get("result")
    result = raw_result if isinstance(raw_result, dict) else {}
    raw_metadata = job.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    result = policy.ensure_processing_plan(result, job=job, metadata=metadata)
    raw_video_source = metadata.get("video_source")
    video_source = raw_video_source if isinstance(raw_video_source, dict) else {}
    raw_segments = canonical_raw_segments(result)
    display_segments = canonical_display_segments(result)
    transcript_text = _text(result.get("transcript_text"))
    corrected_transcript_text = _text(result.get("corrected_transcript_text"))
    corrected_segments = sanitize_raw_segments(result.get("corrected_segments"))
    transcript_corrections = result.get("transcript_corrections")
    correction_meta = result.get("transcript_correction") if isinstance(result.get("transcript_correction"), dict) else None
    if correction_meta is None and result.get("transcript_correction_status"):
        correction_meta = {"status": result.get("transcript_correction_status")}
    diagnosis = note_generation_diagnosis(job, result, diagnose_error=policy.diagnose_error)
    title = (
        _text(result.get("display_title"))
        or _text(metadata.get("display_title"))
        or _text(video_source.get("display_title"))
        or display_title_for_user(video_source.get("title"), result.get("filename") or job.get("source_filename"))
        or display_title_for_user(job.get("source_filename"), task_id)
        or task_id
    )
    artifacts = _agent_artifacts(task_id, result, artifact_root)
    visual_artifacts = _agent_visual_artifacts(task_id, result, artifact_root)
    visual_evidence = _agent_visual_evidence(result, visual_artifacts)
    visual_requests = _agent_visual_requests(result)
    visual_frame_selections = _agent_visual_frame_selections(result)
    visual_key_moments = _agent_visual_key_moments(result)
    note_status = diagnosis["status"]
    if result.get("summary_skipped"):
        note_status = "skipped"
    elif _text(result.get("summary_markdown")):
        note_status = "completed"

    package = {
        "agent_task_package_version": AGENT_TASK_PACKAGE_VERSION,
        "task": {
            "task_id": task_id,
            "status": job.get("status"),
            "stage": job.get("stage"),
            "progress": job.get("progress"),
            "created_at": job.get("created_at"),
            "updated_at": job.get("updated_at"),
        },
        "title": title,
        "source": {
            "type": job.get("source_type") or result.get("source"),
            "filename": result.get("filename") or job.get("source_filename"),
            "raw_title": result.get("raw_title") or metadata.get("raw_title") or video_source.get("raw_title"),
            "display_title": title,
            "url": video_source.get("url") or video_source.get("webpage_url"),
            "duration_seconds": result.get("audio_duration_seconds") or job.get("source_duration_seconds"),
            "file_size_mb": job.get("source_file_size_mb"),
            "video_source": video_source or None,
        },
        "transcript": {
            "available": bool(transcript_text or raw_segments or display_segments),
            "text": transcript_text,
            "preview": _text(result.get("transcript_text_preview") or transcript_text[:300]),
            "raw_segments": raw_segments,
            "display_segments": display_segments,
            "corrected_text": corrected_transcript_text,
            "corrected_segments": corrected_segments,
            "corrections": transcript_corrections if isinstance(transcript_corrections, list) else [],
            "correction": correction_meta,
            "note_input_source": result.get("note_generation_transcript_source") or "transcript_text",
            "raw_segment_count": len(raw_segments),
            "display_segment_count": len(display_segments),
            "corrected_segment_count": len(corrected_segments),
            "source_language": result.get("source_language"),
            "detected_language": result.get("detected_language"),
            "subtitle_mode": result.get("subtitle_mode"),
            "translation_status": result.get("translation_status"),
        },
        "note": {
            "status": note_status,
            "markdown": _text(result.get("summary_markdown")),
            "markdown_chars": len(_text(result.get("summary_markdown"))),
            # Who last wrote this note. Without it an agent re-reading a task
            # cannot tell its own write from a human edit or pipeline output,
            # and would regenerate over work somebody just did by hand.
            "edited": bool(result.get("summary_edited")),
            "edited_at": result.get("summary_edited_at"),
            "source": result.get("summary_source"),
            "source_label": result.get("summary_source_label"),
            "diagnosis": diagnosis,
            "requested_mode": result.get("requested_note_mode"),
            "resolved_mode": result.get("resolved_note_mode"),
            "prompt_preset": result.get("prompt_preset"),
            "prompt_preset_label": result.get("prompt_preset_label"),
            "stats": {
                "chunk_count": result.get("note_mode_chunk_count"),
                "segment_count": result.get("note_mode_segment_count"),
                "evidence_count": result.get("note_mode_evidence_count"),
                "chapter_count": result.get("note_mode_chapter_count"),
                "important_evidence_count": result.get("note_mode_important_evidence_count"),
                "covered_important_evidence_count": result.get("note_mode_covered_important_evidence_count"),
                "coverage_missing_count": result.get("note_mode_coverage_missing_count"),
            },
            "chapter_coverage": result.get("chapter_coverage") if isinstance(result.get("chapter_coverage"), dict) else None,
        },
        "artifacts": artifacts,
        "visual": {
            "available": bool(visual_evidence),
            "evidence": visual_evidence,
            "key_moments": visual_key_moments,
            "key_moments_available": bool(visual_key_moments),
            "artifacts": visual_artifacts,
            "requests": visual_requests,
            "frame_selections": visual_frame_selections,
            "status": result.get("visual_evidence_status"),
            "reason": result.get("visual_evidence_reason"),
            "key_moments_status": result.get("visual_key_moments_status"),
            "key_moments_reason": result.get("visual_key_moments_reason"),
            "pipeline": result.get("visual_evidence_pipeline"),
            "candidate_frame_count": len(result.get("frame_artifacts") or []) if isinstance(result.get("frame_artifacts"), list) else 0,
        },
    }
    usage = policy.usage(job, result, metadata)
    if usage is not None:
        package["usage"] = usage
    package["processing_plan"] = result.get("processing_plan") or policy.build_processing_plan(
        result, job=job, metadata=metadata
    )
    execution = policy.execution(metadata)
    if execution is not None:
        package["execution"] = execution
    package["tool_trace"] = policy.build_tool_trace(result, job=job)
    package["decision_log"] = policy.build_decision_log(result, job=job, metadata=metadata)
    package["next_actions"] = _next_actions(job, diagnosis, policy.extra_next_actions)
    return package
