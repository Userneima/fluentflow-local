"""SRT/VTT subtitle formatting helpers, extracted from server_helpers.py.
Pure leaf functions (typing only) — re-imported by server_helpers."""

from __future__ import annotations

from typing import Any


def _format_subtitle_timestamp(seconds: Any, *, separator: str) -> str:
    try:
        value = max(0.0, float(seconds))
    except (TypeError, ValueError):
        value = 0.0
    total = int(value)
    millis = int(round((value - total) * 1000))
    if millis >= 1000:
        total += 1
        millis -= 1000
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def _format_srt(segments: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for index, segment in enumerate(segments, start=1):
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = _format_subtitle_timestamp(segment.get("start"), separator=",")
        end = _format_subtitle_timestamp(segment.get("end"), separator=",")
        blocks.append(f"{index}\n{start} --> {end}\n{text}\n")
    return "\n".join(blocks).rstrip() + ("\n" if blocks else "")


def _format_vtt(segments: list[dict[str, Any]]) -> str:
    blocks = ["WEBVTT\n"]
    for segment in segments:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = _format_subtitle_timestamp(segment.get("start"), separator=".")
        end = _format_subtitle_timestamp(segment.get("end"), separator=".")
        blocks.append(f"{start} --> {end}\n{text}\n")
    return "\n".join(blocks).rstrip() + "\n"
