"""Shared job/result lifecycle semantics for FluentFlow."""

from __future__ import annotations

from typing import Any

from backend.core.chapter_coverage import bind_chapter_coverage_time_ranges


def result_has_transcript(result: dict[str, Any] | None) -> bool:
    if not isinstance(result, dict):
        return False
    return bool(
        str(result.get("transcript_text") or result.get("transcript_text_preview") or "").strip()
        or (isinstance(result.get("raw_segments"), list) and len(result.get("raw_segments") or []) > 0)
        or (isinstance(result.get("display_segments"), list) and len(result.get("display_segments") or []) > 0)
        or (isinstance(result.get("segments"), list) and len(result.get("segments") or []) > 0)
        or (isinstance(result.get("cleaned_segments"), list) and len(result.get("cleaned_segments") or []) > 0)
    )


def job_has_transcript_result(job: dict[str, Any] | None) -> bool:
    if not isinstance(job, dict):
        return False
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    return result_has_transcript(result)


def result_for_transcript_only(base_result: dict[str, Any]) -> dict[str, Any]:
    return {
        **base_result,
        "status": "completed",
        "summary_markdown": base_result.get("summary_markdown") or "",
        "summary_skipped": True,
        "summary_status": "skipped",
    }


def result_for_summary_failure(base_result: dict[str, Any], summary_error: str) -> dict[str, Any]:
    return {
        **base_result,
        "status": "completed",
        "summary_markdown": "",
        "summary_skipped": False,
        "summary_status": "failed",
        "summary_error": summary_error,
    }


def result_for_summary_success(
    base_result: dict[str, Any],
    summary_markdown: str,
    *,
    requested_note_mode: str | None = None,
    resolved_note_mode: str | None = None,
    note_mode_chunk_count: int | None = None,
    note_mode_segment_count: int | None = None,
    note_mode_evidence_count: int | None = None,
    note_mode_chapter_count: int | None = None,
    note_mode_important_evidence_count: int | None = None,
    note_mode_covered_important_evidence_count: int | None = None,
    note_mode_coverage_missing_count: int | None = None,
    chapter_coverage: dict[str, Any] | None = None,
    note_mode_plan_reason: str | None = None,
    note_mode_plan_confidence: str | None = None,
    note_mode_plan_warnings: list[str] | None = None,
    note_mode_plan_provider: str | None = None,
    note_mode_plan_model: str | None = None,
    note_mode_plan_fallback: bool | None = None,
    note_mode_plan_error: str | None = None,
    note_mode_plan_selected_mode: str | None = None,
    prompt_preset: str | None = None,
    prompt_preset_label: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        **base_result,
        "status": "completed",
        "summary_markdown": summary_markdown,
        "summary_skipped": False,
        "summary_status": "completed",
    }
    if requested_note_mode is not None:
        result["requested_note_mode"] = requested_note_mode
    if resolved_note_mode is not None:
        result["resolved_note_mode"] = resolved_note_mode
    if note_mode_chunk_count is not None:
        result["note_mode_chunk_count"] = note_mode_chunk_count
    if note_mode_segment_count is not None:
        result["note_mode_segment_count"] = note_mode_segment_count
    if note_mode_evidence_count is not None:
        result["note_mode_evidence_count"] = note_mode_evidence_count
    if note_mode_chapter_count is not None:
        result["note_mode_chapter_count"] = note_mode_chapter_count
    if note_mode_important_evidence_count is not None:
        result["note_mode_important_evidence_count"] = note_mode_important_evidence_count
    if note_mode_covered_important_evidence_count is not None:
        result["note_mode_covered_important_evidence_count"] = note_mode_covered_important_evidence_count
    if note_mode_coverage_missing_count is not None:
        result["note_mode_coverage_missing_count"] = note_mode_coverage_missing_count
    if chapter_coverage is not None:
        result["chapter_coverage"] = chapter_coverage
    if note_mode_plan_reason is not None:
        result["note_mode_plan_reason"] = note_mode_plan_reason
    if note_mode_plan_confidence is not None:
        result["note_mode_plan_confidence"] = note_mode_plan_confidence
    if note_mode_plan_warnings is not None:
        result["note_mode_plan_warnings"] = note_mode_plan_warnings
    if note_mode_plan_provider is not None:
        result["note_mode_plan_provider"] = note_mode_plan_provider
    if note_mode_plan_model is not None:
        result["note_mode_plan_model"] = note_mode_plan_model
    if note_mode_plan_fallback is not None:
        result["note_mode_plan_fallback"] = note_mode_plan_fallback
    if note_mode_plan_error is not None:
        result["note_mode_plan_error"] = note_mode_plan_error
    if note_mode_plan_selected_mode is not None:
        result["note_mode_plan_selected_mode"] = note_mode_plan_selected_mode
    if prompt_preset is not None:
        result["prompt_preset"] = prompt_preset
    if prompt_preset_label is not None:
        result["prompt_preset_label"] = prompt_preset_label
    return bind_chapter_coverage_time_ranges(result)
