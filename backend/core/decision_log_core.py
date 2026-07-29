"""Edition-neutral decision log builder for Agent workflow explanations.

The entries here are not model chain-of-thought. They are concise, user-facing
records of what the system decided, what evidence it used, and what changed
because of that decision.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

DECISION_LOG_VERSION = "1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _metadata(job: dict[str, Any] | None) -> dict[str, Any]:
    value = (job or {}).get("metadata")
    return value if isinstance(value, dict) else {}


def _queue_options(metadata: dict[str, Any]) -> dict[str, Any]:
    value = metadata.get("queue_options")
    return value if isinstance(value, dict) else {}


def _material_type_label(value: str) -> str:
    labels = {
        "course_transcript_file": "课程字幕文件",
        "course_material": "课程材料",
        "lecture_material": "讲座材料",
        "sharing_session_material": "分享讨论材料",
        "interview_material": "访谈材料",
        "meeting_material": "会议材料",
        "research_material": "研究材料",
        "briefing_material": "资料解读材料",
        "training_material": "培训材料",
        "learning_material": "学习材料",
        "course_video_pending_content": "待转录课程视频",
        "lecture_video_pending_content": "待转录讲座视频",
        "learning_material_pending_content": "待判断学习材料",
        "course_or_lecture_pending_content": "待判断学习材料",
    }
    return labels.get(value, value or "未知材料")


def _note_mode_label(value: str) -> str:
    labels = {
        "auto": "自动选择",
        "direct": "直接生成",
        "high_fidelity": "高保真笔记",
        "chapter_coverage": "章节覆盖笔记",
    }
    return labels.get(value, value or "未记录")


def _source_label(source_type: str) -> str:
    labels = {
        "video_link": "视频链接",
        "transcript_file": "字幕文件",
        "video": "视频文件",
        "audio": "音频文件",
    }
    return labels.get(source_type, source_type or "未知来源")


def _duration_evidence(seconds: Any) -> str | None:
    value = _number(seconds)
    if value is None or value <= 0:
        return None
    minutes = round(value / 60)
    if minutes < 1:
        return f"时长约 {round(value)} 秒"
    if minutes < 60:
        return f"时长约 {minutes} 分钟"
    return f"时长约 {minutes // 60} 小时 {minutes % 60} 分钟"


def _language_label(value: Any) -> str | None:
    language = _text(value).lower()
    if not language:
        return None
    if language.startswith("zh") or language.startswith("cmn"):
        return "语言判断：中文"
    if language.startswith("en"):
        return "语言判断：英文"
    return f"语言判断：{value}"


def _clean_list(items: list[Any], limit: int = 5) -> list[str]:
    result = []
    for item in items:
        text = _text(item)
        if text:
            result.append(text[:240])
        if len(result) >= limit:
            break
    return result


def _entry(
    *,
    decision_id: str,
    title: str,
    status: str,
    decision: str,
    reason: str,
    evidence: list[Any],
    impact: str,
    source: str,
    stage: str,
    confidence: str | None = None,
    warnings: list[Any] | None = None,
) -> dict[str, Any]:
    item = {
        "id": decision_id,
        "title": title,
        "status": status,
        "decision": decision,
        "reason": reason,
        "evidence": _clean_list(evidence),
        "impact": impact,
        "source": source,
        "stage": stage,
        "confidence": confidence,
        "warnings": _clean_list(warnings or []),
    }
    return {key: value for key, value in item.items() if value not in (None, "", [])}


def _note_status(result: dict[str, Any], job: dict[str, Any]) -> tuple[str, str, str, str]:
    summary = _text(result.get("summary_markdown"))
    status = _text(result.get("summary_status") or job.get("summary_status")).lower()
    error = _text(result.get("summary_error") or job.get("error_reason"))
    if summary:
        return "completed", "笔记已生成", "当前结果包含可用的 AI 笔记。", "打开结果页复查或导出。"
    if result.get("summary_skipped") or status == "skipped":
        return "skipped", "跳过 AI 笔记", "本次开启了仅转录模式。", "保留字幕和转录，需要笔记时可重新生成。"
    if status == "failed" or error:
        return "failed", "笔记生成失败", error or "AI 笔记生成失败。", "保留已完成的转录，用户可以重新生成笔记。"
    if status == "pending" or job.get("stage") == "summary":
        return "running", "正在生成笔记", "转录已经可用，正在等待 AI 返回笔记。", "等待笔记产物生成。"
    return "pending", "等待笔记生成", "需要先完成转录或字幕解析。", "后续步骤完成后再生成笔记。"


def build_decision_log(
    result: dict[str, Any] | None,
    *,
    job: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    build_processing_plan: Callable[..., dict[str, Any]],
    execution_route_label: Callable[[str], str],
    execution_route_reason: str,
) -> dict[str, Any]:
    result = result if isinstance(result, dict) else {}
    job = job if isinstance(job, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else _metadata(job)
    queue_options = _queue_options(metadata)
    plan = result.get("processing_plan") if isinstance(result.get("processing_plan"), dict) else None
    if not plan:
        plan = build_processing_plan(result, job=job, metadata=metadata)

    material = plan.get("material") if isinstance(plan.get("material"), dict) else {}
    execution = plan.get("execution") if isinstance(plan.get("execution"), dict) else {}
    note_strategy = plan.get("note_strategy") if isinstance(plan.get("note_strategy"), dict) else {}
    goal = plan.get("goal") if isinstance(plan.get("goal"), dict) else {}
    source_type = _text(material.get("source_type") or result.get("source") or job.get("source_type"))
    entries: list[dict[str, Any]] = []

    material_type = _text(material.get("type"))
    entries.append(_entry(
        decision_id="material_classification",
        title="判断材料类型",
        status="completed" if material_type else "pending",
        decision=_material_type_label(material_type),
        reason=_text(goal.get("reason")) or "根据来源、时长和正文线索判断材料用途。",
        evidence=[
            f"来源：{_source_label(source_type)}",
            _duration_evidence(material.get("duration_seconds")),
            _language_label(material.get("language")),
            *list(material.get("evidence") if isinstance(material.get("evidence"), list) else []),
        ],
        impact=_text(goal.get("reason")) or "后续按学习材料生成转录、字幕和笔记。",
        source="inferred" if plan.get("generated_by") == "deterministic_runtime_plan" else "recorded",
        stage="planning",
        confidence=_text(material.get("confidence")) or None,
    ))

    scope = _text(execution.get("scope"))
    tool = _text(execution.get("transcription_tool"))
    route_label = execution_route_label(scope)
    entries.append(_entry(
        decision_id="execution_route",
        title="选择处理路线",
        status="completed" if scope and scope != "unknown" else "pending",
        decision=route_label,
        reason=execution_route_reason,
        evidence=[
            f"转写工具：{tool}" if tool else None,
            f"设置中的转写引擎：{queue_options.get('stt_provider')}" if queue_options.get("stt_provider") else None,
            f"结果记录的转写引擎：{result.get('stt_provider')}" if result.get("stt_provider") else None,
            f"来源：{_source_label(source_type)}",
        ],
        impact="这会影响处理速度、隐私边界和失败后的重试路径。",
        source="recorded" if result.get("stt_provider") or queue_options.get("stt_provider") else "inferred",
        stage="route",
    ))

    subtitle_mode = _text(result.get("subtitle_mode"))
    translation_status = _text(result.get("translation_status"))
    subtitle_decision = "生成源语言字幕"
    subtitle_status = "pending"
    subtitle_reason = "等待转录或字幕解析完成后生成可阅读字幕。"
    subtitle_impact = "后续笔记和导出会使用整理后的字幕段落。"
    if translation_status == "completed" or subtitle_mode == "bilingual_zh":
        subtitle_decision = "生成中文对照字幕"
        subtitle_status = "completed"
        subtitle_reason = "已完成中文翻译对照。"
        subtitle_impact = "编辑器和导出产物可以显示双语字幕。"
    elif result.get("transcript_text") or result.get("raw_segments") or result.get("display_segments"):
        subtitle_status = "completed"
        subtitle_reason = "已得到可用于阅读和编辑的字幕段落。"
    entries.append(_entry(
        decision_id="subtitle_strategy",
        title="决定字幕呈现方式",
        status=subtitle_status,
        decision=subtitle_decision,
        reason=subtitle_reason,
        evidence=[
            f"字幕模式：{subtitle_mode}" if subtitle_mode else None,
            f"翻译状态：{translation_status}" if translation_status else None,
            _language_label(result.get("source_language") or result.get("detected_language")),
        ],
        impact=subtitle_impact,
        source="recorded" if subtitle_mode or translation_status else "inferred",
        stage="subtitle",
    ))

    requested = _text(result.get("requested_note_mode") or note_strategy.get("requested_mode") or queue_options.get("note_mode") or "auto")
    selected = _text(result.get("note_mode_plan_selected_mode") or note_strategy.get("selected_mode"))
    resolved = _text(result.get("resolved_note_mode") or note_strategy.get("resolved_mode") or selected or requested)
    planner_recorded = bool(
        result.get("note_mode_plan_reason")
        or note_strategy.get("reason")
        or result.get("note_mode_plan_error")
        or result.get("note_mode_plan_selected_mode")
    )
    entries.append(_entry(
        decision_id="note_mode_selection",
        title="选择笔记生成方式",
        status="completed" if resolved else "pending",
        decision=_note_mode_label(resolved),
        reason=(
            _text(result.get("note_mode_plan_reason") or note_strategy.get("reason"))
            or ("用户指定了固定笔记模式。" if requested != "auto" else "按当前自动模式和材料信息选择笔记生成方式。")
        ),
        evidence=[
            f"用户请求：{_note_mode_label(requested)}",
            f"AI 规划选择：{_note_mode_label(selected)}" if selected else None,
            _duration_evidence(result.get("audio_duration_seconds") or job.get("source_duration_seconds") or material.get("duration_seconds")),
            f"转录长度：{result.get('note_mode_transcript_length')} 字" if result.get("note_mode_transcript_length") else None,
            f"规划模型：{result.get('note_mode_plan_provider')} / {result.get('note_mode_plan_model')}" if result.get("note_mode_plan_provider") else None,
        ],
        impact="不同模式会影响生成速度、结构完整度和证据覆盖。",
        source="recorded" if planner_recorded else "inferred",
        stage="note",
        confidence=_text(result.get("note_mode_plan_confidence") or note_strategy.get("confidence")) or None,
        warnings=result.get("note_mode_plan_warnings") or note_strategy.get("warnings") or [],
    ))

    note_status, note_decision, note_reason, note_impact = _note_status(result, job)
    entries.append(_entry(
        decision_id="note_generation_outcome",
        title="判断笔记生成结果",
        status=note_status,
        decision=note_decision,
        reason=note_reason,
        evidence=[
            f"摘要状态：{result.get('summary_status') or job.get('summary_status')}" if result.get("summary_status") or job.get("summary_status") else None,
            f"笔记字数：{len(_text(result.get('summary_markdown')))}" if result.get("summary_markdown") else None,
            f"证据数量：{result.get('note_mode_evidence_count')}" if result.get("note_mode_evidence_count") else None,
            f"重点覆盖：{result.get('note_mode_covered_important_evidence_count')}/{result.get('note_mode_important_evidence_count')}" if result.get("note_mode_important_evidence_count") else None,
        ],
        impact=note_impact,
        source="recorded" if result.get("summary_status") or result.get("summary_markdown") or result.get("summary_error") else "inferred",
        stage="note",
    ))

    if result.get("lark_response") or result.get("lark_error") or queue_options.get("export_to_lark"):
        export_ok = isinstance(result.get("lark_response"), dict)
        export_error = _text(result.get("lark_error"))
        entries.append(_entry(
            decision_id="feishu_export",
            title="判断飞书导出状态",
            status="completed" if export_ok else ("failed" if export_error else "pending"),
            decision="已同步至飞书" if export_ok else ("飞书导出失败" if export_error else "等待飞书导出"),
            reason="根据本次导出设置和导出结果判断。",
            evidence=[
                f"导出路线：{queue_options.get('lark_export_route')}" if queue_options.get("lark_export_route") else None,
                f"飞书文档：{result.get('lark_response', {}).get('url')}" if export_ok else None,
                export_error,
            ],
            impact="飞书状态决定用户是否可以直接打开云文档，或需要检查授权后重试。",
            source="recorded" if result.get("lark_response") or result.get("lark_error") else "inferred",
            stage="export",
        ))

    return {
        "decision_log_version": DECISION_LOG_VERSION,
        "entries": entries,
        "entry_count": len(entries),
    }
