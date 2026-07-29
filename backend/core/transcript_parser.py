"""Parse existing transcript/subtitle files into plain text and segments."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ParsedTranscript:
    text: str
    segments: tuple[dict[str, float | str], ...]
    duration: float


_TAG_RE = re.compile(r"<[^>]+>")
_TIMESTAMP_RE = re.compile(
    r"(?P<h>\d{1,2}:)?(?P<m>\d{1,2}):(?P<s>\d{1,2})(?P<ms>[,.]\d{1,3})?"
)
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_SPEAKER_LABEL_RE = re.compile(r"^\s*([^：:\n]{1,24})[：:]")
_PARAGRAPH_GAP_SECONDS = 8.0
_PARAGRAPH_SOFT_MAX_CHARS = 360
_PARAGRAPH_HARD_MAX_CHARS = 560
_SENTENCE_END_CHARS = set("。！？!?；;.")
_NO_SPACE_BEFORE_RE = re.compile(r"^[,.;:!?，。！？；：、）】》」』”’]")
_NO_SPACE_AFTER_RE = re.compile(r"[(（【《「『“‘]$")


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_timestamp(value: str) -> float:
    match = _TIMESTAMP_RE.search(value.strip())
    if not match:
        raise ValueError(f"Invalid subtitle timestamp: {value}")
    hours = int((match.group("h") or "0:").rstrip(":"))
    minutes = int(match.group("m"))
    seconds = int(match.group("s"))
    ms_raw = (match.group("ms") or "").lstrip(".,")
    millis = int(ms_raw.ljust(3, "0")[:3]) if ms_raw else 0
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def _clean_caption_line(line: str) -> str:
    text = _TAG_RE.sub("", line)
    return text.replace("&nbsp;", " ").strip()


def _ends_sentence(text: str) -> bool:
    stripped = text.rstrip("）】》」』”’\"'")
    return bool(stripped) and stripped[-1] in _SENTENCE_END_CHARS


def _speaker_label(text: str) -> str | None:
    match = _SPEAKER_LABEL_RE.match(text)
    if not match:
        return None
    label = match.group(1).strip()
    if re.search(r"\s", label) and not _CJK_RE.search(label):
        return None
    return label


def _should_insert_space(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if _NO_SPACE_BEFORE_RE.match(right) or _NO_SPACE_AFTER_RE.search(left):
        return False
    if _CJK_RE.search(left[-1]) or _CJK_RE.search(right[0]):
        return False
    return True


def _append_inline(left: str, right: str) -> str:
    right = right.strip()
    if not left:
        return right
    if not right:
        return left
    separator = " " if _should_insert_space(left, right) else ""
    return f"{left}{separator}{right}"


def _merge_segments_into_paragraphs(segments: list[dict[str, float | str]]) -> str:
    paragraphs: list[str] = []
    current = ""
    previous_segment: dict[str, float | str] | None = None

    for segment in segments:
        text = str(segment["text"]).strip()
        if not text:
            continue
        if not current:
            current = text
            previous_segment = segment
            continue

        previous_text = str(previous_segment["text"]) if previous_segment else ""
        gap = (
            float(segment["start"]) - float(previous_segment["end"])
            if previous_segment is not None
            else 0.0
        )
        current_speaker = _speaker_label(text)
        previous_speaker = _speaker_label(previous_text)
        speaker_changed = bool(
            current_speaker and previous_speaker and current_speaker != previous_speaker
        )
        should_break = (
            gap >= _PARAGRAPH_GAP_SECONDS
            or speaker_changed
            or len(current) >= _PARAGRAPH_HARD_MAX_CHARS
            or (len(current) >= _PARAGRAPH_SOFT_MAX_CHARS and _ends_sentence(previous_text))
        )

        if should_break:
            paragraphs.append(current)
            current = text
        else:
            current = _append_inline(current, text)
        previous_segment = segment

    if current:
        paragraphs.append(current)
    return "\n\n".join(paragraphs)


def _parse_timed_captions(content: str) -> ParsedTranscript:
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    segments: list[dict[str, float | str]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.upper() == "WEBVTT" or line.startswith(("NOTE", "STYLE", "REGION")):
            i += 1
            continue
        if line.isdigit() and i + 1 < len(lines) and "-->" in lines[i + 1]:
            i += 1
            line = lines[i].strip()
        if "-->" not in line:
            i += 1
            continue
        start_raw, end_raw = line.split("-->", 1)
        try:
            start = _parse_timestamp(start_raw)
            end = _parse_timestamp(end_raw.split()[0])
        except ValueError:
            i += 1
            continue
        i += 1
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            text = _clean_caption_line(lines[i])
            if text:
                text_lines.append(text)
            i += 1
        text = " ".join(text_lines).strip()
        if text:
            segments.append({"start": start, "end": end, "text": text})
    joined = _merge_segments_into_paragraphs(segments)
    duration = max((float(s["end"]) for s in segments), default=0.0)
    return ParsedTranscript(text=joined, segments=tuple(segments), duration=duration)


def _parse_plain_text(content: str) -> ParsedTranscript:
    lines = [line.strip() for line in content.replace("\r\n", "\n").split("\n")]
    text = "\n".join(line for line in lines if line).strip()
    return ParsedTranscript(text=text, segments=(), duration=0.0)


def parse_transcript_file(raw: bytes, filename: str | None = None) -> ParsedTranscript:
    content = _decode_text(raw)
    suffix = Path(filename or "").suffix.lower()
    if suffix in {".srt", ".vtt"} or "-->" in content:
        parsed = _parse_timed_captions(content)
        if parsed.text:
            return parsed
    return _parse_plain_text(content)
