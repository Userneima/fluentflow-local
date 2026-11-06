"""Changing passages of a note by their exact text, for the follow-along note
(``note_history.replace_text``) and the review note
(``review_note.replace_text``) alike.

Requirement (owner, 2026-11-06): an agent fixing a sentence, or deleting one
kind of content in dozens of places, must not hand back the whole note. Each
passage is named by its text, which must occur in the note exactly once; when
any one of several passages does not, nothing at all changes, so a call never
leaves a note half edited.
"""

from __future__ import annotations

from typing import Any


class ReplaceError(ValueError):
    """Why the replacements were refused; ``code`` is what the agent acts on."""

    def __init__(self, message: str, code: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def occurrences(text: str, part: str) -> list[int]:
    """Where ``part`` starts in ``text``, overlapping places too ("哈哈"
    starts twice in "哈哈哈")."""
    found, start = [], text.find(part)
    while start >= 0:
        found.append(start)
        start = text.find(part, start + 1)
    return found


def pairs(old_text: Any, new_text: Any, replacements: Any) -> list[tuple[str, str]]:
    """The (old, new) pairs a call asks for: ``replacements`` (a list of
    ``{old_text, new_text}``) or the single ``old_text``/``new_text``."""
    if replacements is None:
        raw = [{"old_text": old_text, "new_text": new_text}]
    elif old_text is not None or new_text is not None:
        raise ReplaceError("old_text/new_text 和 replacements 只传一种。", code="bad_request", status=422)
    elif not isinstance(replacements, list) or not replacements:
        raise ReplaceError("replacements 要是非空列表，每项带 old_text 和 new_text。", code="bad_request", status=422)
    else:
        raw = replacements
    result = []
    for index, item in enumerate(raw, start=1):
        label = f"第 {index} 组" if len(raw) > 1 else ""
        old = item.get("old_text") if isinstance(item, dict) else None
        new = item.get("new_text") if isinstance(item, dict) else None
        if not isinstance(old, str) or not old:
            raise ReplaceError(f"{label}old_text 不能为空：写要改掉的那段原文，照笔记里的字一字不差。",
                               code="old_text_required", status=422)
        if not isinstance(new, str):
            raise ReplaceError(f"{label}new_text 要是字符串；删掉那段就传空字符串。", code="new_text_required", status=422)
        result.append((old, new))
    return result


def apply(text: str, wanted: list[tuple[str, str]]) -> str:
    """``text`` with every pair's old passage replaced by its new one. Each old
    passage must occur exactly once in ``text`` as it is now and no two may
    overlap; otherwise ``ReplaceError`` names the first pair that fails and
    nothing is replaced."""
    many = len(wanted) > 1
    spans: list[tuple[int, int, str, int]] = []
    for index, (old, new) in enumerate(wanted, start=1):
        label = f"第 {index} 组的 " if many else ""
        found = occurrences(text, old)
        if not found:
            raise ReplaceError(
                f"{label}old_text 在笔记里一次也没出现，笔记没有改动。照笔记原样复制那段字"
                "（包括标点、空格和换行），再替换。", code="not_found_in_note")
        if len(found) > 1:
            raise ReplaceError(
                f"{label}old_text 在笔记里出现了 {len(found)} 次，不知道改哪一处，笔记没有改动。"
                "把前后的字多带一些，让它在笔记里只出现一次再替换。", code="ambiguous")
        spans.append((found[0], found[0] + len(old), new, index))
    spans.sort()
    for (_, end, _, first), (start, _, _, second) in zip(spans, spans[1:]):
        if start < end:
            raise ReplaceError(
                f"第 {first} 组和第 {second} 组的 old_text 在笔记里有重叠，笔记没有改动。合成一组再替换。",
                code="overlapping")
    for start, end, new, _ in reversed(spans):
        text = text[:start] + new + text[end:]
    return text
