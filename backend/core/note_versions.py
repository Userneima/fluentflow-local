"""Every version of a note that something is about to write over, kept, with
what replaced it.

Requirement (owner, 2026-10-21, with the note kept in Feishu as well): before
either side is overwritten (the md beside the PDF, the task's own note, the
Feishu copy), the version being replaced is saved first, so it can be found
again. The copies go in ``过程文件/历史版本/`` next to the note:

    <md stem>_<side>_<YYYYmmdd-HHMMSS>[-n]_<by>.md

``side`` says where that version lived (``本地`` for the md in the folder,
``FluentFlow`` for the task's note, ``飞书`` for the Feishu document). ``by``
(owner's request, 2026-10-22) says what replaced it: an edit on the page, the
md edited in Obsidian, an agent's note, a rewrite, Feishu, a restore. Copies
kept before 2026-10-22 have no ``by`` and read as not recorded. The text is
kept as it was, so restoring is copying it back over the md; its screenshot
links point at ``过程文件/截图/`` as seen from the md.

What changed the task's note is declared by the writer that changed it
(``declare``), on the note it wrote: a declaration whose note is no longer the
task's note says nothing, so a writer that declares nothing reads as not
recorded rather than as whoever wrote before it.

A version identical to the newest copy already kept for the same note is not
saved again: a sync that changes nothing must not pile up copies.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

DIRNAME = "历史版本"

LOCAL = "本地"
TASK = "FluentFlow"
FEISHU = "飞书"
SIDES = (LOCAL, TASK, FEISHU)

# What replaced a version: the code an agent sees, and the words in the file
# name and in what the user is shown.
WEB_EDIT = "web_edit"
OBSIDIAN = "obsidian"
AGENT_NOTE = "agent_note"
REWRITE = "rewrite"
GENERATED = "generated"
FEISHU_PULL = "feishu_pull"
EXPORT = "export"
RESTORE = "restore"
ADJUST = "adjust"
BY_LABELS = {
    WEB_EDIT: "网页编辑",
    OBSIDIAN: "Obsidian改了md",
    AGENT_NOTE: "agent写入",
    REWRITE: "重写笔记",
    GENERATED: "生成笔记",
    FEISHU_PULL: "飞书拉回",
    EXPORT: "导出到飞书",
    RESTORE: "恢复版本",
    ADJUST: "时间改回原片",
}
NOT_RECORDED = "未记录"
_BY_CODES = {label: code for code, label in BY_LABELS.items()}

RESULT_FIELD = "note_changed_by"

# A version this much shorter than the one before it is flagged: the
# 2026-10-22 loss was a 40,000-character note saved as its 240-character
# preview.
SHARP_DROP_RATIO = 0.5
SHARP_DROP_CHARS = 1000


def folder_for(md: Path) -> Path:
    from backend.core.output_folder import PROCESS_DIRNAME

    return md.parent / PROCESS_DIRNAME / DIRNAME


def md_for(task_id: str, result: dict[str, Any] | None) -> Path:
    """Where a task's note versions are kept: beside its md in the output
    folder, or, for a note with no folder, in the task's own storage."""
    from backend.core.output_folder import RESULT_KEY
    from backend.core.storage_paths import _artifact_storage_dir

    result = result if isinstance(result, dict) else {}
    folder = result.get(RESULT_KEY) if isinstance(result.get(RESULT_KEY), dict) else {}
    if folder.get("md_path"):
        return Path(str(folder["md_path"]))
    return _artifact_storage_dir() / task_id / "笔记.md"


def note_hash(note: str) -> str:
    return hashlib.sha256(note.encode("utf-8")).hexdigest()[:16]


def declare(fields: dict[str, Any], by: str) -> dict[str, Any]:
    """``fields`` (a result, or the fields about to be written into one) with
    ``by`` recorded as what wrote its note. Call it after the note is final."""
    fields[RESULT_FIELD] = {
        "by": by,
        "note": note_hash(str(fields.get("summary_markdown") or "")),
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    return fields


def changed_by(result: dict[str, Any] | None) -> str | None:
    """What wrote the task's current note, or None when that is not recorded."""
    if not isinstance(result, dict):
        return None
    declared = result.get(RESULT_FIELD)
    if not isinstance(declared, dict) or declared.get("by") not in BY_LABELS:
        return None
    if declared.get("note") != note_hash(str(result.get("summary_markdown") or "")):
        return None
    return str(declared["by"])


@dataclass(frozen=True)
class Version:
    path: Path
    side: str
    stamp: str
    by: str | None

    @property
    def id(self) -> str:
        return self.path.name

    @property
    def saved_at(self) -> datetime:
        return datetime.strptime(self.stamp[:15], "%Y%m%d-%H%M%S")


def _pattern(md: Path) -> re.Pattern[str]:
    return re.compile(
        "^" + re.escape(md.stem) + "_(" + "|".join(re.escape(side) for side in SIDES) + r")_(\d{8}-\d{6}(?:-\d+)?)"
        + r"(?:_([^/]+?))?\.md$"
    )


def parse(md: Path, path: Path) -> Version | None:
    match = _pattern(md).match(path.name)
    if not match:
        return None
    label = match.group(3)
    return Version(path=path, side=match.group(1), stamp=match.group(2), by=_BY_CODES.get(label) if label else None)


def _order(version: Version) -> tuple[str, int, str]:
    return (version.stamp[:15], int(version.stamp[16:] or 1), version.path.name)


def kept(md: Path) -> list[Version]:
    """The kept versions of ``md``, oldest first."""
    folder = folder_for(md)
    if not folder.is_dir():
        return []
    found = [v for v in (parse(md, p) for p in folder.glob("*.md")) if v is not None]
    return sorted(found, key=_order)


def versions(md: Path) -> list[Path]:
    """The kept versions of ``md``, oldest first."""
    return [v.path for v in kept(md)]


def keep(md: Path, text: str, side: str, by: str | None = None) -> Path | None:
    """Save ``text``, the version of ``md``'s note that ``side`` held, before
    ``by`` overwrites it. Returns the copy, or None when there was nothing new
    to keep."""
    if not text.strip():
        return None
    existing = versions(md)
    if existing:
        try:
            if existing[-1].read_text(encoding="utf-8") == text:
                return None
        except OSError:
            pass
    folder = folder_for(md)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    label = BY_LABELS.get(by or "", NOT_RECORDED)

    # Two copies kept in the same second get -2, -3: the order is read from
    # the stamp, whichever side or ``by`` each has.
    taken = {v.stamp for v in kept(md)}
    stamped, n = stamp, 2
    while stamped in taken:
        stamped, n = f"{stamp}-{n}", n + 1
    copy = folder / f"{md.stem}_{side}_{stamped}_{label}.md"
    copy.write_text(text, encoding="utf-8")
    return copy


def chars(text: str) -> int:
    return len(text.strip())


def sharp_drop(before: int | None, after: int) -> bool:
    if not before:
        return False
    return after < before * SHARP_DROP_RATIO and before - after >= SHARP_DROP_CHARS
