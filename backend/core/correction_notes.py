"""A series' own list of names and terms the transcriber keeps getting wrong.

Requirement (owner, 2026-10-18): recordings of one series sit in one folder (a
team's weekly meetings, a training camp). The user keeps a short document in
that folder, ``FluentFlow 校正文档.md``: the right spelling, what the
transcript tends to make of it, what it is. When a recording is processed,
FluentFlow looks for the document from the recording's folder upwards and
gives it to the model that writes the note, which then writes the right names
instead of marking them as unsure. Only what recurs goes in; a global name
table was turned down as too much upkeep for what it brings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.core import job_store

FILENAME = "FluentFlow 校正文档.md"
MAX_CHARS = 4000


def for_path(recording: Path) -> str:
    """The nearest correction document above ``recording``, or empty."""
    home = Path.home()
    for folder in [recording.parent, *recording.parent.parents]:
        candidate = folder / FILENAME
        try:
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8").strip()[:MAX_CHARS]
        except OSError:
            return ""
        if folder == home or folder == folder.parent:
            break
    return ""


def _recording_of(job: dict[str, Any]) -> Path | None:
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    origin = metadata.get("folder_intake") if isinstance(metadata.get("folder_intake"), dict) else {}
    for value in (origin.get("original_path"), metadata.get("kept_original_path")):
        if str(value or "").strip():
            return Path(str(value)).expanduser()
    return None


def for_task(task_id: str, client_id: str | None = None) -> str:
    job = job_store.get_job(task_id, client_id=client_id) if task_id else None
    recording = _recording_of(job or {})
    return for_path(recording) if recording is not None else ""


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


__all__ = ["FILENAME", "as_section", "for_path", "for_task", "rules_for"]
