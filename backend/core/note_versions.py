"""Every version of a note that something is about to write over, kept.

Requirement (owner, 2026-10-21, with the note kept in Feishu as well): before
either side is overwritten (the md beside the PDF, the task's own note, the
Feishu copy), the version being replaced is saved first, so it can be found
again. The copies go in ``过程文件/历史版本/`` next to the note:

    <md stem>_<side>_<YYYYmmdd-HHMMSS>.md

``side`` says where that version lived (``本地`` for the md in the folder,
``FluentFlow`` for the task's note, ``飞书`` for the Feishu document). The text
is kept as it was, so restoring is copying it back over the md; its screenshot
links point at ``过程文件/截图/`` as seen from the md.

A version identical to the newest copy already kept for the same note is not
saved again: a sync that changes nothing must not pile up copies.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

DIRNAME = "历史版本"

LOCAL = "本地"
TASK = "FluentFlow"
FEISHU = "飞书"


def folder_for(md: Path) -> Path:
    from backend.core.output_folder import PROCESS_DIRNAME

    return md.parent / PROCESS_DIRNAME / DIRNAME


def versions(md: Path) -> list[Path]:
    """The kept versions of ``md``, oldest first."""
    folder = folder_for(md)
    if not folder.is_dir():
        return []
    prefix = f"{md.stem}_"
    found = [p for p in folder.glob(f"{md.stem}_*.md") if p.name.startswith(prefix)]
    return sorted(found, key=lambda p: (p.stat().st_mtime, p.name))


def keep(md: Path, text: str, side: str) -> Path | None:
    """Save ``text``, the version of ``md``'s note that ``side`` held, before it
    is overwritten. Returns the copy, or None when there was nothing new to keep."""
    if not text.strip():
        return None
    kept = versions(md)
    if kept:
        try:
            if kept[-1].read_text(encoding="utf-8") == text:
                return None
        except OSError:
            pass
    folder = folder_for(md)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    copy = folder / f"{md.stem}_{side}_{stamp}.md"
    n = 2
    while copy.exists():
        copy = folder / f"{md.stem}_{side}_{stamp}-{n}.md"
        n += 1
    copy.write_text(text, encoding="utf-8")
    return copy
