"""Lines the transcriber most likely heard out of noise.

Whisper-family models write fluent sentences into music, crowd noise and room
tone, usually in another language and usually the same few: "ご視聴ありがとう
ございました", "구독과 좋아요 부탁드립니다", "Субтитры ...". On a Chinese meeting
(百校, 2026-10-15) the cut judge read them as talk and gave reasons such as
"无关外语字幕" for stretches where nobody was speaking at all.

What is measured here is mechanical: which writing system a line is in, against
the recording's own. Whether the line was real speech is still the model's
call; it is only told which lines are in another script than the rest.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

_HAN = re.compile(r"[一-鿿]")
_KANA = re.compile(r"[぀-ヿ]")
_HANGUL = re.compile(r"[가-힯]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_LATIN = re.compile(r"[A-Za-z]")
# A recording is Chinese when at least this share of its letters are Han.
HAN_SHARE = 0.6


def chinese_recording(texts: Iterable[str]) -> bool:
    han = other = 0
    for text in texts:
        han += len(_HAN.findall(text))
        other += len(_KANA.findall(text)) + len(_HANGUL.findall(text)) + len(_CYRILLIC.findall(text)) + len(_LATIN.findall(text))
    total = han + other
    return bool(total) and han / total >= HAN_SHARE


def other_script(text: str, chinese: bool) -> bool:
    """Whether ``text`` is in another writing system than a Chinese recording:
    any kana, Hangul or Cyrillic, or Latin with no Han at all. English terms
    inside a Chinese sentence are not."""
    if not chinese:
        return False
    if _KANA.search(text) or _HANGUL.search(text) or _CYRILLIC.search(text):
        return True
    return not _HAN.search(text) and len(_LATIN.findall(text)) >= 6


def tagger(segments: list[dict[str, Any]]):
    chinese = chinese_recording(str(seg.get("text") or "") for seg in segments)
    return lambda text: other_script(str(text or ""), chinese)


__all__ = ["chinese_recording", "other_script", "tagger"]
