"""Chapter coverage evidence table post-processing."""

from __future__ import annotations

from typing import Any

from backend.core.result_schema import canonical_display_segments, canonical_raw_segments


def _text(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _timed_segments_from(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timed: list[dict[str, Any]] = []
    for segment in segments:
        text = _text(segment.get("text"))
        start = _float(segment.get("start"))
        end = _float(segment.get("end"))
        if not text or start is None or end is None or end <= start:
            continue
        timed.append({"text": text, "start": start, "end": end})
    return timed


def _timed_segments(result: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    raw = _timed_segments_from(canonical_raw_segments(result))
    if raw:
        return raw, "raw_segments"
    display = _timed_segments_from(canonical_display_segments(result))
    if display:
        return display, "display_segments"
    return [], None


def _segment_char_ranges(transcript: str, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranges: list[dict[str, Any]] = []
    cursor = 0
    fallback_cursor = 0
    for segment in segments:
        text = _text(segment.get("text"))
        if not text:
            continue
        found = transcript.find(text, cursor)
        if found < 0:
            found = fallback_cursor
        end = found + len(text)
        ranges.append({
            "char_start": found,
            "char_end": end,
            "start": segment["start"],
            "end": segment["end"],
        })
        cursor = max(end, cursor)
        fallback_cursor = end + 1
    return ranges


def _time_range_for_chars(char_start: int | None, char_end: int | None, timed_ranges: list[dict[str, Any]]) -> dict[str, float | None]:
    if char_start is None or char_end is None or char_end <= char_start:
        return {"start_seconds": None, "end_seconds": None}
    overlaps = [
        item
        for item in timed_ranges
        if item["char_end"] > char_start and item["char_start"] < char_end
    ]
    if not overlaps:
        return {"start_seconds": None, "end_seconds": None}
    return {
        "start_seconds": min(item["start"] for item in overlaps),
        "end_seconds": max(item["end"] for item in overlaps),
    }


def _bind_item_time(item: dict[str, Any], timed_ranges: list[dict[str, Any]]) -> dict[str, Any]:
    char_start = _int(item.get("char_start"))
    char_end = _int(item.get("char_end"))
    time_range = _time_range_for_chars(char_start, char_end, timed_ranges)
    if time_range["start_seconds"] is None or time_range["end_seconds"] is None:
        return dict(item)
    return {
        **item,
        "start_seconds": round(time_range["start_seconds"], 3),
        "end_seconds": round(time_range["end_seconds"], 3),
    }


def bind_chapter_coverage_time_ranges(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """Attach subtitle time ranges to a chapter coverage table when possible."""
    if not isinstance(result, dict):
        return result
    coverage = result.get("chapter_coverage")
    if not isinstance(coverage, dict):
        return result
    transcript = _text(result.get("transcript_text"))
    segments, time_binding_source = _timed_segments(result)
    if not transcript or not segments:
        return result
    timed_ranges = _segment_char_ranges(transcript, segments)
    if not timed_ranges:
        return result

    next_coverage = dict(coverage)
    bound_counts: dict[str, int] = {}
    for key in ("segments", "evidence", "chapters"):
        items = coverage.get(key)
        if not isinstance(items, list):
            continue
        bound_items = []
        count = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            bound = _bind_item_time(item, timed_ranges)
            if bound.get("start_seconds") is not None and bound.get("end_seconds") is not None:
                count += 1
            bound_items.append(bound)
        next_coverage[key] = bound_items
        bound_counts[f"time_bound_{key}_count"] = count

    summary = dict(next_coverage.get("summary") or {})
    summary.update({
        "time_bound": any(bound_counts.values()),
        "time_binding_source": time_binding_source,
        **bound_counts,
    })
    next_coverage["summary"] = summary
    return {**result, "chapter_coverage": next_coverage}
