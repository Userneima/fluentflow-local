"""Parse existing transcript/subtitle files into plain text and segments."""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass
from functools import lru_cache
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
    # UTF-16 only with its byte-order mark: without one, almost any even
    # number of bytes decodes, and a GB18030 subtitle file came out as
    # garbage with no timed line in it (push review, 2026-11-25).
    utf16 = ("utf-16",) if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ()
    for encoding in ("utf-8-sig", *utf16, "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _readable_share(text: str) -> float:
    """The share of ``text`` that reads as ordinary Chinese or English text:
    printable ASCII, the usual whitespace, and characters simplified or
    traditional Chinese text is written in (GB2312 or Big5). The bytes of
    any text read as UTF-16 come out as CJK code points too, but mostly rare
    ones, and the ones read from English letters seldom all common."""
    if not text:
        return 0.0
    return sum(1 for char in text if _readable(char)) / len(text)


@lru_cache(maxsize=8192)
def _readable(char: str) -> bool:
    if " " <= char <= "~" or char in "\t\n\r\f":
        return True
    if char < "\x80":
        return False
    for encoding in ("gb2312", "big5"):
        try:
            char.encode(encoding)
            return True
        except UnicodeEncodeError:
            continue
    return False


def _decode_start(sample: bytes, encoding: str) -> str | None:
    """``sample``, the start of a file, decoded strictly; a character cut off
    at its end is left out. None when it is not in ``encoding``."""
    try:
        return codecs.getincrementaldecoder(encoding)().decode(sample, final=False)
    except UnicodeDecodeError:
        return None


def utf16_without_mark(raw: bytes) -> bool:
    """Whether the bytes look like UTF-16 saved without its byte-order mark,
    which ``_decode_text`` does not read as UTF-16: the start of the file,
    read as UTF-16 little- or big-endian, is ordinary Chinese or English
    text, and read the way ``_decode_text`` reads it, it is not (an ASCII
    character of UTF-16 reads as itself and a NUL, Chinese as garbage).

    Only a 4 KB sample is looked at. A file that reads as ordinary text the
    usual way is never taken for it, so stray bytes, a cut-off last
    character, NUL padding, terminal colour codes or a Latin-1 file are read
    as before (review of 71b101f, 2026-11-26)."""
    sample = raw[:4096]
    if len(sample) < 8 or sample[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return False
    usual = _decode_start(sample, "utf-8-sig")
    if usual is None:
        usual = _decode_start(sample, "gb18030")
    if usual is None:
        usual = sample.decode("utf-8", errors="replace")
    if _readable_share(usual) >= 0.9:
        return False
    even = sample[: len(sample) // 2 * 2]
    return any(
        (text := _decode_start(even, encoding)) is not None and _readable_share(text) >= 0.9
        for encoding in ("utf-16-le", "utf-16-be")
    )


UTF16_HINT = "文件可能是不带编码标记的 UTF-16，用文本编辑器另存为 UTF-8 后再交"


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
