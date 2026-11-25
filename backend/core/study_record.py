"""Marking a task's note as studied, and what that tells the note's writer.

Requirement (owner, 2026-11-25): what he does while studying a note counts as
feedback his agent can learn from. When he finishes a lesson he clicks 学完了;
the task leaves the pinned ones (pinned means 正在学) and goes into the 学完的
list, grouped by the folder its recording came from. He may say in one click
whether the note was 好用, 有错 or 有漏.

At that click the changes he made to the note since it was last written whole
(edits on the page, in Obsidian, pulled back from Feishu, or words an agent
changed at his request) are put into a study record beside the note feedback
(``note_feedback``). A note studied without changes is recorded too: it says
the note needed none. This runs once, on his click, so it is not the product
watching his edits in the background (decision 5.38).

Whether an edit is a corrected fact, a thought of his own or a problem with
how notes are written is a judgement about meaning, left to the agent reading
the record; nothing here sorts the edits.

Stored on the task as ``metadata.studied`` (``STUDIED_KEY``): when, the
verdict, the course (the recording's folder name) and the id of its record.
Set only through ``mark``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.core import job_store, note_changes, note_feedback, note_versions, original_locator, test_copy

logger = logging.getLogger(__name__)

STUDIED_KEY = "studied"
VERDICTS = ("good", "wrong", "missing")
VERDICT_LABELS = {"good": "好用", "wrong": "有错", "missing": "有漏"}
# Tasks with no recording on this machine (a link, an upload) are grouped here.
NO_FOLDER = "链接和上传"

# Writers that change some words of a note someone already had: the owner on
# the page, in Obsidian or in Feishu, or an agent changing words he asked for.
# Every other writer (a note generated, written by an agent, rewritten,
# restored, times put back) writes the whole note, which is where his changes
# are counted from.
EDITS = (note_versions.WEB_EDIT, note_versions.OBSIDIAN, note_versions.FEISHU_PULL, note_versions.AGENT_EDIT)


# A screenshot is linked one way in the md beside the recording (过程文件/截图/…)
# and another in the task's note (/jobs/<id>/artifacts/frame?file=…); the md's
# kept versions use the first. Both are written as the file name before
# comparing, so the link's form is not counted as his change (seen on a copy
# of real notes edited in Obsidian, 2026-11-25).
_SCREENSHOT = re.compile(r"\]\((?:过程文件/截图/|/jobs/[^/)\s]+/artifacts/frame\?file=)([^)#&\s]+)[^)]*\)")


def _comparable(note: str) -> str:
    return _SCREENSHOT.sub(r"](截图/\1)", note)


# The note as last written whole, and which edits came after it, beside the
# change log in 过程文件 (for a note with no folder, in the task's own
# storage). Kept when the note is saved, not found later in 历史版本: an edit
# on the page keeps its version only when the folder catches up about a minute
# later, and a note with no folder keeps none (found on a test copy,
# 2026-11-25).
WRITTEN_FILE = "写成时的笔记.md"
EDITS_FILE = "写成后的改动.json"


def _written_paths(task_id: str, result: dict[str, Any]) -> tuple[Path, Path]:
    process = note_changes.path_for(task_id, result).parent
    return process / WRITTEN_FILE, process / EDITS_FILE


def _replace(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _on_result_saved(task_id: str, before: dict[str, Any] | None, after: dict[str, Any] | None) -> None:
    old = str((before or {}).get("summary_markdown") or "")
    new = str((after or {}).get("summary_markdown") or "")
    by = note_versions.changed_by(after)
    if not new.strip() or old == new or by is None or not test_copy.writable(task_id):
        return
    written, edits = _written_paths(task_id, after or {})
    try:
        if by not in EDITS:
            _replace(written, new)
            edits.unlink(missing_ok=True)
            return
        if not written.is_file() and old.strip():
            # Written before this was kept (or by a save that told no
            # listener): the note before its first edit is the note as written.
            _replace(written, old)
        try:
            counts = json.loads(edits.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            counts = {}
        label = note_versions.BY_LABELS[by]
        counts[label] = int(counts.get(label) or 0) + 1
        _replace(edits, json.dumps(counts, ensure_ascii=False))
    except OSError as exc:  # the save stands; the study record falls back to 历史版本
        logger.warning("written note of %s not kept: %s", task_id, exc)


job_store.add_result_listener(_on_result_saved)


class StudyError(ValueError):
    pass


def course_of(job: dict[str, Any]) -> str:
    """The name of the folder the task's recording came from."""
    _field, path = original_locator._recorded(job)
    name = Path(path).parent.name if path else ""
    return name or NO_FOLDER


def edits_since_written(task_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """The owner's changes to the note since it was last written whole.

    The note as written is kept on every save (``_on_result_saved``). For a
    note last written before that, the kept versions say what replaced each
    one, so the oldest
    of the versions followed only by edits is the note as written; it is
    compared with the note now. Both the task's note and the md beside the
    recording are looked at: an edit pulled from Feishu or made by an agent
    has kept only the md's version (seen on real notes, 2026-11-25).
    """
    note = str(result.get("summary_markdown") or "")
    written_file, edits_file = _written_paths(task_id, result)
    try:
        written = written_file.read_text(encoding="utf-8")
        try:
            by = json.loads(edits_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            by = {}
        found = note_changes.changes(written, note) if written.strip() != note.strip() else []
        return {"changes": found, "total": len(found), "edited_by": by, "baseline_kept": True}
    except OSError:
        pass  # a note last written before 2026-11-25: found from 历史版本 below
    kept = [v for v in note_versions.kept(note_versions.md_for(task_id, result))
            if v.side in (note_versions.TASK, note_versions.LOCAL)]
    start = len(kept)
    while start > 0 and kept[start - 1].by in EDITS:
        start -= 1
    if note_versions.changed_by(result) not in EDITS:
        return {"changes": [], "total": 0, "edited_by": {}, "baseline_kept": True}
    if start == len(kept):
        # Edited, but no version from before the edit was kept.
        label = note_versions.BY_LABELS[note_versions.changed_by(result)]
        return {"changes": [], "total": 0, "edited_by": {label: 1}, "baseline_kept": False}
    # One edit keeps a version on each side in the same second: counted once.
    by: dict[str, int] = {}
    for code, _second in dict.fromkeys((v.by, v.stamp[:15]) for v in kept[start:]):
        label = note_versions.BY_LABELS[code]
        by[label] = by.get(label, 0) + 1
    try:
        written = kept[start].path.read_text(encoding="utf-8")
    except OSError:
        return {"changes": [], "total": 0, "edited_by": by, "baseline_kept": False}
    before, after = _comparable(written), _comparable(note)
    found = note_changes.changes(before, after) if before.strip() != after.strip() else []
    return {"changes": found, "total": len(found), "edited_by": by, "baseline_kept": True}


def mark(task_id: str, *, studied: bool = True, verdict: str | None = None,
         client_id: str | None = None) -> dict[str, Any]:
    """Mark the task studied (and unpin it), or take the mark back.

    Marking again makes the study record again under its id, so changing the
    verdict later also takes in edits made since. Taking the mark back deletes
    the record: a click by mistake must not teach the writer anything.
    """
    job = job_store.get_job(task_id, client_id=client_id)
    if not job:
        raise StudyError("任务不存在")
    if verdict is not None and verdict not in VERDICTS:
        raise StudyError("verdict 只能是 good（好用）、wrong（有错）或 missing（有漏）")
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    previous = metadata.get(STUDIED_KEY) if isinstance(metadata.get(STUDIED_KEY), dict) else {}
    if not studied:
        if previous.get("record_id"):
            note_feedback.remove_study(str(previous["record_id"]))
        job_store.update_job_metadata(task_id, {STUDIED_KEY: None})
        return {"studied": None}
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    if not str(result.get("summary_markdown") or "").strip():
        raise StudyError("这个任务还没有笔记，没有可以学完的")
    # Marked again without a verdict (the button clicked before the page knew
    # it was studied) keeps the one already given.
    if verdict is None:
        verdict = previous.get("verdict") if previous.get("verdict") in VERDICTS else None
    edits = edits_since_written(task_id, result)
    title = str(result.get("display_title") or metadata.get("display_title") or job.get("source_filename") or "")
    record = note_feedback.record_study(task_id=task_id, title=title, verdict=verdict, edits=edits,
                                        replace_id=str(previous.get("record_id") or ""))
    mark = {
        "at": previous.get("at") or datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "course": course_of(job),
        "record_id": record["id"],
        "edits": edits["total"],
    }
    job_store.update_job_metadata(task_id, {STUDIED_KEY: mark, job_store.PINNED_AT_KEY: None})
    return {"studied": mark}


__all__ = ["EDITS", "NO_FOLDER", "STUDIED_KEY", "StudyError", "VERDICTS", "VERDICT_LABELS",
           "course_of", "edits_since_written", "mark"]
