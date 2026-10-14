"""Every output of a recording from the user's own folder, in one folder beside it.

Requirement (owner, 2026-10-14). A recording the user pointed at on their disk
gets a folder named after it, next to it; the original is not moved:

    <name>/
        <name>_去气口.mp4      the cut video, or <name>_压缩.mp4 when the cut was declined
        <name>_笔记.pdf
        过程文件/
            <name>_笔记.md, 截图/   the note and the screenshots it shows
            <name>.txt, <name>.srt  transcript and subtitles
            <name>_剪辑表.json      where the gaps were cut

Before this only the cut video was put beside the original; the transcript and
the note stayed inside FluentFlow, and the folder above was assembled by hand
when a conversation remembered to.

- Filled as the work goes: the video when the cut is done, the rest when the
  task ends (``sync``), and the note again whenever it changes afterwards.
- Never overwrites another task's results. A folder records the task it belongs
  to in a hidden marker; a second task on the same recording gets the next free
  name (``<name> (2)``), and deleting either is the user's call.
- Uploads and link downloads have no folder of the user's to sit beside and are
  left in FluentFlow.
- A folder that cannot be written does not fail the task; the reason is kept on
  the task.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.core import job_store
from backend.core.storage_paths import _artifact_storage_dir

logger = logging.getLogger(__name__)

MARKER_NAME = ".fluentflow-task"
PROCESS_DIRNAME = "过程文件"
SHOTS_DIRNAME = "截图"
CUT_LABEL = "去气口"
COMPRESSED_LABEL = "压缩"
NOTE_LABEL = "笔记"
CUT_LIST_LABEL = "剪辑表"
RESULT_KEY = "output_folder"
MAX_FOLDERS_PER_RECORDING = 99

_UNSAFE = re.compile(r'[\\/:*?"<>|\r\n]+')
_IMAGE = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)(\))")


class OutputFolderError(RuntimeError):
    pass


# ── which folder ────────────────────────────────────────────────────────────

def original_path(job: dict[str, Any] | None) -> Path | None:
    """The recording a by-path task was read from, whether or not it still exists."""
    metadata = (job or {}).get("metadata")
    origin = metadata.get("folder_intake") if isinstance(metadata, dict) else None
    if not isinstance(origin, dict):
        return None
    recorded = str(origin.get("original_path") or "").strip()
    return Path(recorded).expanduser() if recorded else None


def _stem(source: Path) -> str:
    return _UNSAFE.sub("_", source.stem).strip() or "recording"


def _owner(folder: Path) -> str:
    try:
        return (folder / MARKER_NAME).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def claim(task_id: str, source: Path) -> Path:
    """This task's folder beside ``source``: the one it already marked, else the
    first name not taken, created and marked now."""
    base = source.parent / _stem(source)
    candidates = [base] + [base.with_name(f"{base.name} ({n})") for n in range(2, MAX_FOLDERS_PER_RECORDING + 1)]
    for candidate in candidates:
        if candidate.is_dir() and _owner(candidate) == task_id:
            return candidate
    for candidate in candidates:
        if not candidate.exists():
            candidate.mkdir(parents=False)
            (candidate / MARKER_NAME).write_text(task_id + "\n", encoding="utf-8")
            return candidate
    raise OutputFolderError(f"「{base.name}」开头的文件夹太多了，先清理一下：{source.parent}")


# ── putting files in it ─────────────────────────────────────────────────────

def _link_or_copy(src: Path, target: Path) -> str:
    """Put ``src`` at ``target`` as a hard link (one file, two names) or a copy.
    ``target`` is inside this task's own folder, so an earlier one is replaced."""
    if target.exists():
        try:
            if os.path.samefile(src, target):
                return "hard_link"
        except OSError:
            pass
        target.unlink()
    try:
        os.link(src, target)
        return "hard_link"
    except OSError as exc:
        # Different filesystems, or one without hard links: copying is the way.
        logger.info("hard link not possible for %s (%s); copying", target, exc)
    shutil.copyfile(src, target)
    return "copy"


def deliver_media(task_id: str, source: Path, media: Path, label: str) -> dict[str, Any]:
    """Put a finished video (the cut, or the compressed copy) in the task's folder.

    Returns the fields the cut state has always carried (``delivered_path`` and
    so on) plus where the folder is."""
    try:
        folder = claim(task_id, source)
        target = folder / f"{_stem(source)}_{label}{media.suffix.lower() or '.mp4'}"
        how = _link_or_copy(media, target)
    except (OSError, OutputFolderError) as exc:
        logger.warning("could not put %s in the output folder for %s: %s", media, source, exc)
        return {
            "delivered": False,
            "delivery_error": f"剪后文件没能放进原文件旁边的文件夹（{exc}）。任务里那份还在，可以从页面下载。",
        }
    return {
        "delivered": True,
        "delivered_path": str(target),
        "delivered_name": target.name,
        "delivered_as": how,
        "output_folder": str(folder),
    }


def _write_if_changed(target: Path, text: str) -> bool:
    try:
        if target.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    target.write_text(text, encoding="utf-8")
    return True


def _artifact_file(task_id: str, record: Any) -> Path | None:
    if not isinstance(record, dict):
        return None
    name = str(record.get("filename") or "").strip()
    if not name or ".." in Path(name).parts:
        return None
    path = _artifact_storage_dir() / task_id / name
    return path if path.is_file() else None


def frame_file(task_id: str, src: str) -> Path | None:
    """The screenshot file an image reference in a note points at, or None.

    A note refers to its screenshots as this app's artifact URLs
    (``/jobs/<id>/artifacts/frame?file=<name>``) or, in a frame note, by name."""
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(src)
    name = ""
    if parsed.path.endswith(f"/jobs/{task_id}/artifacts/frame") or parsed.path == f"/jobs/{task_id}/artifacts/frame":
        name = (parse_qs(parsed.query or "").get("file") or [""])[0].strip()
    elif not parsed.scheme and "/" not in src and src.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        name = src
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    path = _artifact_storage_dir() / task_id / "frames" / name
    return path if path.is_file() else None


def _note_for_folder(task_id: str, note: str, shots: Path) -> tuple[str, list[Path]]:
    """The note with each screenshot pointing at ``截图/<file>``, and the files to copy."""
    files: list[Path] = []

    def local(match: re.Match[str]) -> str:
        path = frame_file(task_id, match.group(2))
        if path is None:
            return match.group(0)
        files.append(path)
        return f"{match.group(1)}{SHOTS_DIRNAME}/{path.name}{match.group(3)}"

    return _IMAGE.sub(local, note), files


# ── keeping it in step with the task ────────────────────────────────────────

# One sync at a time: the end of a task and a note saved at that moment would
# otherwise both see a stale record and export the PDF twice.
_SYNC_LOCK = threading.Lock()


def sync(task_id: str) -> dict[str, Any] | None:
    """Bring the task's folder up to date with what the task has now.

    Returns what it recorded on the task, or None for a task with no folder of
    the user's to deliver into. Never raises: the task's own results stand."""
    with _SYNC_LOCK:
        return _sync_locked(task_id)


def _sync_locked(task_id: str) -> dict[str, Any] | None:
    job = job_store.get_job(task_id)
    source = original_path(job)
    if job is None or source is None:
        return None
    result = dict(job.get("result") or {}) if isinstance(job.get("result"), dict) else {}
    try:
        record = _sync_files(task_id, source, result)
    except Exception as exc:  # noqa: BLE001 - the task is finished whatever this does
        logger.warning("output folder for %s not updated: %s", task_id, exc)
        previous = result.get(RESULT_KEY) if isinstance(result.get(RESULT_KEY), dict) else {}
        record = {**previous, "error": f"产出没能整理进原文件旁边的文件夹（{exc}）。任务里的结果都还在。"}
    _record(task_id, record)
    return record


def _sync_files(task_id: str, source: Path, result: dict[str, Any]) -> dict[str, Any]:
    folder = claim(task_id, source)
    process = folder / PROCESS_DIRNAME
    process.mkdir(exist_ok=True)
    stem = _stem(source)
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}

    for kind, suffix in (("transcript_txt", ".txt"), ("transcript_srt", ".srt")):
        path = _artifact_file(task_id, artifacts.get(kind))
        if path is not None:
            shutil.copyfile(path, process / f"{stem}{suffix}")
    cut_list = _artifact_file(task_id, artifacts.get("debreath_cut_list"))
    if cut_list is not None:
        shutil.copyfile(cut_list, process / f"{stem}_{CUT_LIST_LABEL}.json")

    record: dict[str, Any] = {"path": str(folder), "error": None}
    note = str(result.get("summary_markdown") or "").strip()
    if note:
        shots = process / SHOTS_DIRNAME
        text, files = _note_for_folder(task_id, note, shots)
        if files:
            shots.mkdir(exist_ok=True)
            for path in files:
                target = shots / path.name
                if not target.exists():
                    shutil.copyfile(path, target)
        changed = _write_if_changed(process / f"{stem}_{NOTE_LABEL}.md", text.rstrip() + "\n")
        pdf = folder / f"{stem}_{NOTE_LABEL}.pdf"
        digest = hashlib.sha256(note.encode("utf-8")).hexdigest()
        previous = result.get(RESULT_KEY) if isinstance(result.get(RESULT_KEY), dict) else {}
        if changed or not pdf.is_file() or previous.get("note_digest") != digest:
            record.update(_export_pdf(task_id, folder, f"{stem}_{NOTE_LABEL}"))
        else:
            record["pdf_path"] = str(pdf)
        record["note_digest"] = digest
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    return record


def _export_pdf(task_id: str, folder: Path, name: str) -> dict[str, Any]:
    from backend.core import note_pdf

    try:
        exported = note_pdf.export_pdf(task_id, output_dir=str(folder), filename=name)
    except Exception as exc:  # noqa: BLE001 - the markdown note is already there
        return {"pdf_path": None, "pdf_error": f"PDF 没生成（{exc}）。笔记的 md 版本在「{PROCESS_DIRNAME}」里。"}
    if exported.get("pdf_path"):
        return {"pdf_path": exported["pdf_path"], "pdf_error": None}
    return {"pdf_path": None, "pdf_error": exported.get("reason") or "PDF 没生成。"}


def _record(task_id: str, record: dict[str, Any]) -> None:
    job = job_store.get_job(task_id)
    if not job:
        return
    result = dict(job.get("result") or {}) if isinstance(job.get("result"), dict) else {}
    result[RESULT_KEY] = record
    job_store.update_job_result(task_id, result, touch_updated_at=False)


# A note that changes after the task ended (rewritten, regenerated, edited and
# saved) refreshes the folder. One worker, and a task already waiting is not
# queued twice: an editor saving every few seconds must not stack PDF exports.
_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="output-folder")
_PENDING: set[str] = set()
_PENDING_LOCK = threading.Lock()


def _run_pending(task_id: str) -> None:
    with _PENDING_LOCK:
        _PENDING.discard(task_id)
    sync(task_id)


def schedule_sync(task_id: str) -> None:
    with _PENDING_LOCK:
        if task_id in _PENDING:
            return
        _PENDING.add(task_id)
    _WORKER.submit(_run_pending, task_id)


def _on_result_saved(task_id: str, before: dict[str, Any] | None, after: dict[str, Any] | None) -> None:
    """Refresh a folder that already exists when the task's note changed."""
    if not isinstance(after, dict) or not isinstance(after.get(RESULT_KEY), dict):
        return
    previous = (before or {}).get("summary_markdown") if isinstance(before, dict) else None
    if str(after.get("summary_markdown") or "") != str(previous or ""):
        schedule_sync(task_id)


job_store.add_result_listener(_on_result_saved)


__all__ = [
    "CUT_LABEL",
    "COMPRESSED_LABEL",
    "MARKER_NAME",
    "OutputFolderError",
    "PROCESS_DIRNAME",
    "claim",
    "deliver_media",
    "frame_file",
    "original_path",
    "schedule_sync",
    "sync",
]
