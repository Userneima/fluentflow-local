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

from backend.core.result_artifacts import (
    DEBREATH_CUT_LIST_KIND,
    DEBREATH_MEDIA_KIND,
    DEBREATH_TRANSCRIPT_KIND,
    VISUAL_NOTE_KIND,
)
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


def _agent_debreath(result: dict[str, Any], artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Breath-gap removal state, flattened for an agent that has to decide.

    ``available`` answers "has this been run at all", which is what separates a
    task the agent may submit from one it should read. The numbers are lifted out
    of the stored plan because an agent choosing whether to render again needs the
    counts, not the 2000 ranges — those stay in the cut-list artifact, which is
    listed here by kind so the agent can fetch it from ``artifacts``.

    ``warnings`` is carried verbatim. It is where the module says the threshold may
    not suit the material, and an agent that drops it will happily render a
    recording whose speech was being cut.
    """
    raw_state = result.get("debreath")
    state = raw_state if isinstance(raw_state, dict) else {}
    raw_plan = state.get("plan")
    plan = raw_plan if isinstance(raw_plan, dict) else {}
    raw_render = state.get("render")
    render = raw_render if isinstance(raw_render, dict) else {}
    return {
        "available": bool(state),
        "status": state.get("status"),
        "stage": state.get("stage"),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "error": state.get("error"),
        "settings": state.get("settings") if isinstance(state.get("settings"), dict) else None,
        "warnings": [_text(item) for item in (state.get("warnings") or []) if _text(item)],
        "cut_count": plan.get("cut_count"),
        "removed_seconds": plan.get("removed_seconds"),
        "removed_percent": plan.get("removed_percent"),
        "kept_seconds": plan.get("kept_seconds"),
        "source_duration_seconds": plan.get("source_duration_seconds"),
        "level_separation": plan.get("level_separation") if isinstance(plan.get("level_separation"), dict) else None,
        "rendered": bool(state.get("rendered")),
        "render_verified": state.get("render_verified"),
        "render_failed_checks": render.get("failed_checks") if isinstance(render.get("failed_checks"), list) else [],
        "cut_list_artifact": DEBREATH_CUT_LIST_KIND if DEBREATH_CUT_LIST_KIND in artifacts else None,
        "media_artifact": DEBREATH_MEDIA_KIND if DEBREATH_MEDIA_KIND in artifacts else None,
        # Subtitles on the cut file's clock. An agent that hands a caption track
        # to the shortened media must use this one; the task's own transcript
        # belongs to the recording before it was shortened.
        "transcript_artifact": DEBREATH_TRANSCRIPT_KIND if DEBREATH_TRANSCRIPT_KIND in artifacts else None,
        "transcript_timeline": state.get("transcript_timeline") if isinstance(state.get("transcript_timeline"), dict) else None,
        "media_filename": _text(state.get("media_filename")) or None,
    }


def _agent_cut_media_note(result: dict[str, Any], artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The note written from the de-breathed file, and what it was written from.

    An agent reading this has to be able to tell three things apart that all
    produce a note: the ordinary transcript note, this one, and this one after
    somebody edited it. So the fields that matter most here are not the text —
    that is in ``note`` — but ``media_source`` and ``subtitle_timeline``, which
    say which file was read and whose clock its timestamps are on, and
    ``promoted``, which says whether the task's note is currently this one.

    ``basis`` is carried verbatim because it is measured from the citations, not
    reported by the model. An agent that treats a ``transcript_only`` note as
    having been written from pictures is making the exact claim this flow refuses
    to make.
    """
    raw_state = result.get("visual_note")
    state = raw_state if isinstance(raw_state, dict) else {}
    media = state.get("media_source") if isinstance(state.get("media_source"), dict) else None
    timeline = state.get("subtitle_timeline") if isinstance(state.get("subtitle_timeline"), dict) else None
    replaced = state.get("replaced_note") if isinstance(state.get("replaced_note"), dict) else {}
    frames_sent = state.get("frames_sent") if isinstance(state.get("frames_sent"), list) else []
    frames_cited = state.get("frames_cited") if isinstance(state.get("frames_cited"), list) else []
    return {
        "available": bool(state),
        "status": state.get("status"),
        "stage": state.get("stage"),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "error": state.get("error"),
        "markdown_chars": len(_text(state.get("markdown"))),
        "basis": state.get("basis"),
        "basis_note": _text(state.get("basis_note")),
        "media_source": media,
        "subtitle_timeline": timeline,
        "frames_sent": len(frames_sent),
        "frames_cited": len(frames_cited),
        "transcript_chars": state.get("transcript_chars"),
        "model": state.get("model"),
        "channel": state.get("channel"),
        # Whether the task's note is this one right now, and whether the note it
        # displaced can still be put back.
        "promoted": bool(state.get("promoted")),
        "summary_written_from": _text(result.get("summary_written_from")) or None,
        "previous_note_restorable": bool(_text(replaced.get("previous_markdown"))),
        "note_artifact": VISUAL_NOTE_KIND if VISUAL_NOTE_KIND in artifacts else None,
    }


def _cut_media_note_next_actions(
    job: dict[str, Any], note: dict[str, Any], debreath: dict[str, Any]
) -> list[dict[str, Any]]:
    """Only when there is a step to take.

    A running note gets a wait; a failed one gets the retry with the recorded
    reason. A finished task that never ran one gets a suggestion *only* once a
    cut file exists, because before that the next step belongs to the de-breath
    and suggesting both would describe two entrances to one flow.
    """
    task_id = job.get("task_id")
    path = f"/agent/v1/tasks/{task_id}/visual-note"
    if note.get("status") == "running":
        return [{
            "action": "wait_cut_media_note",
            "method": "GET",
            "path": f"/agent/v1/tasks/{task_id}/package",
            "reason": "正在根据剪后的文件重写笔记，稍后再次读取任务包。",
        }]
    if note.get("status") == "failed":
        return [{
            "action": "write_cut_media_note",
            "method": "POST",
            "path": path,
            "reason": _text(note.get("error")) or "上次重写笔记失败，可以重新发起。",
        }]
    if not note.get("available") and debreath.get("media_artifact"):
        return [{
            "action": "write_cut_media_note",
            "method": "POST",
            "path": path,
            "reason": "已经有剪后的文件，可以据它重写这个任务的笔记。",
        }]
    return []


def _debreath_next_actions(job: dict[str, Any], debreath: dict[str, Any]) -> list[dict[str, Any]]:
    """Only when there is something to do about it.

    A completed task with no de-breath is not a problem to fix, so it gets no
    action — every finished task would otherwise carry one, and a list where
    everything is suggested says nothing. A run that is still going or that failed
    is different: there the agent has a next step.
    """
    task_id = job.get("task_id")
    if debreath.get("status") == "running":
        return [{
            "action": "wait_debreath",
            "method": "GET",
            "path": f"/agent/v1/tasks/{task_id}/package",
            "reason": "去气口正在进行，稍后再次读取任务包。",
        }]
    if debreath.get("status") == "failed":
        return [{
            "action": "debreath",
            "method": "POST",
            "path": f"/agent/v1/tasks/{task_id}/debreath",
            "reason": _text(debreath.get("error")) or "上次去气口失败，可以重新发起。",
        }]
    return []


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
    debreath = _agent_debreath(result, artifacts)
    cut_media_note = _agent_cut_media_note(result, artifacts)
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
        # The measured consonant-band deficit, plus whether a corrected take
        # exists. Reported so an agent can decide which take to hand a listener.
        # `transcription_take` names the take this transcript came from — the
        # corrected one by default, the plain one when correcting failed — so a
        # re-run can reproduce it instead of silently changing its input.
        "audio": {
            "presence": result.get("voice_presence") if isinstance(result.get("voice_presence"), dict) else None,
            "enhanced_available": bool(artifacts.get("playback_audio_enhanced")),
            "transcription_take": result.get("transcription_audio_take") or "plain",
        },
        "debreath": debreath,
        "cut_media_note": cut_media_note,
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
    package["next_actions"] = (
        _next_actions(job, diagnosis, policy.extra_next_actions)
        + _debreath_next_actions(job, debreath)
        + _cut_media_note_next_actions(job, cut_media_note, debreath)
    )
    return package
