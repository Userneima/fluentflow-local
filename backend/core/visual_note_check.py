"""The check a frame note passes before it becomes the task's note.

Measured on 2026-10-09: of twenty notes the subscription channel wrote in the
stability run, one was the whole text ``ewline placeholder`` and three began
with residue the model put into the note field itself (``： # 标题``,
``deep# 标题``, ``好的，这是一段面试录像的笔记。`` above the title). All four
were stored as finished notes and replaced whatever the task had.

Two mechanical rules, nothing that judges what the note says:

- Residue glued in front of the title's ``#`` is cut, and so is a short
  assistant-style opening line (one of the fixed openers below) sitting right
  above the first heading. These rules only ever remove text before the first
  heading.
- A note far too short for its transcript is refused; the caller writes it
  once more, and failing again, keeps the task's current note.
"""

from __future__ import annotations

import re

# Characters of residue that can sit glued before the title's ``#``.
_GLUED_PREFIX_MAX = 8
# An opening line longer than this is content, not a preamble.
_PREAMBLE_MAX = 40
# How an assistant opens a reply rather than a note. A fixed list on purpose:
# a line that does not start like this is left alone.
_PREAMBLE_OPENERS = ("好的", "好，", "以下是", "下面是", "这是一", "Here is", "Here's", "Sure")
_HEADING = re.compile(r"#{1,6}\s")
# What residue in front of the ``#`` looks like: punctuation and spaces, or a
# lowercase word fragment of three letters or more (``deep``), or one or two
# non-ASCII characters (``厂#``, measured 2026-10-10). A capital letter or a
# short ASCII word is kept, because ``C# 入门`` is a title.
_RESIDUE = re.compile(r"[\s\W_]+|[a-z]{3,}\s*|[^\x00-\x7f]{1,2}\s*")
# A stray mark at the very start, in front of a title with no ``#`` at all
# (``« 批判性思维``). Markdown's own openers are not in this set.
_STRAY_LEAD = re.compile(r"\A[«»‹›¶§•·※]+\s+")

# A note this short is a failed write whatever the recording was. Below the
# transcript threshold a recording is a short clip, and a short note is right.
MIN_NOTE_CHARS = 200
MIN_TRANSCRIPT_CHARS_FOR_CHECK = 1000


def strip_leading_residue(markdown: str) -> str:
    """The note without what came before its title. Unchanged when the start
    matches neither rule."""
    text = (markdown or "").lstrip()
    if "»" not in text.split("\n", 1)[0]:  # «…» is a pair someone meant
        text = _STRAY_LEAD.sub("", text)
    lines = text.split("\n")
    first = lines[0]
    hash_at = first.find("#")
    if 0 < hash_at <= _GLUED_PREFIX_MAX and _HEADING.match(first[hash_at:]) and _RESIDUE.fullmatch(first[:hash_at]):
        lines[0] = first[hash_at:]
        return "\n".join(lines)
    if "#" not in first and len(first.strip()) <= _PREAMBLE_MAX and first.startswith(_PREAMBLE_OPENERS):
        rest = [line for line in lines[1:]]
        following = next((line for line in rest if line.strip()), "")
        if _HEADING.match(following):
            return "\n".join(rest).lstrip()
    return text


def too_short_reason(markdown: str, transcript: str) -> str | None:
    """Why this note cannot be used, or ``None`` when it can."""
    chars = len((markdown or "").strip())
    if len((transcript or "").strip()) >= MIN_TRANSCRIPT_CHARS_FOR_CHECK and chars < MIN_NOTE_CHARS:
        return f"Claude 返回的笔记只有 {chars} 个字，不像一份完整的笔记"
    return None


__all__ = ["MIN_NOTE_CHARS", "strip_leading_residue", "too_short_reason"]
