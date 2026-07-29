"""User-facing task detail projection.

This module turns internal job rows, queued job_steps, and result artifacts into
a stable "processing detail" shape for the frontend. It intentionally separates
user-facing timeline language from lower-level queue and model terminology.
"""

from __future__ import annotations

from typing import Any, Protocol

from backend.core.result_schema import canonical_display_segments, canonical_raw_segments
from backend.core.title_display import display_title_for_user

TASK_DETAIL_VERSION = "1"
TASK_SNAPSHOT_VERSION = "1"

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
LIVE_STATUSES = {"queued", "running"}


class TaskDetailPolicy(Protocol):
    def note_generation_diagnosis(
        self,
        job: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]: ...

    def diagnose_error(self, error: Any) -> dict[str, Any]: ...

    def build_decision_log(
        self,
        result: dict[str, Any],
        *,
        job: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]: ...

    def stt_route(
        self,
        result: dict[str, Any],
        metadata: dict[str, Any],
    ) -> str: ...

    def route_snapshot(
        self,
        job: dict[str, Any],
        result: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]: ...

    def next_action_for_failure(
        self,
        job: dict[str, Any],
        failed_step: dict[str, Any] | None,
    ) -> str: ...

    def is_source_retryable(self, job: dict[str, Any]) -> bool: ...

    def additional_stage_step(self, stage: str) -> str | None: ...

    def additional_recorded_step(self, step_type: str) -> str | None: ...


def _text(value: Any) -> str:
    return str(value or "").strip()


def _num(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = _text(value)
        return float(text) if text else None
    except ValueError:
        return None


def _metadata(job: dict[str, Any]) -> dict[str, Any]:
    value = job.get("metadata")
    return value if isinstance(value, dict) else {}


def _result(job: dict[str, Any]) -> dict[str, Any]:
    value = job.get("result")
    return value if isinstance(value, dict) else {}


def _video_source(metadata: dict[str, Any]) -> dict[str, Any]:
    value = metadata.get("video_source")
    return value if isinstance(value, dict) else {}


def _queue_options(metadata: dict[str, Any]) -> dict[str, Any]:
    value = metadata.get("queue_options")
    return value if isinstance(value, dict) else {}


def _title(job: dict[str, Any], result: dict[str, Any], metadata: dict[str, Any]) -> str:
    video_source = _video_source(metadata)
    return (
        _text(result.get("display_title"))
        or _text(metadata.get("display_title"))
        or _text(video_source.get("display_title"))
        or display_title_for_user(
            result.get("filename")
            or metadata.get("raw_title")
            or video_source.get("title")
            or job.get("source_filename"),
            job.get("source_filename") or result.get("filename"),
        )
        or _text(job.get("source_filename"))
        or _text(job.get("task_id"))
        or "未命名任务"
    )


def _source_type(job: dict[str, Any], result: dict[str, Any], metadata: dict[str, Any]) -> str:
    value = _text(job.get("source_type") or result.get("source"))
    if value:
        return value
    if _video_source(metadata):
        return "video_link"
    return "unknown"


def _has_transcript(result: dict[str, Any]) -> bool:
    return bool(
        _text(result.get("transcript_text") or result.get("transcript_text_preview"))
        or canonical_raw_segments(result)
        or canonical_display_segments(result)
    )


def _has_note(result: dict[str, Any]) -> bool:
    return bool(_text(result.get("summary_markdown")))


def _artifact_items(task_id: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, dict):
        return []
    labels = {
        "transcript_txt": "纯文本 TXT",
        "transcript_srt": "字幕 SRT",
        "transcript_vtt": "字幕 VTT",
        "transcript_bilingual_srt": "双语字幕 SRT",
        "transcript_bilingual_vtt": "双语字幕 VTT",
        "summary_md": "Markdown 笔记",
        "playback_audio": "播放音频",
    }
    items: list[dict[str, Any]] = []
    for kind, artifact in artifacts.items():
        if not isinstance(artifact, dict):
            continue
        filename = _text(artifact.get("filename"))
        url = _text(artifact.get("url")) or f"/jobs/{task_id}/artifacts/{kind}"
        items.append({
            "kind": str(kind),
            "label": labels.get(str(kind), str(kind)),
            "filename": filename,
            "url": url,
            "download_url": url,
            "content_type": artifact.get("content_type"),
            "size_bytes": artifact.get("size_bytes"),
        })
    return [{key: value for key, value in item.items() if value not in (None, "")} for item in items]


def _template_for(source_type: str, result: dict[str, Any], metadata: dict[str, Any]) -> list[dict[str, Any]]:
    common_note = {
        "id": "note_generation",
        "title": "AI 笔记生成",
        "pending_detail": "等待转录或字幕整理完成后生成笔记。",
    }
    common_save = {
        "id": "result_save",
        "title": "结果保存",
        "pending_detail": "等待前面步骤完成后保存字幕和笔记产物。",
    }
    export_enabled = bool(
        result.get("lark_response")
        or result.get("lark_error")
        or _text(_queue_options(metadata).get("export_to_lark")).lower() in {"1", "true", "yes", "on"}
    )

    if source_type == "transcript_file":
        steps = [
            {
                "id": "subtitle_parse",
                "title": "字幕解析",
                "pending_detail": "等待读取字幕文件。",
            },
            {
                "id": "subtitle_prepare",
                "title": "字幕整理",
                "pending_detail": "等待整理字幕段落。",
            },
            common_note,
            common_save,
        ]
    else:
        steps = [
            {
                "id": "source_fetch",
                "title": "素材获取与校验",
                "pending_detail": "等待获取或保存素材。",
            },
            {
                "id": "audio_prepare",
                "title": "音频准备",
                "pending_detail": "等待准备可转写音频。",
            },
            {
                "id": "transcription",
                "title": "语音转写",
                "pending_detail": "等待开始转写。",
            },
            {
                "id": "subtitle_prepare",
                "title": "字幕整理",
                "pending_detail": "等待生成可阅读字幕段落。",
            },
            common_note,
            common_save,
        ]
    if export_enabled:
        steps.append({
            "id": "feishu_export",
            "title": "飞书导出",
            "pending_detail": "等待结果保存后导出到飞书。",
        })
    return steps


STAGE_TO_STEP = {
    "resolving": "source_fetch",
    "downloading": "source_fetch",
    "saving": "source_fetch",
    "import": "source_fetch",
    "source_download": "source_fetch",
    "queued": "transcription",
    "audio": "audio_prepare",
    "stt": "transcription",
    "transcript_parse": "subtitle_parse",
    "transcript_cleanup": "subtitle_prepare",
    "speaker_diarization": "subtitle_prepare",
    "translation": "subtitle_prepare",
    "transcript_ready": "subtitle_prepare",
    "note_mode_plan": "note_generation",
    "summary": "note_generation",
    "summary_regenerate": "note_generation",
    "export": "feishu_export",
    "done": "result_save",
    "failed": "source_fetch",
    "recovery": "source_fetch",
}

STEP_ORDER = {
    "source_fetch": 10,
    "subtitle_parse": 10,
    "audio_prepare": 20,
    "transcription": 30,
    "subtitle_prepare": 40,
    "note_generation": 50,
    "result_save": 60,
    "feishu_export": 70,
}

RECORDED_STEP_MAP = {
    "video_source": "source_fetch",
    "transcription": "transcription",
}


def _recorded_by_timeline_step(
    policy: TaskDetailPolicy,
    steps: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for step in steps:
        step_type = _text(step.get("step_type"))
        mapped = RECORDED_STEP_MAP.get(step_type) or policy.additional_recorded_step(step_type)
        if not mapped:
            continue
        result[mapped] = step
    return result


def _recorded_status(value: str) -> str:
    if value == "queued":
        return "pending"
    if value in {"running", "completed", "failed", "cancelled"}:
        return value
    return "pending"


def _stt_route(
    policy: TaskDetailPolicy,
    result: dict[str, Any],
    metadata: dict[str, Any],
) -> str:
    return policy.stt_route(result, metadata)


def _note_mode_label(result: dict[str, Any], metadata: dict[str, Any]) -> str:
    value = _text(
        result.get("resolved_note_mode")
        or result.get("requested_note_mode")
        or _queue_options(metadata).get("note_mode")
        or "auto"
    )
    labels = {
        "auto": "自动选择",
        "direct": "直接生成",
        "high_fidelity": "高保真笔记",
        "chapter_coverage": "章节覆盖笔记",
    }
    return labels.get(value, value)


def _status_for_snapshot(job: dict[str, Any]) -> str:
    value = _text(job.get("status") or job.get("task_state")).lower()
    aliases = {
        "processing": "running",
        "done": "completed",
        "canceled": "cancelled",
    }
    value = aliases.get(value, value)
    if value in {"queued", "running", "completed", "failed", "cancelled"}:
        return value
    stage = _text(job.get("stage")).lower()
    if stage == "queued":
        return "queued"
    if stage and stage not in {"idle", "done"}:
        return "running"
    if _result(job):
        return "completed"
    return "queued"


def _current_step_id(timeline: list[dict[str, Any]]) -> str | None:
    for status in ("failed", "cancelled", "running"):
        match = next((step for step in timeline if step.get("status") == status), None)
        if match:
            return _text(match.get("id")) or None
    pending = next((step for step in timeline if step.get("status") == "pending"), None)
    if pending:
        return _text(pending.get("id")) or None
    if timeline:
        return _text(timeline[-1].get("id")) or None
    return None


def _route_snapshot(
    policy: TaskDetailPolicy,
    job: dict[str, Any],
    result: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return policy.route_snapshot(job, result, metadata)


def _note_payload(
    policy: TaskDetailPolicy,
    job: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    markdown = _text(result.get("summary_markdown"))
    summary_status = _text(result.get("summary_status") or job.get("summary_status"))
    status = "pending"
    if result.get("summary_skipped") or summary_status == "skipped":
        status = "skipped"
    elif markdown:
        status = "completed"
    elif result.get("summary_error") or summary_status == "failed":
        status = "failed"
    elif summary_status:
        status = summary_status
    diagnosis = policy.note_generation_diagnosis(job, result)
    payload = {
        "status": status,
        "markdown_chars": len(markdown),
        "diagnosis": diagnosis,
        "requested_mode": result.get("requested_note_mode"),
        "resolved_mode": result.get("resolved_note_mode"),
        "prompt_preset": result.get("prompt_preset"),
        "prompt_preset_label": result.get("prompt_preset_label"),
    }
    return {key: value for key, value in payload.items() if value not in (None, "")}


def _detail_for(
    policy: TaskDetailPolicy,
    step_id: str,
    status: str,
    job: dict[str, Any],
    result: dict[str, Any],
    metadata: dict[str, Any],
    default_pending: str,
) -> str:
    progress_meta = metadata.get("video_source_progress") if isinstance(metadata.get("video_source_progress"), dict) else {}
    if status == "pending":
        return default_pending
    if status == "cancelled":
        return "任务已取消。"
    if step_id == "source_fetch":
        if progress_meta.get("message") and status == "running":
            loaded = _num(progress_meta.get("loaded_bytes"))
            total = _num(progress_meta.get("total_bytes"))
            suffix = f"（{round(loaded / 1024 / 1024, 1)}MB / {round(total / 1024 / 1024, 1)}MB）" if loaded and total else ""
            return f"{progress_meta.get('message')}{suffix}"
        video_source = _video_source(metadata)
        provider = _text(video_source.get("provider"))
        if provider:
            return f"已获取视频内容，来源：{provider}。"
        return "素材已保存，后续可以继续处理。"
    if step_id == "subtitle_parse":
        return "已读取字幕文件并转换为可处理文本。" if status == "completed" else "正在读取字幕文件。"
    if step_id == "audio_prepare":
        return "已准备可转写音频。" if status == "completed" else "正在准备可转写音频。"
    if step_id == "transcription":
        route = _stt_route(policy, result, metadata)
        if status == "running":
            return route or "正在转写音频。"
        return route or "已完成语音转写。"
    if step_id == "subtitle_prepare":
        mode = _text(result.get("subtitle_mode"))
        translation = _text(result.get("translation_status"))
        if translation == "completed" or mode == "bilingual_zh":
            return "已生成中文字幕对照。"
        if _has_transcript(result):
            return "已生成可阅读字幕段落。"
        return "正在整理字幕段落。"
    if step_id == "note_generation":
        if result.get("summary_skipped") or result.get("summary_status") == "skipped":
            return "本次开启了仅转录模式，已跳过 AI 笔记。"
        if status == "failed":
            return _text(result.get("summary_error") or job.get("error_reason")) or "AI 笔记生成失败。"
        if _has_note(result):
            return f"已生成{_note_mode_label(result, metadata)}。"
        return f"正在生成{_note_mode_label(result, metadata)}。"
    if step_id == "result_save":
        count = len(_artifact_items(_text(job.get("task_id")), result))
        if count:
            return f"已保存 {count} 个结果产物。"
        return "结果已保存。"
    if step_id == "feishu_export":
        if result.get("lark_response"):
            return "已同步至飞书文档。"
        if result.get("lark_error"):
            return _text(result.get("lark_error")) or "飞书导出失败。"
        return "等待导出至飞书。"
    return default_pending


def _apply_result_overrides(
    policy: TaskDetailPolicy,
    statuses: dict[str, str],
    job: dict[str, Any],
    result: dict[str, Any],
) -> None:
    if _has_transcript(result):
        for step_id in ("audio_prepare", "transcription", "subtitle_prepare", "subtitle_parse"):
            if step_id in statuses and statuses[step_id] not in {"failed", "cancelled"}:
                statuses[step_id] = "completed"
    if result.get("summary_skipped") or result.get("summary_status") == "skipped":
        if "note_generation" in statuses:
            statuses["note_generation"] = "skipped"
    elif _has_note(result):
        statuses["note_generation"] = "completed"
    elif result.get("summary_error") or result.get("summary_status") == "failed":
        statuses["note_generation"] = "failed"
    if result.get("artifacts"):
        statuses["result_save"] = "completed"
    if result.get("lark_response"):
        statuses["feishu_export"] = "completed"
    elif result.get("lark_error"):
        statuses["feishu_export"] = "failed"
    if job.get("status") == "cancelled":
        stage = _text(job.get("stage"))
        active = STAGE_TO_STEP.get(stage) or policy.additional_stage_step(stage) or "source_fetch"
        statuses[active] = "cancelled"


def _timeline(
    policy: TaskDetailPolicy,
    job: dict[str, Any],
    recorded_steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result = _result(job)
    metadata = _metadata(job)
    source_type = _source_type(job, result, metadata)
    template = _template_for(source_type, result, metadata)
    step_ids = [item["id"] for item in template]
    job_stage = _text(job.get("stage"))
    active_step = STAGE_TO_STEP.get(job_stage) or policy.additional_stage_step(job_stage) or step_ids[0]
    active_order = STEP_ORDER.get(active_step, 0)
    job_status = _text(job.get("status"))
    recorded = _recorded_by_timeline_step(policy, recorded_steps)

    statuses: dict[str, str] = {}
    sources: dict[str, str] = {}
    for item in template:
        step_id = item["id"]
        if step_id in recorded:
            statuses[step_id] = _recorded_status(_text(recorded[step_id].get("status")))
            sources[step_id] = "recorded"
            continue
        if job_status == "completed":
            statuses[step_id] = "completed"
        elif job_status == "failed":
            order = STEP_ORDER.get(step_id, 0)
            statuses[step_id] = "failed" if step_id == active_step else ("completed" if order < active_order else "pending")
        elif job_status == "cancelled":
            order = STEP_ORDER.get(step_id, 0)
            statuses[step_id] = "cancelled" if step_id == active_step else ("completed" if order < active_order else "pending")
        elif job_status == "queued":
            statuses[step_id] = "completed" if step_id in {"source_fetch", "subtitle_parse"} else "pending"
        elif job_status == "running":
            order = STEP_ORDER.get(step_id, 0)
            statuses[step_id] = "running" if step_id == active_step else ("completed" if order < active_order else "pending")
        else:
            statuses[step_id] = "pending"
        sources[step_id] = "inferred"

    _apply_result_overrides(policy, statuses, job, result)

    timeline = []
    for item in template:
        step_id = item["id"]
        status = statuses.get(step_id, "pending")
        recorded_step = recorded.get(step_id) or {}
        detail = _detail_for(
            policy,
            step_id,
            status,
            job,
            result,
            metadata,
            item.get("pending_detail") or "",
        )
        error_reason = (
            _text(recorded_step.get("error_reason"))
            or (_text(result.get("summary_error")) if step_id == "note_generation" and status == "failed" else "")
            or (_text(result.get("lark_error")) if step_id == "feishu_export" and status == "failed" else "")
            or (_text(job.get("error_reason")) if status == "failed" and step_id == active_step else "")
        )
        timeline.append({
            "id": step_id,
            "title": item["title"],
            "status": status,
            "detail": detail,
            "source": sources.get(step_id, "inferred"),
            "progress": _num(job.get("progress")) if step_id == active_step and status == "running" else None,
            "started_at": recorded_step.get("started_at"),
            "finished_at": recorded_step.get("finished_at"),
            "error_reason": error_reason or None,
        })
    return [{key: value for key, value in item.items() if value is not None and value != ""} for item in timeline]


def _diagnosis(
    policy: TaskDetailPolicy,
    job: dict[str, Any],
    timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    result = _result(job)
    job_status = _text(job.get("status"))
    note_diag = policy.note_generation_diagnosis(job, result)
    if job_status == "failed":
        failed_step = next((step for step in timeline if step.get("status") == "failed"), None)
        error_diag = policy.diagnose_error(
            job.get("error_reason") or (failed_step or {}).get("error_reason")
        )
        return {
            "visible": True,
            "severity": "error",
            "title": error_diag.get("title") or (failed_step.get("title") + "失败" if failed_step else "任务处理失败"),
            "detail": error_diag.get("detail") or _text(job.get("error_reason") or failed_step.get("error_reason")) or "任务失败，但没有记录更具体的原因。",
            "next_action": error_diag.get("next_action")
            or policy.next_action_for_failure(job, failed_step),
            "code": error_diag.get("code"),
            "step_id": failed_step.get("id") if failed_step else None,
        }
    if note_diag.get("status") == "failed":
        return {
            "visible": True,
            "severity": note_diag.get("severity") or "error",
            "title": note_diag.get("title"),
            "detail": note_diag.get("detail"),
            "next_action": note_diag.get("next_action"),
            "step_id": "note_generation",
        }
    if job_status == "cancelled":
        return {
            "visible": True,
            "severity": "neutral",
            "title": "任务已取消",
            "detail": "这条任务已被取消，未生成完整结果。",
            "next_action": "可以删除这条记录，或回到开始页重新提交。",
            "step_id": next((step.get("id") for step in timeline if step.get("status") == "cancelled"), None),
        }
    return {"visible": False}


def _actions(
    policy: TaskDetailPolicy,
    job: dict[str, Any],
    result: dict[str, Any],
    artifacts: list[dict[str, Any]],
    diagnosis: dict[str, Any],
) -> list[dict[str, Any]]:
    task_id = _text(job.get("task_id"))
    status = _text(job.get("status"))
    actions: list[dict[str, Any]] = []
    if status in LIVE_STATUSES:
        actions.append({
            "id": "cancel",
            "label": "取消任务",
            "method": "POST",
            "path": f"/jobs/{task_id}/cancel",
            "enabled": True,
            "tone": "danger",
        })
    if _has_transcript(result) or status == "completed":
        actions.append({
            "id": "open_result",
            "label": "打开结果",
            "method": "GET",
            "path": f"/jobs/{task_id}",
            "enabled": True,
            "tone": "primary",
        })
    if artifacts:
        actions.append({
            "id": "download_outputs",
            "label": "下载产物",
            "method": "GET",
            "path": f"/jobs/{task_id}/artifacts/{{kind}}",
            "enabled": True,
            "tone": "secondary",
        })
    if diagnosis.get("step_id") == "note_generation" and _has_transcript(result):
        actions.append({
            "id": "regenerate_note",
            "label": "重生笔记",
            "method": "POST",
            "path": f"/agent/v1/tasks/{task_id}/note/regenerate",
            "enabled": True,
            "tone": "secondary",
        })
    if status in TERMINAL_STATUSES:
        actions.append({
            "id": "delete",
            "label": "删除记录",
            "method": "DELETE",
            "path": f"/jobs/{task_id}",
            "enabled": True,
            "tone": "danger",
        })
    if status == "failed" and policy.is_source_retryable(job):
        actions.append({
            "id": "retry",
            "label": "重新处理",
            "method": "POST",
            "path": f"/jobs/{task_id}/retry",
            "enabled": True,
            "tone": "secondary",
        })
    elif status == "failed":
        actions.append({
            "id": "resubmit",
            "label": "重新提交",
            "method": "NAVIGATE",
            "path": "/",
            "enabled": True,
            "tone": "secondary",
        })
    return actions


def build_task_snapshot(
    job: dict[str, Any],
    *,
    policy: TaskDetailPolicy,
    job_steps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    job_steps = job_steps or []
    result = _result(job)
    metadata = _metadata(job)
    task_id = _text(job.get("task_id"))
    artifacts = _artifact_items(task_id, result)
    timeline = _timeline(policy, job, job_steps)
    diagnosis = _diagnosis(policy, job, timeline)
    current_step = _current_step_id(timeline)
    failure_reason = ""
    next_action = ""
    if diagnosis.get("visible") and diagnosis.get("severity") in {"error", "warning"}:
        failure_reason = _text(diagnosis.get("detail"))
        next_action = _text(diagnosis.get("next_action"))
    snapshot = {
        "task_snapshot_version": TASK_SNAPSHOT_VERSION,
        "task_id": task_id,
        "overall_status": _status_for_snapshot(job),
        "current_step": current_step,
        "progress": _num(job.get("progress")),
        "steps": timeline,
        "step_statuses": {
            _text(step.get("id")): step.get("status")
            for step in timeline
            if _text(step.get("id")) and step.get("status")
        },
        "failure_reason": failure_reason or None,
        "next_action": next_action or None,
        "artifacts": artifacts,
        "route": _route_snapshot(policy, job, result, metadata),
        "actions": _actions(policy, job, result, artifacts, diagnosis),
        "data_quality": {
            "has_recorded_steps": bool(job_steps),
            "timeline_sources": sorted({step.get("source") for step in timeline if step.get("source")}),
        },
    }
    return {key: value for key, value in snapshot.items() if value not in (None, "", [])}


def build_task_detail(
    job: dict[str, Any],
    *,
    policy: TaskDetailPolicy,
    job_steps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    job_steps = job_steps or []
    result = _result(job)
    metadata = _metadata(job)
    task_id = _text(job.get("task_id"))
    artifacts = _artifact_items(task_id, result)
    timeline = _timeline(policy, job, job_steps)
    diagnosis = _diagnosis(policy, job, timeline)
    decision_log = policy.build_decision_log(result, job=job, metadata=metadata)
    source_type = _source_type(job, result, metadata)
    task_snapshot = build_task_snapshot(job, policy=policy, job_steps=job_steps)
    task = {
        "task_id": task_id,
        "status": job.get("status"),
        "stage": job.get("stage"),
        "progress": job.get("progress"),
        "source_type": source_type,
        "title": _title(job, result, metadata),
        "filename": result.get("filename") or job.get("source_filename"),
        "file_size_mb": job.get("source_file_size_mb"),
        "duration_seconds": result.get("audio_duration_seconds") or job.get("source_duration_seconds"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }
    return {
        "ok": True,
        "task_detail_version": TASK_DETAIL_VERSION,
        "task_snapshot": task_snapshot,
        "task": {key: value for key, value in task.items() if value is not None and value != ""},
        "note": _note_payload(policy, job, result),
        "timeline": timeline,
        "decision_log": decision_log,
        "diagnosis": {key: value for key, value in diagnosis.items() if value is not None and value != ""},
        "actions": _actions(policy, job, result, artifacts, diagnosis),
        "artifacts": artifacts,
        "chapter_coverage": result.get("chapter_coverage") if isinstance(result.get("chapter_coverage"), dict) else None,
        "recorded_steps": job_steps,
        "data_quality": {
            "has_recorded_steps": bool(job_steps),
            "timeline_sources": sorted({step.get("source") for step in timeline if step.get("source")}),
        },
    }
