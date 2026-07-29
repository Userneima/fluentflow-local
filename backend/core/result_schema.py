"""Canonical result payload schema helpers."""

from __future__ import annotations

from typing import Any

RESULT_SCHEMA_VERSION = "2"

LEGACY_SEGMENT_KEYS = {
    "segments",
    "bilingual_segments",
    "translated_segments_zh",
    "cleaned_segments",
}


def sanitize_raw_segments(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    segments: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "")
        segment: dict[str, Any] = {"text": text}
        for key in ("start", "end"):
            try:
                segment[key] = float(item.get(key) or 0)
            except (TypeError, ValueError):
                segment[key] = 0.0
        if item.get("speaker"):
            segment["speaker"] = str(item.get("speaker"))
        segments.append(segment)
    return segments


def sanitize_display_segments(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    segments: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        text_zh = str(item.get("text_zh") or "").strip()
        if not text and not text_zh:
            continue
        segment: dict[str, Any] = {"text": text}
        if text_zh:
            segment["text_zh"] = text_zh
        for key in ("start", "end"):
            try:
                segment[key] = float(item.get(key) or 0)
            except (TypeError, ValueError):
                segment[key] = 0.0
        for key in ("speaker", "source_start_index", "source_end_index"):
            if item.get(key) is not None:
                segment[key] = item.get(key)
        segments.append(segment)
    return segments


def canonical_raw_segments(result: dict[str, Any]) -> list[dict[str, Any]]:
    is_current = str(result.get("result_schema_version") or "") == RESULT_SCHEMA_VERSION
    if is_current:
        return sanitize_raw_segments(result.get("raw_segments"))
    # Legacy rows sometimes used `raw_segments` for pre-cleanup STT noise, so prefer `segments`.
    return (
        sanitize_raw_segments(result.get("segments"))
        or sanitize_raw_segments(result.get("raw_segments"))
        or sanitize_raw_segments(result.get("cleaned_segments"))
    )


def canonical_display_segments(result: dict[str, Any]) -> list[dict[str, Any]]:
    display = sanitize_display_segments(result.get("display_segments"))
    if display:
        return display
    if str(result.get("result_schema_version") or "") == RESULT_SCHEMA_VERSION:
        return canonical_raw_segments(result)

    bilingual = sanitize_display_segments(result.get("bilingual_segments"))
    if bilingual:
        return bilingual
    raw = canonical_raw_segments(result)
    translated_segments = sanitize_raw_segments(result.get("translated_segments_zh"))
    if raw and translated_segments:
        merged: list[dict[str, Any]] = []
        for source, translated in zip(raw, translated_segments):
            text = str(source.get("text") or "").strip()
            text_zh = str(translated.get("text") or translated.get("text_zh") or "").strip()
            if not text and not text_zh:
                continue
            segment = dict(source)
            segment["text"] = text
            if text_zh:
                segment["text_zh"] = text_zh
            merged.append(segment)
        if merged:
            return merged
    return raw


def normalize_result_for_storage(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return result
    next_result = dict(result)
    raw = canonical_raw_segments(next_result)
    display = canonical_display_segments(next_result)

    next_result["result_schema_version"] = RESULT_SCHEMA_VERSION
    for key in LEGACY_SEGMENT_KEYS:
        next_result.pop(key, None)
    if raw:
        next_result["raw_segments"] = raw
    else:
        next_result.pop("raw_segments", None)
    if display:
        next_result["display_segments"] = display
        if any(str(segment.get("text_zh") or "").strip() for segment in display):
            next_result["subtitle_mode"] = "bilingual_zh"
        elif not next_result.get("subtitle_mode"):
            next_result["subtitle_mode"] = "source_only"
    else:
        next_result.pop("display_segments", None)
    return next_result


def normalize_result_for_read(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    if str(result.get("result_schema_version") or "") == RESULT_SCHEMA_VERSION:
        return normalize_result_for_storage(result)
    normalized = normalize_result_for_storage(result)
    if isinstance(normalized, dict):
        normalized["result_schema_migrated_from"] = result.get("result_schema_version") or "legacy"
    return normalized
