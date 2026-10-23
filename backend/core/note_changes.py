"""What each change to a note changed, written down in 过程文件/修改记录.md.

Requirement (owner, 2026-10-23, decision 6.36): the whole-note copies in
``历史版本`` say how the note read before each change, not what changed; the
note about Luo Fuli kept 62 of them in two days, most a few characters apart.
Each change of the task's own note (``summary_markdown``) adds an entry, newest
first: when, what made it (the writer's declaration, ``note_versions``), the
chapter it is in, the words before and after.

Only the task's note is followed. Every change ends there (an edit in the md
is taken in, an edit in Feishu is pulled in); the md and the Feishu document
being overwritten afterwards only follow it, so one change is one entry.

A change to more than half the note, or a sharp drop in length, is one line
with the length before and after instead of the words. The note is compared
paragraph by paragraph (its lines); within a long paragraph only the changed
words are shown, with some words on either side. Changes of spacing alone (a
table lined up by the editor) are not listed.

Writing the log never fails the save that caused it.
"""

from __future__ import annotations

import difflib
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.core import job_store, note_versions

logger = logging.getLogger(__name__)

FILENAME = "修改记录.md"
HEADER = (
    "# 修改记录\n\n"
    "这篇笔记每次被改了什么记在这里，新的在最上面。每次被替换的整篇旧版都在旁边的「历史版本」文件夹里；"
    "要恢复到某一版，告诉你的 agent 是哪个时间的哪一版，它会用 restore_note_version 恢复。\n"
)

# A paragraph longer than this shows only the changed words, with CONTEXT
# characters on either side.
SHORT_PARAGRAPH = 120
CONTEXT = 25
# Changes this close together in one paragraph are shown as one.
JOIN_GAP = 40
# A paragraph longer than this is not compared word by word, which takes time
# growing with the square of its length (1.5 to 1.9 seconds for one paragraph
# of 20,000 characters, measured 2026-10-23, in the thread saving the note):
# what lies between the words it starts and ends with is the change.
LONGEST_COMPARED = 3000
# Longest text shown for one side of one change.
SHOWN_CHARS = 300
# Changes listed in one entry; the rest are counted.
LISTED = 20
WHOLE_NOTE_SHARE = 0.5

_HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*#*\s*$")
_WHITESPACE = re.compile(r"\s+")
_DASHES = re.compile(r"-{3,}")
_LOCK = threading.Lock()


def path_for(task_id: str, result: dict[str, Any] | None) -> Path:
    """Beside 历史版本, in 过程文件 (for a note with no folder, in the task's
    own storage)."""
    return note_versions.folder_for(note_versions.md_for(task_id, result)).parent / FILENAME


def _heading_before(lines: list[str], index: int) -> str | None:
    """The nearest ## or ### heading at or above ``index``; a ### one with
    the ## chapter it is in, since every chapter has its own （一）."""
    sub = None
    for line in reversed(lines[: index + 1]):
        match = _HEADING.match(line)
        if not match:
            continue
        if len(match.group(1)) == 2:
            return f"{match.group(2)} › {sub}" if sub else match.group(2)
        if sub is None:
            sub = match.group(2)
    return sub


def _clip(text: str) -> str:
    text = text.strip()
    if len(text) <= SHOWN_CHARS:
        return text
    return text[:SHOWN_CHARS] + f"……（共 {len(text)} 字）"


def _one_line(text: str) -> str:
    return " / ".join(part.strip() for part in text.split("\n") if part.strip())


def _ends(old: str, new: str) -> tuple[int, int]:
    """How many characters the two start with and end with alike."""
    start = 0
    limit = min(len(old), len(new))
    while start < limit and old[start] == new[start]:
        start += 1
    end = 0
    while end < limit - start and old[-1 - end] == new[-1 - end]:
        end += 1
    return start, end


def _spans(old: str, new: str) -> list[list[int]]:
    """Where two versions of one paragraph differ, as [i1, i2, j1, j2]."""
    if max(len(old), len(new)) > LONGEST_COMPARED:
        start, end = _ends(old, new)
        return [[start, len(old) - end, start, len(new) - end]]
    spans: list[list[int]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if spans and i1 - spans[-1][1] <= JOIN_GAP:
            spans[-1][1], spans[-1][3] = i2, j2
        else:
            spans.append([i1, i2, j1, j2])
    return spans


def _within(old: str, new: str) -> list[tuple[str, str]]:
    """The changed words of one paragraph, each with some words around it."""
    if len(old) <= SHORT_PARAGRAPH and len(new) <= SHORT_PARAGRAPH:
        return [(old, new)]
    spans = _spans(old, new)
    found = []
    for i1, i2, j1, j2 in spans:
        start_old, start_new = max(0, i1 - CONTEXT), max(0, j1 - CONTEXT)
        end_old, end_new = min(len(old), i2 + CONTEXT), min(len(new), j2 + CONTEXT)
        before = ("……" if start_old else "") + old[start_old:end_old] + ("……" if end_old < len(old) else "")
        after = ("……" if start_new else "") + new[start_new:end_new] + ("……" if end_new < len(new) else "")
        found.append((before, after))
    return found


def _bare(text: str) -> str:
    """The text without its spacing, and a table's |---| line at any width."""
    return _DASHES.sub("---", _WHITESPACE.sub("", text))


def changes(before: str, after: str) -> list[dict[str, str | None]]:
    """Each change from ``before`` to ``after``: its chapter and the words
    before and after (an empty side is an addition or a removal)."""
    old, new = before.split("\n"), after.split("\n")
    found: list[dict[str, str | None]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        chapter = _heading_before(new, j1) if j2 > j1 else _heading_before(old, i1)
        removed = [line for line in old[i1:i2] if line.strip()]
        added = [line for line in new[j1:j2] if line.strip()]
        if not removed and not added:
            continue  # blank lines only
        if len(removed) == len(added):
            pairs = [piece for a, b in zip(removed, added) for piece in _within(a, b)]
        else:
            pairs = [("\n".join(removed), "\n".join(added))]
        for a, b in pairs:
            if a and b and _bare(a) == _bare(b):
                continue  # spacing only: an editor lining up a table
            found.append({"chapter": chapter, "before": _one_line(_clip(a)), "after": _one_line(_clip(b))})
    return found


def _changed_share(before: str, after: str) -> float:
    """How much of the earlier note was changed or removed, in characters:
    a paragraph changed in a few words counts those words, and words added
    count nothing, so a chapter added to a short note is not the note
    replaced (review, 2026-10-23)."""
    old, new = before.split("\n"), after.split("\n")
    changed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if i2 - i1 == j2 - j1:
            for a, b in zip(old[i1:i2], new[j1:j2]):
                changed += sum(k2 - k1 for k1, k2, _l1, _l2 in _spans(a, b))
        else:
            changed += sum(len(line) for line in old[i1:i2])
    total = sum(len(line) for line in old) or 1
    return changed / total


def entry(before: str, after: str, by: str | None, at: datetime | None = None) -> str | None:
    """The entry for one change of the note, or None when nothing changed."""
    if before.strip() == after.strip():
        return None
    stamp = (at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    who = note_versions.BY_LABELS.get(by or "", note_versions.NOT_RECORDED)
    head = f"## {stamp} · {who}\n\n"
    size_before, size_after = note_versions.chars(before), note_versions.chars(after)
    if note_versions.sharp_drop(size_before, size_after):
        return head + f"字数骤降：从 {size_before} 字到 {size_after} 字。\n"
    if _changed_share(before, after) > WHOLE_NOTE_SHARE:
        return head + f"整篇换掉：从 {size_before} 字到 {size_after} 字。\n"
    found = changes(before, after)
    if not found:
        return head + "只改了空格和对齐（比如表格被重新排齐），文字没变。\n"
    lines = []
    for change in found[:LISTED]:
        place = f"「{change['chapter']}」" if change["chapter"] else "开头"
        if not change["before"]:
            lines.append(f"- {place}新增：{change['after']}")
        elif not change["after"]:
            lines.append(f"- {place}删去：{change['before']}")
        else:
            lines.append(f"- {place}\n  - 原文：{change['before']}\n  - 改成：{change['after']}")
    if len(found) > LISTED:
        lines.append(f"- 另有 {len(found) - LISTED} 处，见历史版本。")
    return head + "\n".join(lines) + "\n"


def record(path: Path, text: str) -> None:
    """Put ``text`` at the top of the log, under its header."""
    with _LOCK:
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError:
            existing = ""
        start = existing.find("\n## ")
        older = existing[start + 1:] if start >= 0 else ""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(HEADER + "\n" + text + ("\n" + older if older else ""), encoding="utf-8")
        os.replace(temporary, path)


def _on_result_saved(task_id: str, before: dict[str, Any] | None, after: dict[str, Any] | None) -> None:
    old = str((before or {}).get("summary_markdown") or "")
    new = str((after or {}).get("summary_markdown") or "")
    if not old.strip() or old == new:
        return  # a first note replaces nothing
    try:
        text = entry(old, new, note_versions.changed_by(after))
        if text:
            record(path_for(task_id, after), text)
    except Exception as exc:  # noqa: BLE001 - the save stands without its log entry
        logger.warning("change log of %s not written: %s", task_id, exc)


job_store.add_result_listener(_on_result_saved)
