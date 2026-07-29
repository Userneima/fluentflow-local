"""Note quality evaluation report helpers.

This module deliberately separates observable run metrics from semantic quality
judgment. Completeness, faithfulness, and usefulness should come from a human
or model review payload, not from brittle text-length heuristics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import re

from backend.core.result_schema import canonical_display_segments, canonical_raw_segments, normalize_result_for_read

NOTE_QUALITY_REPORT_VERSION = "1"

RUBRIC_DIMENSIONS = (
    "coverage",
    "faithfulness",
    "specificity",
    "structure",
    "redundancy",
    "readability",
)


@dataclass(frozen=True)
class NoteQualityInput:
    sample_id: str
    result: dict[str, Any]
    job: dict[str, Any] | None = None
    source_path: str | None = None
    review: dict[str, Any] | None = None


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


def _int(value: Any) -> int | None:
    number = _num(value)
    if number is None:
        return None
    return int(number)


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[。！？.!?\n]+", text or "") if item.strip()]


def _payload_result(payload: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not isinstance(payload, dict):
        raise ValueError("quality input must be a JSON object")
    if isinstance(payload.get("result"), dict):
        result = normalize_result_for_read(payload["result"])
        return result, payload
    result = normalize_result_for_read(payload)
    if not isinstance(result, dict):
        raise ValueError("quality input does not contain a result object")
    return result, None


def load_note_quality_input(path: Path, *, review: dict[str, Any] | None = None) -> NoteQualityInput:
    payload = json.loads(path.read_text(encoding="utf-8"))
    result, job = _payload_result(payload)
    sample_id = _text(
        result.get("task_id")
        or (job or {}).get("task_id")
        or path.stem
    ) or path.stem
    return NoteQualityInput(
        sample_id=sample_id,
        result=result,
        job=job,
        source_path=str(path),
        review=review,
    )


def load_review_file(path: Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("review file must be a JSON object")
    return payload


def _review_for_sample(review: dict[str, Any] | None, sample_id: str) -> dict[str, Any] | None:
    if not review:
        return None
    samples = review.get("samples")
    if isinstance(samples, dict) and isinstance(samples.get(sample_id), dict):
        return samples[sample_id]
    if isinstance(review.get(sample_id), dict):
        return review[sample_id]
    if any(key in review for key in ("scores", "covered_points", "missed_important_points", "notes")):
        return review
    return None


def _quality_review(review: dict[str, Any] | None) -> dict[str, Any]:
    scores = review.get("scores") if isinstance(review, dict) and isinstance(review.get("scores"), dict) else {}
    normalized_scores = {
        dimension: _num(scores.get(dimension))
        for dimension in RUBRIC_DIMENSIONS
    }
    covered_points = review.get("covered_points") if isinstance(review, dict) and isinstance(review.get("covered_points"), list) else []
    missed_points = review.get("missed_important_points") if isinstance(review, dict) and isinstance(review.get("missed_important_points"), list) else []
    total_points = _int(review.get("total_points")) if isinstance(review, dict) else None
    important_points = _int(review.get("important_points")) if isinstance(review, dict) else None
    covered_important_points = _int(review.get("covered_important_points")) if isinstance(review, dict) else None
    if covered_important_points is None and important_points is not None:
        covered_important_points = max(important_points - len(missed_points), 0)
    important_coverage_rate = (
        round(covered_important_points / important_points, 4)
        if covered_important_points is not None and important_points and important_points > 0
        else None
    )
    return {
        "status": "reviewed" if review else "pending_review",
        "rubric": normalized_scores,
        "covered_points": covered_points,
        "missed_important_points": missed_points,
        "total_points": total_points,
        "important_points": important_points,
        "covered_important_points": covered_important_points,
        "important_coverage_rate": important_coverage_rate,
        "hallucination_risk": review.get("hallucination_risk") if isinstance(review, dict) else None,
        "reviewer": review.get("reviewer") if isinstance(review, dict) else None,
        "notes": review.get("notes") if isinstance(review, dict) else None,
    }


def _usage_metrics(result: dict[str, Any], job: dict[str, Any] | None) -> dict[str, Any]:
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    metadata = (job or {}).get("metadata") if isinstance((job or {}).get("metadata"), dict) else {}
    token_usage = (
        result.get("token_usage")
        if isinstance(result.get("token_usage"), dict)
        else (usage.get("tokens") if isinstance(usage.get("tokens"), dict) else {})
    )
    return {
        "elapsed_seconds": _num(
            result.get("summary_elapsed_seconds")
            or result.get("note_generation_elapsed_seconds")
            or metadata.get("summary_elapsed_seconds")
        ),
        "model_call_count": _int(
            result.get("model_call_count")
            or result.get("note_model_call_count")
            or metadata.get("model_call_count")
        ),
        "input_tokens": _int(token_usage.get("input_tokens") or token_usage.get("prompt_tokens")),
        "output_tokens": _int(token_usage.get("output_tokens") or token_usage.get("completion_tokens")),
        "total_tokens": _int(token_usage.get("total_tokens")),
        "estimated_units": _num(usage.get("estimated_units") or result.get("estimated_units")),
        "billable_units": _num(usage.get("billable_units") or result.get("billable_units")),
    }


def _observable_warnings(result: dict[str, Any], transcript: str, summary: str) -> list[str]:
    warnings: list[str] = []
    if not transcript:
        warnings.append("missing_transcript")
    if not summary:
        warnings.append("missing_summary")
    if summary and transcript and len(summary) / max(len(transcript), 1) < 0.03:
        warnings.append("very_high_compression_needs_review")
    if result.get("resolved_note_mode") in {"high_fidelity", "chapter_coverage"} and not result.get("note_mode_evidence_count"):
        warnings.append("evidence_count_missing_for_high_quality_mode")
    if result.get("resolved_note_mode") == "chapter_coverage" and result.get("note_mode_coverage_missing_count") is None:
        warnings.append("coverage_missing_count_unrecorded")
    if result.get("summary_status") == "failed":
        warnings.append("summary_failed")
    return warnings


def build_note_quality_report(item: NoteQualityInput) -> dict[str, Any]:
    result = item.result
    job = item.job or {}
    raw_segments = canonical_raw_segments(result)
    display_segments = canonical_display_segments(result)
    transcript = _text(result.get("transcript_text")) or "\n".join(_text(segment.get("text")) for segment in raw_segments)
    summary = _text(result.get("summary_markdown"))
    summary_chars = len(summary)
    transcript_chars = len(transcript)
    review = _review_for_sample(item.review, item.sample_id)
    chapter_coverage = result.get("chapter_coverage") if isinstance(result.get("chapter_coverage"), dict) else {}
    chapter_coverage_summary = chapter_coverage.get("summary") if isinstance(chapter_coverage.get("summary"), dict) else {}

    coverage_meta = {
        "coverage_checked": result.get("coverage_checked"),
        "coverage_revision_used": result.get("coverage_revision_used"),
        "evidence_count": _int(_first_present(result.get("note_mode_evidence_count"), chapter_coverage_summary.get("evidence_count"))),
        "chapter_count": _int(_first_present(result.get("note_mode_chapter_count"), chapter_coverage_summary.get("chapter_count"))),
        "important_evidence_count": _int(_first_present(result.get("note_mode_important_evidence_count"), chapter_coverage_summary.get("important_evidence_count"))),
        "covered_important_evidence_count": _int(_first_present(result.get("note_mode_covered_important_evidence_count"), chapter_coverage_summary.get("covered_important_evidence_count"))),
        "coverage_missing_count": _int(_first_present(result.get("note_mode_coverage_missing_count"), chapter_coverage_summary.get("coverage_missing_count"))),
        "chapter_coverage_version": chapter_coverage.get("chapter_coverage_version"),
    }
    important = coverage_meta["important_evidence_count"]
    covered = coverage_meta["covered_important_evidence_count"]
    coverage_meta["recorded_important_coverage_rate"] = (
        round(covered / important, 4)
        if covered is not None and important and important > 0
        else None
    )

    return {
        "note_quality_report_version": NOTE_QUALITY_REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "sample": {
            "sample_id": item.sample_id,
            "title": _text(result.get("display_title") or result.get("raw_title") or result.get("filename") or job.get("source_filename")),
            "source_path": item.source_path,
            "source_type": job.get("source_type") or result.get("source"),
            "duration_seconds": _num(result.get("audio_duration_seconds") or job.get("source_duration_seconds")),
        },
        "run": {
            "requested_note_mode": result.get("requested_note_mode"),
            "resolved_note_mode": result.get("resolved_note_mode"),
            "prompt_preset": result.get("prompt_preset"),
            "prompt_preset_label": result.get("prompt_preset_label"),
            "provider": result.get("note_mode_plan_provider"),
            "model": result.get("note_mode_plan_model"),
            "status": result.get("summary_status") or ("completed" if summary else "unknown"),
        },
        "material_metrics": {
            "transcript_chars": transcript_chars,
            "transcript_sentence_count": len(_sentences(transcript)),
            "raw_segment_count": len(raw_segments),
            "display_segment_count": len(display_segments),
            "source_language": result.get("source_language") or result.get("detected_language"),
            "subtitle_mode": result.get("subtitle_mode"),
        },
        "note_metrics": {
            "summary_chars": summary_chars,
            "summary_sentence_count": len(_sentences(summary)),
            "compression_ratio": round(summary_chars / transcript_chars, 4) if transcript_chars else None,
        },
        "coverage_metadata": coverage_meta,
        "usage_metrics": _usage_metrics(result, job),
        "quality_review": _quality_review(review),
        "observable_warnings": _observable_warnings(result, transcript, summary),
    }


def build_note_quality_collection(items: list[NoteQualityInput]) -> dict[str, Any]:
    reports = [build_note_quality_report(item) for item in items]
    by_mode: dict[str, dict[str, Any]] = {}
    for report in reports:
        mode = _text(report["run"].get("resolved_note_mode") or "unknown")
        bucket = by_mode.setdefault(mode, {"count": 0, "sample_ids": [], "avg_summary_chars": 0.0, "avg_compression_ratio": None})
        bucket["count"] += 1
        bucket["sample_ids"].append(report["sample"]["sample_id"])
    for mode, bucket in by_mode.items():
        mode_reports = [report for report in reports if _text(report["run"].get("resolved_note_mode") or "unknown") == mode]
        bucket["avg_summary_chars"] = round(
            sum(report["note_metrics"]["summary_chars"] for report in mode_reports) / max(len(mode_reports), 1),
            2,
        )
        ratios = [
            report["note_metrics"]["compression_ratio"]
            for report in mode_reports
            if report["note_metrics"]["compression_ratio"] is not None
        ]
        bucket["avg_compression_ratio"] = round(sum(ratios) / len(ratios), 4) if ratios else None
    return {
        "note_quality_collection_version": NOTE_QUALITY_REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "run_count": len(reports),
        "modes": by_mode,
        "reports": reports,
    }


def render_note_quality_markdown(collection: dict[str, Any]) -> str:
    lines = [
        "# Note Quality Evaluation Report",
        "",
        f"- Runs: {collection.get('run_count', 0)}",
        f"- Generated: {collection.get('generated_at')}",
        "",
        "## Mode Summary",
        "",
        "| Mode | Runs | Avg summary chars | Avg compression |",
        "| --- | ---: | ---: | ---: |",
    ]
    for mode, item in sorted((collection.get("modes") or {}).items()):
        lines.append(
            f"| {mode} | {item.get('count', 0)} | {item.get('avg_summary_chars')} | {item.get('avg_compression_ratio')} |"
        )
    lines.extend(["", "## Runs", ""])
    for report in collection.get("reports") or []:
        sample = report.get("sample") or {}
        run = report.get("run") or {}
        material = report.get("material_metrics") or {}
        note = report.get("note_metrics") or {}
        coverage = report.get("coverage_metadata") or {}
        review = report.get("quality_review") or {}
        lines.extend([
            f"### {sample.get('sample_id')}",
            "",
            f"- Title: {sample.get('title') or '-'}",
            f"- Mode: {run.get('resolved_note_mode') or '-'}",
            f"- Transcript chars: {material.get('transcript_chars')}",
            f"- Summary chars: {note.get('summary_chars')}",
            f"- Compression ratio: {note.get('compression_ratio')}",
            f"- Evidence: {coverage.get('evidence_count')}",
            f"- Important coverage: {coverage.get('covered_important_evidence_count')}/{coverage.get('important_evidence_count')}",
            f"- Review status: {review.get('status')}",
            f"- Warnings: {', '.join(report.get('observable_warnings') or []) or '-'}",
            "",
        ])
    lines.extend([
        "## How To Read This",
        "",
        "This report records observable metrics and attached reviews. It does not automatically decide note quality.",
        "Use it to compare modes, then inspect boundary samples manually before changing production defaults.",
        "",
    ])
    return "\n".join(lines)
