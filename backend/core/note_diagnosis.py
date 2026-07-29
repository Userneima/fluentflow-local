"""Edition-neutral note generation diagnosis."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from backend.core.result_schema import canonical_display_segments, canonical_raw_segments


def _text(value: Any) -> str:
    return str(value or "").strip()


def build_note_generation_diagnosis(
    job: dict[str, Any],
    result: dict[str, Any],
    *,
    diagnose_error: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    summary = _text(result.get("summary_markdown"))
    status = _text(result.get("summary_status") or job.get("summary_status")).lower()
    stage = _text(result.get("stage") or job.get("stage")).lower()
    raw_error = _text(result.get("summary_error") or job.get("error_reason"))
    has_transcript = bool(
        _text(result.get("transcript_text") or result.get("transcript_text_preview"))
    )
    has_transcript = has_transcript or bool(
        canonical_raw_segments(result) or canonical_display_segments(result)
    )

    base = {
        "status": "pending",
        "code": "note_pending",
        "severity": "info",
        "title": "笔记还在生成",
        "detail": "转录已进入摘要阶段，等待 AI 返回笔记。",
        "next_action": "稍等片刻；如果长时间没有变化，再刷新任务状态。",
        "retryable": False,
    }
    if summary:
        return {
            **base,
            "status": "completed",
            "code": "note_completed",
            "severity": "success",
            "title": "笔记已生成",
            "detail": "当前结果包含可用的 AI 笔记。",
            "next_action": "",
            "retryable": True,
        }
    if not has_transcript:
        return {
            **base,
            "status": "unavailable",
            "code": "transcript_missing",
            "severity": "warning",
            "title": "还没有可用于生成笔记的转录",
            "detail": "需要先完成转录，AI 才能生成笔记。",
            "next_action": "先等待或重新提交转录任务。",
        }
    if result.get("summary_skipped") or status == "skipped":
        return {
            **base,
            "status": "skipped",
            "code": "transcript_only_mode",
            "severity": "neutral",
            "title": "本次开启了仅转录模式",
            "detail": "系统按设置跳过了 AI 笔记，转录和字幕已保留。",
            "next_action": "需要笔记时，打开结果并重新生成。",
            "retryable": True,
        }
    if status == "failed" or raw_error:
        diagnosis = diagnose_error(raw_error)
        code = str(diagnosis.get("code") or "ai_note_failed")
        title = str(diagnosis.get("title") or "AI 笔记生成失败")
        next_action = str(
            diagnosis.get("next_action")
            or "重新生成；如果仍失败，换一个笔记模式或缩短材料。"
        )
        if code in {"unknown_error", "video_download_failed"}:
            code = "ai_note_failed"
            title = "AI 笔记生成失败"
        return {
            **base,
            "status": "failed",
            "code": code,
            "severity": "error",
            "title": title,
            "detail": str(
                diagnosis.get("detail") or raw_error or "处理失败，但没有返回具体原因。"
            ),
            "next_action": next_action,
            "retryable": True,
        }
    if status == "pending" or stage == "summary":
        return base
    return {
        **base,
        "code": "note_missing_unknown",
        "severity": "warning",
        "title": "暂时没有可见笔记",
        "detail": "转录已存在，但结果里没有记录明确的笔记状态。",
        "next_action": "重新生成；如果失败，再查看任务详情。",
        "retryable": True,
    }
