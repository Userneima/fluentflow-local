"""Edition-neutral Processing Plan builder."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


ExecutionResolver = Callable[..., tuple[str, str]]

PROCESSING_PLAN_VERSION = "1"
SUPPORTED_GOALS = {"course_notes", "learning_notes", "lecture_notes"}

PLANNER_MATERIAL_TYPE_MAP = {
    "course": "course_material",
    "interview": "interview_material",
    "career_talk": "sharing_session_material",
    "meeting": "meeting_material",
    "research": "research_material",
    "competition_brief": "briefing_material",
    "product_training": "training_material",
    "other": "learning_material",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _source_type(result: dict[str, Any], job: dict[str, Any] | None) -> str:
    return _text(result.get("source") or (job or {}).get("source_type") or "unknown")


def _filename(result: dict[str, Any], job: dict[str, Any] | None) -> str:
    return _text(result.get("display_title") or result.get("filename") or (job or {}).get("source_filename"))


def _duration_seconds(result: dict[str, Any], job: dict[str, Any] | None) -> float | None:
    return _number(result.get("audio_duration_seconds") or (job or {}).get("source_duration_seconds"))


def _transcript_text(result: dict[str, Any]) -> str:
    return _text(result.get("transcript_text") or result.get("transcript_text_preview")).lower()


def _has_transcript(result: dict[str, Any]) -> bool:
    if _transcript_text(result):
        return True
    for key in ("raw_segments", "display_segments", "segments"):
        value = result.get(key)
        if isinstance(value, list) and value:
            return True
    return False


def _content_learning_signals(transcript: str) -> list[str]:
    if not transcript:
        return []
    checks = [
        ("content mentions course/class", ("课程", "这节课", "本节课", "课堂", "course", "lecture")),
        ("content mentions explanation", ("讲解", "解释", "概念", "原理", "案例", "example", "concept")),
        ("content has structured learning markers", ("第一", "第二", "第三", "首先", "然后", "最后", "part one", "first", "second")),
    ]
    signals = []
    for label, tokens in checks:
        if any(token in transcript for token in tokens):
            signals.append(label)
    return signals


def _course_or_lecture_signals(text: str) -> list[str]:
    if not text:
        return []
    checks = [
        ("content explicitly mentions course/class", ("课程", "这节课", "本节课", "课堂", "course", "lesson")),
        ("content explicitly mentions lecture", ("讲座", "lecture")),
    ]
    signals = []
    for label, tokens in checks:
        if any(token in text for token in tokens):
            signals.append(label)
    return signals


def _sharing_or_discussion_signals(text: str, *, filename_only: bool = False) -> list[str]:
    if not text:
        return []
    checks = [
        ("content mentions sharing/discussion", ("分享会", "经验分享", "交流会", "集会", "圆桌", "答疑", "q&a", "discussion", "roundtable")),
        ("content mentions meeting/session", ("会议", "例会", "session", "meeting")),
    ]
    if not filename_only:
        checks.extend([
            ("content mentions discussion/review", ("交流", "讨论", "复盘")),
            ("content mentions interview", ("访谈", "interview")),
        ])
    signals = []
    for label, tokens in checks:
        if any(token in text for token in tokens):
            signals.append(label)
    return signals


def _planned_material_type(result: dict[str, Any]) -> tuple[str, str] | None:
    planned = _text(result.get("note_mode_plan_material_type") or result.get("material_type")).lower()
    mapped = PLANNER_MATERIAL_TYPE_MAP.get(planned)
    if not mapped:
        return None
    confidence = _text(result.get("note_mode_plan_confidence")).lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "medium"
    return mapped, confidence


def _material_type(
    source_type: str,
    filename: str,
    duration_seconds: float | None,
    result: dict[str, Any],
) -> tuple[str, str, list[str]]:
    name = filename.lower()
    evidence: list[str] = []
    transcript = _transcript_text(result)
    planned = _planned_material_type(result)
    if planned:
        material, confidence = planned
        evidence.append(f"note planner material_type={_text(result.get('note_mode_plan_material_type') or result.get('material_type')).lower()}")
        return material, confidence, evidence
    if source_type == "transcript_file":
        evidence.append("source_type=transcript_file")
        return "course_transcript_file", "medium", evidence
    sharing_signals = [
        *_sharing_or_discussion_signals(name, filename_only=True),
        *_sharing_or_discussion_signals(transcript),
    ]
    if sharing_signals:
        evidence.extend(sharing_signals)
        return "sharing_session_material", "medium", evidence
    explicit_course_signals = _course_or_lecture_signals(transcript)
    if explicit_course_signals:
        evidence.extend(explicit_course_signals)
        if duration_seconds and duration_seconds >= 1800:
            evidence.append("duration>=30min")
            return "lecture_material", "high", evidence
        return "course_material", "high", evidence
    content_signals = _content_learning_signals(transcript)
    if content_signals:
        evidence.extend(content_signals)
        if duration_seconds and duration_seconds >= 1800:
            evidence.append("duration>=30min")
        return "learning_material", "medium", evidence
    if source_type in {"video_link", "douyin", "youtube"}:
        evidence.append(f"source_type={source_type}")
        if duration_seconds and duration_seconds >= 1800:
            evidence.append("duration>=30min")
            return "lecture_video_pending_content", "medium", evidence
        return "course_video_pending_content", "medium", evidence
    if any(token in name for token in ("course", "lecture", "lesson", "课程", "讲座", "课堂", "课")):
        evidence.append("weak filename hint: course_or_lecture")
        return "course_or_lecture_pending_content", "low", evidence
    if source_type in {"video", "audio"}:
        evidence.append(f"source_type={source_type}")
        return "learning_material_pending_content", "medium", evidence
    return "course_or_lecture_pending_content", "low", evidence or ["fallback route"]


def _learning_goal(material_type: str, source_type: str) -> tuple[str, str]:
    if material_type in {"lecture_material", "lecture_video_pending_content"}:
        return "lecture_notes", "按讲座材料处理，优先保留主题结构、论证线索和可复用观点。"
    if source_type == "transcript_file":
        return "course_notes", "已有字幕/转录，整理为可复用课程笔记。"
    if material_type in {"course_material", "course_transcript_file", "course_video_pending_content"}:
        return "course_notes", "按课程材料处理，整理成可复习、可回看的一份笔记。"
    return "learning_notes", "按长视频学习材料处理，整理成可复习、可回看的一份笔记。"


def note_strategy_from_result(result: dict[str, Any]) -> dict[str, Any]:
    requested = _text(result.get("requested_note_mode"))
    selected = _text(result.get("note_mode_plan_selected_mode"))
    resolved = _text(result.get("resolved_note_mode"))
    strategy = {
        "requested_mode": requested or None,
        "selected_mode": selected or resolved or None,
        "resolved_mode": resolved or selected or requested or None,
        "reason": _text(result.get("note_mode_plan_reason")) or None,
        "confidence": _text(result.get("note_mode_plan_confidence")) or None,
        "material_type": _text(result.get("note_mode_plan_material_type")) or None,
        "warnings": result.get("note_mode_plan_warnings") if isinstance(result.get("note_mode_plan_warnings"), list) else [],
        "fallback": result.get("note_mode_plan_fallback") if result.get("note_mode_plan_fallback") is not None else None,
        "error": _text(result.get("note_mode_plan_error")) or None,
        "planner_provider": _text(result.get("note_mode_plan_provider")) or None,
        "planner_model": _text(result.get("note_mode_plan_model")) or None,
    }
    return {key: value for key, value in strategy.items() if value not in (None, "", [])}


def build_processing_plan(
    result: dict[str, Any] | None,
    *,
    job: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    resolve_execution: ExecutionResolver,
) -> dict[str, Any]:
    result = result if isinstance(result, dict) else {}
    job = job if isinstance(job, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    source_type = _source_type(result, job)
    filename = _filename(result, job)
    duration = _duration_seconds(result, job)
    has_transcript = _has_transcript(result)
    planning_stage = "completed" if has_transcript else "initial"
    material_type, material_confidence, evidence = _material_type(source_type, filename, duration, result)
    goal, goal_reason = _learning_goal(material_type, source_type)
    scope, tool = resolve_execution(result, job, metadata)
    summary_skipped = bool(result.get("summary_skipped") or result.get("summary_status") == "skipped")
    translation_status = _text(result.get("translation_status"))
    note_strategy = note_strategy_from_result(result)

    steps = [
        {
            "id": "ingest",
            "label": "获取材料",
            "tool": "video_link_resolver" if source_type == "video_link" else ("transcript_parser" if source_type == "transcript_file" else "media_upload"),
            "reason": "把输入材料转换为后续可处理的任务来源。",
        },
        {
            "id": "transcribe",
            "label": "生成或读取转录",
            "tool": tool,
            "reason": "先得到稳定转录，再进入字幕、笔记和导出。",
        },
        {
            "id": "prepare_subtitles",
            "label": "整理字幕",
            "tool": "segment_normalizer",
            "reason": "保留原始可编辑字幕，并生成阅读/导出字幕。",
        },
    ]
    if not summary_skipped:
        steps.append({
            "id": "generate_note",
            "label": "生成学习笔记",
            "tool": "ai_note_generator",
            "note_mode": note_strategy.get("resolved_mode") or note_strategy.get("selected_mode") or note_strategy.get("requested_mode") or "auto",
            "reason": note_strategy.get("reason") or "根据转录长度和任务设置选择笔记生成路线。",
        })
    steps.append({
        "id": "export",
        "label": "保存和导出产物",
        "tool": "artifact_writer",
        "optional": True,
        "reason": "把转录、字幕和笔记保存为可下载产物；飞书导出按设置触发。",
    })

    expected_outputs = ["transcript", "source_subtitles", "artifacts"]
    if translation_status == "completed" or result.get("subtitle_mode") == "bilingual_zh":
        expected_outputs.append("bilingual_subtitles")
    if not summary_skipped:
        expected_outputs.append("markdown_note")

    risk_notes = []
    if material_confidence == "low":
        risk_notes.append("材料类型只基于来源/文件名/时长判断，文件名仅作为弱信号。")
    if planning_stage == "initial":
        risk_notes.append("当前是任务创建时的初始计划，转录完成后会基于正文补全判断。")
    if scope == "unknown":
        risk_notes.append("执行通道无法从当前结果确定，前端应按任务来源继续展示。")
    if note_strategy.get("fallback"):
        risk_notes.append("笔记策略规划曾降级，最终模式可能来自长度规则。")

    return {
        "processing_plan_version": PROCESSING_PLAN_VERSION,
        "generated_by": "deterministic_runtime_plan",
        "planning_stage": planning_stage,
        "execution_mode": "automatic",
        "requires_user_confirmation": False,
        "goal": {
            "primary": goal,
            "reason": goal_reason,
            "supported_goals": sorted(SUPPORTED_GOALS),
        },
        "material": {
            "type": material_type,
            "confidence": material_confidence,
            "source_type": source_type,
            "filename": filename or None,
            "duration_seconds": duration,
            "language": result.get("source_language") or result.get("detected_language"),
            "evidence": evidence,
            "evidence_policy": {
                "transcript_content": "primary" if has_transcript else "pending",
                "filename": "weak",
            },
        },
        "execution": {
            "scope": scope,
            "transcription_tool": tool,
        },
        "steps": steps,
        "note_strategy": note_strategy,
        "expected_outputs": expected_outputs,
        "risk_notes": risk_notes,
    }


def ensure_processing_plan(
    result: dict[str, Any] | None,
    *,
    job: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    resolve_execution: ExecutionResolver,
) -> dict[str, Any]:
    if not isinstance(result, dict):
        return result
    existing = result.get("processing_plan") if isinstance(result.get("processing_plan"), dict) else {}
    generated = build_processing_plan(
        result,
        job=job,
        metadata=metadata,
        resolve_execution=resolve_execution,
    )
    note_strategy = {
        **dict(existing.get("note_strategy") or {}),
        **generated.get("note_strategy", {}),
    }
    next_plan = {
        **existing,
        **generated,
        "processing_plan_version": PROCESSING_PLAN_VERSION,
        "note_strategy": note_strategy,
    }
    return {**result, "processing_plan": next_plan}
