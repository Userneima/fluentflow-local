"""A task's note versions as an agent reads and restores them.

Requirement (owner, 2026-10-22, after a 40,000-character note was saved over
with its 240-character preview and found again only by hand): an agent can
list the kept versions of a task's note with what replaced each one and how
long each was, read any of them, and put one back. Putting one back keeps the
note it replaces first and then goes through the note's ordinary save, so the
md in the output folder and the Feishu copy follow; it names the note version
it started from and is refused when the note changed since.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.core import note_versions
from backend.core.job_store import get_job, save_result_changes
from backend.core.result_schema import content_versions


class NoteHistoryError(Exception):
    def __init__(self, message: str, *, status: int = 409, code: str = "refused") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


STALE = ("笔记在你读取版本列表之后又被改过（网页、Obsidian、飞书或别的 agent），这次恢复没有执行，"
         "笔记没有改动。重新列出历史版本，确认要恢复的还是那一版，再用新的 base_version 恢复。")


def _job(task_id: str, client_id: str | None) -> dict[str, Any]:
    job = get_job(task_id, client_id=client_id)
    if not job:
        raise NoteHistoryError("任务不存在", status=404, code="not_found")
    return job


def _result(job: dict[str, Any]) -> dict[str, Any]:
    return dict(job.get("result") or {}) if isinstance(job.get("result"), dict) else {}


def _by(code: str | None) -> dict[str, Any]:
    return {"changed_by": code, "changed_by_label": note_versions.BY_LABELS.get(code or "", note_versions.NOT_RECORDED)}


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise NoteHistoryError(f"这一版读不出来（{exc}）", status=404, code="unreadable") from exc


def list_versions(task_id: str, *, client_id: str | None = None) -> dict[str, Any]:
    """The note now and every kept version, newest first: when it was kept,
    where it lived, what replaced it, its length and the change from the
    previous version on the same side, a sharp drop flagged."""
    result = _result(_job(task_id, client_id))
    md = note_versions.md_for(task_id, result)
    kept = note_versions.kept(md)
    rows: list[dict[str, Any]] = []
    # Each side is compared with its own previous version: the Feishu copy is
    # Feishu's export format, a few hundred characters longer than the same
    # note on the other sides, and comparing across sides showed that as a
    # change every time (seen on a copy of a real note's 56 versions).
    previous: dict[str, int] = {}
    for version in kept:
        size = note_versions.chars(_read(version.path))
        before = previous.get(version.side)
        rows.append({
            "version_id": version.id,
            "saved_at": version.saved_at.isoformat(timespec="seconds"),
            "side": version.side,
            # What wrote over this version; the version after it (or the
            # current note) is what that writer left.
            "replaced_by": version.by,
            "replaced_by_label": note_versions.BY_LABELS.get(version.by or "", note_versions.NOT_RECORDED),
            "chars": size,
            "chars_change": None if before is None else size - before,
            "sharp_drop": note_versions.sharp_drop(before, size),
            "restorable": version.side != note_versions.FEISHU,
        })
        previous[version.side] = size
    note = str(result.get("summary_markdown") or "")
    size = note_versions.chars(note)
    # The task's note against the last version it had; the folder's md when
    # the task's note was never kept.
    before = previous.get(note_versions.TASK, previous.get(note_versions.LOCAL))
    current = {
        "note_version": content_versions(result).get("note_version"),
        "chars": size,
        "chars_change": None if before is None else size - before,
        "sharp_drop": note_versions.sharp_drop(before, size),
        **_by(note_versions.changed_by(result)),
    }
    return {
        "ok": True,
        "task_id": task_id,
        "current": current,
        "versions": list(reversed(rows)),
        "kept_in": str(note_versions.folder_for(md)),
    }


def _find(task_id: str, result: dict[str, Any], version_id: str) -> note_versions.Version:
    md = note_versions.md_for(task_id, result)
    for version in note_versions.kept(md):
        if version.id == version_id:
            return version
    raise NoteHistoryError(f"这个任务的笔记没有叫 {version_id} 的历史版本。先列出历史版本，用列表里的 version_id。",
                           status=404, code="no_such_version")


def read_version(task_id: str, version_id: str, *, client_id: str | None = None) -> dict[str, Any]:
    result = _result(_job(task_id, client_id))
    version = _find(task_id, result, version_id)
    text = _read(version.path)
    return {
        "ok": True,
        "task_id": task_id,
        "version_id": version.id,
        "saved_at": version.saved_at.isoformat(timespec="seconds"),
        "side": version.side,
        "replaced_by": version.by,
        "replaced_by_label": note_versions.BY_LABELS.get(version.by or "", note_versions.NOT_RECORDED),
        "chars": note_versions.chars(text),
        "markdown": text,
        "path": str(version.path),
    }


def _as_task_note(task_id: str, md: Path, version: note_versions.Version, text: str) -> str:
    """The version as the task keeps its note. A copy of the md points its
    screenshots at ``过程文件/截图…/``; they go back to the task's own pictures."""
    if version.side != note_versions.LOCAL:
        return text
    from backend.core import output_folder

    process = md.parent / output_folder.PROCESS_DIRNAME
    shots = [path for path in process.glob(f"{output_folder.SHOTS_DIRNAME}*") if path.is_dir()] \
        if process.is_dir() else []
    prefixes = tuple(p for path in shots for p in (path.relative_to(md.parent).as_posix(), path.name))
    return output_folder._stored_form(task_id, text, prefixes)


def _keep_current(task_id: str, md: Path, result: dict[str, Any]) -> None:
    """The note as it is before the restore, kept first. When the md in the
    folder is exactly what FluentFlow last wrote, that copy is kept, so the
    folder's own refresh after the restore finds it already kept."""
    from backend.core import output_folder

    note = str(result.get("summary_markdown") or "")
    record = result.get(output_folder.RESULT_KEY) if isinstance(result.get(output_folder.RESULT_KEY), dict) else {}
    try:
        on_disk = md.read_text(encoding="utf-8")
    except OSError:
        on_disk = ""
    if on_disk and record.get("md_written_digest") == output_folder._digest(on_disk):
        note_versions.keep(md, on_disk, note_versions.LOCAL, note_versions.RESTORE)
    else:
        note_versions.keep(md, note, note_versions.TASK, note_versions.RESTORE)


def restore_version(
    task_id: str, version_id: str, *, base_version: str | None, client_id: str | None = None,
) -> dict[str, Any]:
    job = _job(task_id, client_id)
    before = _result(job)
    if not isinstance(base_version, str) or not base_version.strip():
        raise NoteHistoryError("恢复要带上 base_version：列出历史版本时 current.note_version 的值。",
                               status=422, code="base_version_required")
    if base_version.strip() != content_versions(before).get("note_version"):
        raise NoteHistoryError(STALE, code="stale")
    version = _find(task_id, before, version_id)
    if version.side == note_versions.FEISHU:
        raise NoteHistoryError(
            "这一版是飞书文档被覆盖前的样子，存的是飞书导出的格式，截图和章节链接不能原样回到笔记里，"
            "不能直接恢复。它通常和同一时间前后的「本地」或「FluentFlow」版本内容相同，恢复那一版；"
            "或者读取这一版，把需要的段落改进笔记。",
            code="feishu_side")
    md = note_versions.md_for(task_id, before)
    note = _as_task_note(task_id, md, version, _read(version.path)).strip()
    if not note:
        raise NoteHistoryError("这一版是空的，不能恢复。", code="empty")
    current = str(before.get("summary_markdown") or "")
    if note == current.strip():
        return {"ok": True, "task_id": task_id, "restored": False, "message": "笔记现在就是这一版，没有改动。",
                "note_version": content_versions(before).get("note_version")}

    _keep_current(task_id, md, before)
    from backend.core.result_artifacts import _attach_result_artifacts

    after = dict(before)
    if not after.get("summary_edited") and not after.get("summary_original_markdown"):
        after["summary_original_markdown"] = current
    after.update({
        "summary_markdown": note,
        "summary_skipped": False,
        "summary_status": "completed",
        "summary_error": None,
        "summary_edited": True,
        "summary_edited_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "summary_restored_from": version.id,
    })
    note_versions.declare(after, note_versions.RESTORE)
    after = _attach_result_artifacts(task_id, after, parts=("summary",))
    saved = save_result_changes(task_id, before, after, client_id=client_id)
    if not saved:
        raise NoteHistoryError("任务不存在", status=404, code="not_found")
    _follow_now(task_id, saved.get("result"))
    restored = saved.get("result") if isinstance(saved.get("result"), dict) else {}
    return {
        "ok": True,
        "task_id": task_id,
        "restored": True,
        "version_id": version.id,
        "chars": note_versions.chars(note),
        "note_version": content_versions(restored).get("note_version"),
        "follows": _follows(restored),
    }


def _follow_now(task_id: str, result: Any) -> None:
    """A restore is one deliberate act, not typing: the folder and Feishu are
    brought in step now rather than after the minute an editor's saves wait."""
    from backend.core import feishu_sync, output_folder

    if not isinstance(result, dict):
        return
    if isinstance(result.get(output_folder.RESULT_KEY), dict):
        output_folder.schedule_sync(task_id)
    if feishu_sync.recorded_sync(result) is not None:
        feishu_sync.schedule_push_when_quiet(task_id, delay=0)


def _follows(result: dict[str, Any]) -> list[str]:
    from backend.core import feishu_sync, output_folder

    places = ["FluentFlow 任务里的笔记（已写入）"]
    folder = result.get(output_folder.RESULT_KEY) if isinstance(result.get(output_folder.RESULT_KEY), dict) else {}
    if folder.get("md_path"):
        places.append(f"产出文件夹的 md 和 PDF（{folder['md_path']}，几秒内更新）")
    if feishu_sync.recorded_sync(result) is not None:
        places.append("飞书文档（只重写变了的章节，几秒到一分钟内更新）")
    return places
