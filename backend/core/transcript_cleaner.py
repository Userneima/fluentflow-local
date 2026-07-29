"""Mechanical transcript cleanup for obvious STT repetition loops."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable


@dataclass(frozen=True)
class TranscriptCleanupIssue:
    kind: str
    segment_index: int
    start: float | None
    end: float | None
    original: str
    cleaned: str
    repeat_unit: str
    repeat_count: int
    reason: str


@dataclass(frozen=True)
class TranscriptCleanupResult:
    cleaned_text: str
    cleaned_segments: tuple[dict[str, float | str], ...]
    issues: tuple[TranscriptCleanupIssue, ...]
    applied_count: int
    removed_segment_count: int
    raw_length: int
    cleaned_length: int


_SPACE_RE = re.compile(r"\s+")
_MEANINGFUL_RE = re.compile(r"[\w\u3400-\u9fff]", re.UNICODE)
_MIN_REPEAT_COUNT = 5
_MIN_LONG_REPEAT_COUNT = 4
_MAX_UNIT_CHARS = 24


def _segment_text(segment: Any) -> str:
    if isinstance(segment, dict):
        return str(segment.get("text") or "")
    return str(getattr(segment, "text", "") or "")


def _segment_start(segment: Any) -> float | None:
    value = segment.get("start") if isinstance(segment, dict) else getattr(segment, "start", None)
    return float(value) if value is not None else None


def _segment_end(segment: Any) -> float | None:
    value = segment.get("end") if isinstance(segment, dict) else getattr(segment, "end", None)
    return float(value) if value is not None else None


def _segment_to_dict(segment: Any, text: str) -> dict[str, float | str]:
    payload: dict[str, float | str] = {"text": text}
    start = _segment_start(segment)
    end = _segment_end(segment)
    speaker = segment.get("speaker") if isinstance(segment, dict) else getattr(segment, "speaker", None)
    if start is not None:
        payload["start"] = start
    if end is not None:
        payload["end"] = end
    if speaker:
        payload["speaker"] = str(speaker)
    return payload


def _normalize_for_compare(text: str) -> str:
    return _SPACE_RE.sub("", text).strip().lower()


def _is_meaningful_unit(unit: str) -> bool:
    stripped = unit.strip()
    if len(_normalize_for_compare(stripped)) < 2:
        return False
    return bool(_MEANINGFUL_RE.search(stripped))


def _repeat_threshold(unit: str) -> int:
    normalized_len = len(_normalize_for_compare(unit))
    if normalized_len >= 4:
        return _MIN_LONG_REPEAT_COUNT
    return _MIN_REPEAT_COUNT


def _skip_spaces(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _count_repeats_at(text: str, start: int, unit: str) -> tuple[int, int]:
    count = 1
    cursor = start + len(unit)
    while cursor < len(text):
        next_cursor = _skip_spaces(text, cursor)
        if not text.startswith(unit, next_cursor):
            break
        count += 1
        cursor = next_cursor + len(unit)
    return count, cursor


def _choose_repeat_candidate(text: str, start: int) -> tuple[str, int, int] | None:
    best: tuple[str, int, int] | None = None
    max_unit_len = min(_MAX_UNIT_CHARS, len(text) - start)
    for unit_len in range(2, max_unit_len + 1):
        unit = text[start : start + unit_len]
        if unit[0].isspace() or unit[-1].isspace() or not _is_meaningful_unit(unit):
            continue
        count, end = _count_repeats_at(text, start, unit)
        if count < _repeat_threshold(unit):
            continue
        consumed = end - start
        # best[...] only evaluated when best is not None (short-circuit); pylint can't narrow the `or`.
        if best is None or consumed > best[2] - start or (consumed == best[2] - start and len(unit) < len(best[0])):  # pylint: disable=unsubscriptable-object
            best = (unit, count, end)
    return best


def _collapse_repeated_phrases(text: str) -> tuple[str, list[tuple[str, int]]]:
    out: list[str] = []
    issues: list[tuple[str, int]] = []
    index = 0
    while index < len(text):
        if text[index].isspace():
            out.append(text[index])
            index += 1
            continue
        candidate = _choose_repeat_candidate(text, index)
        if candidate is None:
            out.append(text[index])
            index += 1
            continue
        unit, count, end = candidate
        out.append(unit)
        issues.append((unit.strip(), count))
        index = end
    cleaned = _SPACE_RE.sub(" ", "".join(out)).strip()
    return cleaned, issues


def _similarity(left: str, right: str) -> float:
    a = _normalize_for_compare(left)
    b = _normalize_for_compare(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _looks_like_repeated_segment(left: str, right: str) -> bool:
    normalized = _normalize_for_compare(left)
    if len(normalized) < 4:
        return False
    return _similarity(left, right) >= 0.92


def _collapse_repeated_segments(
    segments: tuple[dict[str, float | str], ...],
) -> tuple[tuple[dict[str, float | str], ...], list[TranscriptCleanupIssue], int]:
    if not segments:
        return (), [], 0

    cleaned: list[dict[str, float | str]] = []
    issues: list[TranscriptCleanupIssue] = []
    removed_count = 0
    index = 0
    while index < len(segments):
        run = [segments[index]]
        cursor = index + 1
        while cursor < len(segments) and _looks_like_repeated_segment(
            str(run[-1].get("text") or ""),
            str(segments[cursor].get("text") or ""),
        ):
            run.append(segments[cursor])
            cursor += 1

        if len(run) >= 3:
            kept = dict(run[0])
            if "end" in run[-1]:
                kept["end"] = run[-1]["end"]
            cleaned.append(kept)
            removed_count += len(run) - 1
            issues.append(
                TranscriptCleanupIssue(
                    kind="repeated_segments",
                    segment_index=index,
                    start=float(run[0]["start"]) if "start" in run[0] else None,
                    end=float(run[-1]["end"]) if "end" in run[-1] else None,
                    original="\n".join(str(item.get("text") or "") for item in run),
                    cleaned=str(kept.get("text") or ""),
                    repeat_unit=str(run[0].get("text") or "").strip(),
                    repeat_count=len(run),
                    reason="连续字幕段高度重复，疑似 STT 重复幻觉。",
                )
            )
        else:
            cleaned.extend(run)
        index = cursor

    return tuple(cleaned), issues, removed_count


def clean_repeated_transcript(segments: Iterable[Any]) -> TranscriptCleanupResult:
    """Fold obvious STT repetition loops while preserving segment timing when possible."""
    raw_segments = tuple(_segment_to_dict(segment, _segment_text(segment)) for segment in segments)
    raw_text = "\n".join(str(segment["text"]) for segment in raw_segments)

    phrase_cleaned_segments: list[dict[str, float | str]] = []
    issues: list[TranscriptCleanupIssue] = []
    for index, segment in enumerate(raw_segments):
        original = str(segment.get("text") or "")
        cleaned, local_issues = _collapse_repeated_phrases(original)
        phrase_cleaned_segments.append({**segment, "text": cleaned})
        for unit, count in local_issues:
            issues.append(
                TranscriptCleanupIssue(
                    kind="repeated_phrase",
                    segment_index=index,
                    start=float(segment["start"]) if "start" in segment else None,
                    end=float(segment["end"]) if "end" in segment else None,
                    original=original,
                    cleaned=cleaned,
                    repeat_unit=unit,
                    repeat_count=count,
                    reason="同一短语连续重复，疑似 STT 重复幻觉。",
                )
            )

    segment_cleaned, segment_issues, removed_segment_count = _collapse_repeated_segments(
        tuple(phrase_cleaned_segments),
    )
    all_issues = tuple(issues + segment_issues)
    cleaned_text = "\n".join(str(segment["text"]) for segment in segment_cleaned)
    return TranscriptCleanupResult(
        cleaned_text=cleaned_text,
        cleaned_segments=segment_cleaned,
        issues=all_issues,
        applied_count=len(all_issues),
        removed_segment_count=removed_segment_count,
        raw_length=len(raw_text),
        cleaned_length=len(cleaned_text),
    )


__all__ = [
    "TranscriptCleanupIssue",
    "TranscriptCleanupResult",
    "clean_repeated_transcript",
]
