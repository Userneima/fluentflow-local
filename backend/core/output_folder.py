"""Every output of a recording from the user's own folder, in one folder beside it.

Requirement (owner, 2026-10-14). A recording the user pointed at on their disk
gets a folder named after it, next to it; the original is not moved:

    <name>/
        <name>_去气口.mp4      the cut video, or <name>_压缩.mp4 when the cut was declined
        <name>_笔记.pdf
        <name>_笔记.md          the same note, best read in Obsidian (owner, 2026-10-18)
        过程文件/
            截图/                  the screenshots the note shows
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
- A video downloaded from a link has no folder of the user's to sit beside, so
  it gets one under ``~/Movies/FluentFlow/`` (``FLUENTFLOW_LINK_OUTPUT_DIR``),
  named after the video, with the downloaded original in it as well (owner,
  2026-10-15): ``<title>/<title>.mp4`` next to the cut, the note and
  ``过程文件``. Uploads are left in FluentFlow.
- A folder that cannot be written does not fail the task; the reason is kept on
  the task.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import sys
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
# Said wherever the folder is named to the user, so an agent passes it on.
MD_HINT = "笔记的 md 版推荐用 Obsidian 打开，截图和格式都能直接显示。"
SHOTS_DIRNAME = "截图"
CUT_LABEL = "去气口"
COMPRESSED_LABEL = "压缩"
NOTE_LABEL = "笔记"
CUT_LIST_LABEL = "剪辑表"
RESULT_KEY = "output_folder"
LINK_ROOT_ENV = "FLUENTFLOW_LINK_OUTPUT_DIR"
# The user's own video folder: "Movies" on a Mac, "Videos" on Windows and
# Linux, where a "Movies" folder would be a stray new one (owner, 2026-10-16).
DEFAULT_LINK_ROOT = "~/Movies/FluentFlow"
DEFAULT_LINK_ROOT_ELSEWHERE = "~/Videos/FluentFlow"
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


def windows_videos_folder() -> Path | None:
    """Where this Windows user's Videos folder really is.

    Windows lets the user move it (Properties → Location), and it often lives on
    another drive: on the owner's machine it is D:/视频, with an empty
    C:/Users/<name>/Videos left behind that Explorer never shows. Asked of
    the system (SHGetKnownFolderPath, FOLDERID_Videos); None when it cannot say."""
    try:
        import ctypes
        from ctypes import wintypes
        import uuid

        class _Guid(ctypes.Structure):
            _fields_ = [("data1", wintypes.DWORD), ("data2", wintypes.WORD),
                        ("data3", wintypes.WORD), ("data4", ctypes.c_ubyte * 8)]

        raw = uuid.UUID("18989B1D-99B5-455B-841C-AB7C74E4DDFC").bytes_le
        folder_id = _Guid.from_buffer_copy(raw)
        path_ptr = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32
        if shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(path_ptr)) != 0:
            return None
        try:
            return Path(path_ptr.value) if path_ptr.value else None
        finally:
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)
    except (AttributeError, OSError, ValueError):
        return None


def link_root(platform: str | None = None) -> Path:
    configured = os.environ.get(LINK_ROOT_ENV)
    if configured:
        return Path(configured).expanduser()
    current = platform or sys.platform
    if current == "darwin":
        return Path(DEFAULT_LINK_ROOT).expanduser()
    if current == "win32":
        videos = windows_videos_folder()
        if videos is not None:
            return videos / "FluentFlow"
    return Path(DEFAULT_LINK_ROOT_ELSEWHERE).expanduser()


def _is_link_task(job: dict[str, Any] | None) -> bool:
    metadata = (job or {}).get("metadata")
    return isinstance(metadata, dict) and bool(str(metadata.get("video_source_url") or "").strip())


def _link_original(task_id: str, job: dict[str, Any]) -> Path | None:
    """For a video downloaded from a link: its folder under ``link_root()``
    and the downloaded original in it, put there now if it is not yet. The
    path of that original, which then plays the part of the recording the
    folder sits around. None when the download is not kept (a link that was
    served as subtitles only, or a source already cleaned up)."""
    from backend.core.storage_paths import find_source_file, in_place_source_path

    downloaded = find_source_file(task_id)
    if downloaded is None:
        # FluentFlow's own copy was cleaned up; the one in the folder stays.
        return in_place_source_path(job)
    if downloaded.suffix.lower() in {".srt", ".vtt", ".txt", ".json"}:
        return None
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    title = str(metadata.get("display_title") or metadata.get("raw_title") or job.get("source_filename") or "").strip()
    stem = _UNSAFE.sub("_", title).strip().strip(".") or f"video-{task_id[:8]}"
    root = link_root()
    root.mkdir(parents=True, exist_ok=True)
    names = [stem] + [f"{stem} ({n})" for n in range(2, MAX_FOLDERS_PER_RECORDING + 1)]
    # Renamed by the user, it is still found by its marker.
    folder = next((child for child in sorted(root.iterdir()) if child.is_dir() and _owner(child) == task_id), None)
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    previous = (result.get(RESULT_KEY) or {}).get("path") if isinstance(result.get(RESULT_KEY), dict) else None
    if folder is None and previous:
        raise OutputFolderError(
            f"产出文件夹不在原来的位置了（原来在 {previous}），没有再新建一个。"
            "挪回原处后会继续更新；最新的结果都还在 FluentFlow 里。"
        )
    if folder is None:
        folder = next((root / name for name in names if not (root / name).exists()), None)
        if folder is None:
            raise OutputFolderError(f"「{stem}」开头的文件夹太多了，先清理一下：{root}")
        folder.mkdir()
        _add_owner(folder, task_id, "")
    original = folder / f"{folder.name}{downloaded.suffix.lower()}"
    if not original.exists():
        _link_or_copy(downloaded, original)
    if str(metadata.get("kept_original_path") or "") != str(original):
        job_store.upsert_job(
            task_id=task_id, status=str(job.get("status") or "completed"),
            metadata={**metadata, "kept_original_path": str(original)},
        )
    return original


def _stem(source: Path) -> str:
    return _UNSAFE.sub("_", source.stem).strip() or "recording"


def _owners(folder: Path) -> list[tuple[str, str]]:
    """(task id, name suffix) for each task that has put files in ``folder``.

    The marker holds one line per task: the id, and after a tab the suffix its
    file names carry (empty for the first task)."""
    try:
        lines = (folder / MARKER_NAME).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    owners = []
    for line in lines:
        task, _, suffix = line.partition("\t")
        if task.strip():
            owners.append((task.strip(), suffix))
    return owners


def _owner(folder: Path) -> str:
    owners = _owners(folder)
    return owners[0][0] if owners else ""


def _add_owner(folder: Path, task_id: str, suffix: str) -> None:
    with (folder / MARKER_NAME).open("a", encoding="utf-8") as handle:
        handle.write(f"{task_id}\t{suffix}\n" if suffix else f"{task_id}\n")


def _lives_in_its_own_folder(source: Path) -> bool:
    """The user moved the recording into the folder made for it (found
    2026-10-14). Its outputs go into that folder rather than a new one nested
    inside it."""
    return source.parent.name == _stem(source)


def _taken(folder: Path, stem: str, suffix: str) -> bool:
    names = [f"{stem}_{label}{suffix}" for label in (CUT_LABEL, COMPRESSED_LABEL, NOTE_LABEL)]
    return any(path.stem in names for path in folder.iterdir() if path.is_file())


def place(task_id: str, source: Path) -> tuple[Path, str]:
    """This task's folder, and the suffix its file names carry there.

    Beside the recording: the folder named after it that this task marked, else
    the first such name not taken (``<name> (2)`` ...), created and marked now;
    file names carry no suffix. When the recording already sits in its own
    folder, that folder is shared by every task on it, and a later task's files
    carry `` (2)``, `` (3)``... so nothing an earlier task left is overwritten."""
    stem = _stem(source)
    if _lives_in_its_own_folder(source):
        folder = source.parent
        for task, suffix in _owners(folder):
            if task == task_id:
                return folder, suffix
        used = {suffix for _task, suffix in _owners(folder)}
        for n in range(1, MAX_FOLDERS_PER_RECORDING + 1):
            suffix = "" if n == 1 else f" ({n})"
            if suffix not in used and not _taken(folder, stem, suffix):
                _add_owner(folder, task_id, suffix)
                return folder, suffix
        raise OutputFolderError(f"这个文件夹里的结果版本太多了，先清理一下：{folder}")
    base = source.parent / stem
    candidates = [base] + [base.with_name(f"{base.name} ({n})") for n in range(2, MAX_FOLDERS_PER_RECORDING + 1)]
    for candidate in candidates:
        if candidate.is_dir() and _owner(candidate) == task_id:
            return candidate, ""
    for candidate in candidates:
        if not candidate.exists():
            candidate.mkdir(parents=False)
            _add_owner(candidate, task_id, "")
            return candidate, ""
    raise OutputFolderError(f"「{base.name}」开头的文件夹太多了，先清理一下：{source.parent}")


def _folder_for(task_id: str, source: Path) -> tuple[Path, str]:
    """The folder this task already filled, wherever it is now, else a new one
    (``place``).

    Once a task has a folder the user may rename or move it. Renamed in place
    it is found again by its marker; moved somewhere unknown it is not
    recreated beside the recording, which would leave the user two folders
    for one recording (owner, 2026-10-15)."""
    job = job_store.get_job(task_id) or {}
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    previous = str((result.get(RESULT_KEY) or {}).get("path") or "") if isinstance(result.get(RESULT_KEY), dict) else ""
    if not previous:
        return place(task_id, source)
    known = Path(previous)
    for folder in [known] + [
        child for parent in dict.fromkeys([known.parent, source.parent]) if parent.is_dir()
        for child in sorted(parent.iterdir()) if child.is_dir()
    ]:
        if folder.is_dir():
            for task, suffix in _owners(folder):
                if task == task_id:
                    return folder, suffix
    raise OutputFolderError(
        f"产出文件夹不在原来的位置了（原来在 {previous}），没有再新建一个。"
        "挪回原处后会继续更新；最新的结果都还在 FluentFlow 里。"
    )


def claim(task_id: str, source: Path) -> Path:
    return _folder_for(task_id, source)[0]


def file_name(source: Path, label: str, suffix: str, extension: str) -> str:
    return f"{_stem(source)}_{label}{suffix}{extension}"


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
        folder, suffix = _folder_for(task_id, source)
        target = folder / file_name(source, label, suffix, media.suffix.lower() or ".mp4")
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


def _note_for_folder(task_id: str, note: str, shots: Path, *, relative_to: Path | None = None) -> tuple[str, list[Path]]:
    """The note with each screenshot pointing at the shots dir as seen from
    where the note sits (``relative_to``), and the files to copy."""
    files: list[Path] = []
    prefix = shots.relative_to(relative_to).as_posix() if relative_to is not None else shots.name

    def local(match: re.Match[str]) -> str:
        path = frame_file(task_id, match.group(2))
        if path is None:
            return match.group(0)
        files.append(path)
        return f"{match.group(1)}{prefix}/{path.name}{match.group(3)}"

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
    if job is None:
        return None
    link = _is_link_task(job) and original_path(job) is None
    result = dict(job.get("result") or {}) if isinstance(job.get("result"), dict) else {}
    try:
        source = _link_original(task_id, job) if link else original_path(job)
        if source is None:
            return None
        record = _sync_files(task_id, source, result)
        if link:
            record.update(_deliver_cut(task_id, source, result))
    except Exception as exc:  # noqa: BLE001 - the task is finished whatever this does
        logger.warning("output folder for %s not updated: %s", task_id, exc)
        previous = result.get(RESULT_KEY) if isinstance(result.get(RESULT_KEY), dict) else {}
        record = {**previous, "error": f"产出没能整理进原文件旁边的文件夹（{exc}）。任务里的结果都还在。"}
    _record(task_id, record)
    return record


def _sync_files(task_id: str, source: Path, result: dict[str, Any]) -> dict[str, Any]:
    folder, suffix = _folder_for(task_id, source)
    process = folder / PROCESS_DIRNAME
    process.mkdir(exist_ok=True)
    stem = _stem(source)
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}

    for kind, extension in (("transcript_txt", ".txt"), ("transcript_srt", ".srt")):
        path = _artifact_file(task_id, artifacts.get(kind))
        if path is not None:
            shutil.copyfile(path, process / f"{stem}{suffix}{extension}")
    cut_list = _artifact_file(task_id, artifacts.get("debreath_cut_list"))
    if cut_list is not None:
        shutil.copyfile(cut_list, process / f"{stem}_{CUT_LIST_LABEL}{suffix}.json")

    record: dict[str, Any] = {"path": str(folder), "error": None}
    note = str(result.get("summary_markdown") or "").strip()
    if note:
        # Per task when tasks share a folder: two notes' note_0001.jpg are different pictures.
        shots = process / f"{SHOTS_DIRNAME}{suffix}"
        # The markdown note sits beside the PDF, not in 过程文件: read in
        # Obsidian it shows its screenshots and is the copy people open
        # (owner, 2026-10-18). Its pictures stay in 过程文件.
        text, files = _note_for_folder(task_id, note, shots, relative_to=folder)
        if files:
            shots.mkdir(exist_ok=True)
            for path in files:
                target = shots / path.name
                if not target.exists():
                    shutil.copyfile(path, target)
        md = folder / f"{stem}_{NOTE_LABEL}{suffix}.md"
        changed = _write_if_changed(md, text.rstrip() + "\n")
        # The copy an earlier version kept in 过程文件 would now be a stale twin.
        (process / md.name).unlink(missing_ok=True)
        record["md_path"] = str(md)
        record["md_hint"] = MD_HINT
        pdf = folder / f"{stem}_{NOTE_LABEL}{suffix}.pdf"
        digest = hashlib.sha256(note.encode("utf-8")).hexdigest()
        previous = result.get(RESULT_KEY) if isinstance(result.get(RESULT_KEY), dict) else {}
        if changed or not pdf.is_file() or previous.get("note_digest") != digest:
            record.update(_export_pdf(task_id, folder, f"{stem}_{NOTE_LABEL}{suffix}"))
        else:
            record["pdf_path"] = str(pdf)
        record["note_digest"] = digest
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    return record


def _deliver_cut(task_id: str, source: Path, result: dict[str, Any]) -> dict[str, Any]:
    """A link's cut video, which nothing else puts in the folder (a local
    recording's is delivered as soon as it is rendered)."""
    from backend.core import debreath_job

    media = debreath_job.cut_file_on_disk(task_id, result)
    if media is None:
        return {}
    delivered = deliver_media(task_id, source, media, CUT_LABEL)
    return {"cut_path": delivered.get("delivered_path")} if delivered.get("delivered") else {}


def _export_pdf(task_id: str, folder: Path, name: str) -> dict[str, Any]:
    from backend.core import note_pdf

    try:
        exported = note_pdf.export_pdf(task_id, output_dir=str(folder), filename=name)
    except Exception as exc:  # noqa: BLE001 - the markdown note is already there
        return {"pdf_path": None, "pdf_error": f"PDF 没生成（{exc}）。笔记的 md 版本就在同一个文件夹里。"}
    if exported.get("pdf_path"):
        return {"pdf_path": exported["pdf_path"], "pdf_error": None}
    return {"pdf_path": None, "pdf_error": exported.get("reason") or "PDF 没生成。"}


def _record(task_id: str, record: dict[str, Any]) -> None:
    job = job_store.get_job(task_id)
    if not job:
        return
    before = dict(job.get("result") or {}) if isinstance(job.get("result"), dict) else {}
    job_store.save_result_changes(task_id, before, {**before, RESULT_KEY: record}, touch_updated_at=False)


# A note or subtitles that change after the task ended (rewritten, regenerated,
# edited and saved) refresh the folder. One worker, and a task already waiting is not
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


def _what_the_folder_holds(result: dict[str, Any] | None) -> tuple[Any, ...]:
    """Everything the folder carries a copy of: the note, the transcript and
    subtitles, the cut list. Subtitles saved in the editor used to stay old in
    the folder because only the note was watched (read-only review, 2026-10-16)."""
    if not isinstance(result, dict):
        return ()
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
    return (
        str(result.get("summary_markdown") or ""),
        str(result.get("transcript_text") or ""),
        # Name and size, not the record's time: that is rewritten with every
        # save of the task and would refresh the folder for nothing.
        *(
            (record.get("filename"), record.get("size_bytes")) if isinstance(record, dict) else None
            for record in (artifacts.get(kind) for kind in ("transcript_txt", "transcript_srt", "debreath_cut_list"))
        ),
    )


def _on_result_saved(task_id: str, before: dict[str, Any] | None, after: dict[str, Any] | None) -> None:
    """Refresh a folder that already exists when what it holds changed."""
    if not isinstance(after, dict) or not isinstance(after.get(RESULT_KEY), dict):
        return
    if _what_the_folder_holds(after) != _what_the_folder_holds(before):
        schedule_sync(task_id)


job_store.add_result_listener(_on_result_saved)


__all__ = [
    "CUT_LABEL",
    "COMPRESSED_LABEL",
    "MARKER_NAME",
    "OutputFolderError",
    "PROCESS_DIRNAME",
    "claim",
    "file_name",
    "place",
    "deliver_media",
    "frame_file",
    "original_path",
    "schedule_sync",
    "sync",
]
