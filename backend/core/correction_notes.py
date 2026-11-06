"""A series' own list of names and terms the transcriber keeps getting wrong.

Requirement (owner, 2026-10-18): recordings of one series sit in one folder (a
team's weekly meetings, a training camp). The user keeps a short document in
that folder, ``FluentFlow 校正文档.md``: the right spelling, what the
transcript tends to make of it, what it is. When a recording is processed,
FluentFlow reads every such document from the recording's folder upwards
(nearest first) and gives them to the model that writes the note, which then writes the right names
instead of marking them as unsure. Only what recurs goes in; a global name
table was turned down as too much upkeep for what it brings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.core import job_store

FILENAME = "FluentFlow 校正文档.md"
# Read of each document; the rest of a longer one is not read.
MAX_CHARS = 4000
# Read of all documents together, nearest first: a topic list above a
# series list is read in full only while the two fit.
MAX_TOTAL_CHARS = 8000


def for_folder(start: Path) -> str:
    """Every correction document from ``start`` up to the home folder, the
    nearest first, or empty.

    They stack (owner's to-do, 2026-10-24): a topic list (AI interviews) sits
    in a folder above a series list (one team's weekly meetings), and a
    recording of the series needs both. Reading only the nearest made the
    series list hide the topic one."""
    home = Path.home()
    texts: list[str] = []
    total = 0
    for folder in [start, *start.parents]:
        candidate = folder / FILENAME
        try:
            text = candidate.read_text(encoding="utf-8").strip()[:MAX_CHARS] if candidate.is_file() else ""
        except OSError:
            text = ""
        if text:
            text = text[: max(0, MAX_TOTAL_CHARS - total)]
            if text:
                texts.append(text)
                total += len(text)
        if folder == home or folder == folder.parent:
            break
    return "\n\n".join(texts)


def for_path(recording: Path) -> str:
    """The correction documents above ``recording``, nearest first."""
    return for_folder(recording.parent)


def _folder_of(job: dict[str, Any]) -> Path | None:
    """The folder the task's recording is in, or will be in.

    A video from a link gets its folder under the link root only once its
    note is written (``output_folder._link_original``), so its first note
    looks from the folder it already has (found by its marker, also after
    the user sorted it into a topic folder) or else from the link root,
    where the folder is about to be made."""
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    origin = metadata.get("folder_intake") if isinstance(metadata.get("folder_intake"), dict) else {}
    recorded = str(origin.get("original_path") or "").strip()
    if recorded:
        return Path(recorded).expanduser().parent
    kept = str(metadata.get("kept_original_path") or "").strip()
    if kept and Path(kept).expanduser().is_file():
        return Path(kept).expanduser().parent
    if str(metadata.get("video_source_url") or "").strip():
        from backend.core import output_folder

        task_id = str(job.get("task_id") or "")
        root = output_folder.link_root()
        owned = next((child for child in output_folder._link_folders(root)
                      if task_id and output_folder._owner(child) == task_id), None)
        return owned or root
    if kept:
        return Path(kept).expanduser().parent
    return None


def for_task(task_id: str, client_id: str | None = None) -> str:
    job = job_store.get_job(task_id, client_id=client_id) if task_id else None
    folder = _folder_of({"task_id": task_id, **(job or {})})
    return for_folder(folder) if folder is not None else ""


def as_section(text: str) -> str:
    """The document as a section the note rules can carry; empty without one."""
    if not text.strip():
        return ""
    return (
        "\n\n## 这个系列的校正文档\n\n"
        "下面是用户为这一系列录像整理的、转录常听错的名字和说法。转录稿里出现这里列的听法时，"
        "按这里的正确写法写，不再标「（存疑）」；没列的照上面「转录稿的错」的规则处理。\n\n"
        + text.strip()
    )


def rules_for(task_id: str, rules: str, client_id: str | None = None) -> str:
    return rules + as_section(for_task(task_id, client_id))


__all__ = ["FILENAME", "as_section", "for_folder", "for_path", "for_task", "rules_for"]
