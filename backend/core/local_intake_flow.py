"""What the local edition does to a recording on its own, without being asked.

The product decision behind this file (owner, 2026-08-12): choosing a video to
make a note from is one action, not three. So the local edition removes the
breath gaps, transcribes the shortened audio, and writes the note from that same
shortened file — and the old step that produced a note from the full recording's
transcript first is gone from this path. Nothing is left for the user to click in
the ordinary case.

Two consequences are worth stating plainly, because automation moved both of
them out of the user's hands:

- **Order matters, and cutting first is the reason nothing has to be remapped.**
  The transcript is made *from* the shortened audio, so its timestamps are that
  file's from the start. The page plays that file, the note quotes that file, the
  subtitles fit that file. The alternative order needs a mapping layer between
  every pair of surfaces, and a mapping that is wrong is invisible.
- **Cutting first can destroy words, so it is allowed to decline.** Whatever the
  cut removes, the transcript will never contain, and recovering it costs a whole
  re-transcription. ``debreath_job.prepare_cut_media`` therefore falls back to the
  recording whenever its own spot check says the cuts sit close to the speech, and
  on any failure at all. This module never turns that into a failed upload.

The note runs after the transcript is stored rather than inside the pipeline's own
note stage. That is a real seam and it is visible: the task reaches "completed"
with its transcript, and the note lands about a minute later while the result says
it is still being written. Folding it into the pipeline's progress would mean
either teaching the note job not to persist as it goes or rebuilding the pipeline's
note stage around it — both larger than this, and neither changes what the user
gets.

Both steps are switchable, because both spend something the user might not want
spent on a given machine: minutes of CPU, and the Claude allowance.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.core import debreath_job, local_folder_intake, visual_note_channel, visual_note_job
from backend.core.job_store import get_job, update_job_result, upsert_job
from backend.core.local_keyframe_provider import extract_keyframes
from backend.core.storage_cleanup import is_managed_path
from backend.core.local_config import resolve_secret

logger = logging.getLogger(__name__)

CUT_FIRST_ENV = "FLUENTFLOW_LOCAL_CUT_FIRST"
AUTO_NOTE_ENV = "FLUENTFLOW_LOCAL_AUTO_NOTE"

_OFF = {"0", "false", "no", "off", "disabled"}


def _enabled(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() not in _OFF


def cut_first_enabled() -> bool:
    """Whether an upload gets its breath gaps removed before anything reads it.

    On by default. Off means the recording is transcribed as it is and the cut
    stays a button on the result page — which is what a machine without spare CPU,
    or somebody working on material they do not want touched, should set.
    """
    return _enabled(CUT_FIRST_ENV)


def auto_note_enabled() -> bool:
    """Whether the note is written without being asked.

    On by default, and it spends the Claude allowance of whoever's login this
    machine holds. Off leaves the transcript and the cut file, with the note one
    click away on the result page — the same entry, unchanged.
    """
    return _enabled(AUTO_NOTE_ENV)


def _deliver_beside_original(source: Path, cut: Path) -> dict[str, Any]:
    """Put a copy of the cut file next to the recording it came from.

    Only for a recording the user pointed at in their own folder — for an upload
    the "original" is FluentFlow's own copy, and delivering beside it would mean
    delivering into the store, which is where it already is.

    Copies rather than moves: the task keeps its own artifact, so the file stays
    downloadable from the page and a later cleanup of the store cannot take the
    user's copy with it. Never overwrites; ``cut_file_target`` picks the next free
    name and this records which one it used, because a silently renamed output the
    user cannot find is the same problem as no output at all.
    """
    try:
        target = local_folder_intake.cut_file_target(source)
        shutil.copyfile(cut, target)
    except (OSError, local_folder_intake.FolderIntakeError) as exc:
        logger.warning("could not deliver the cut file next to %s: %s", source, exc)
        return {
            "delivered": False,
            "delivery_error": f"剪后文件没能存到原文件旁边（{exc}）。任务里那份还在，可以从页面下载。",
        }
    return {"delivered": True, "delivered_path": str(target), "delivered_name": target.name}


def preprocess_media(task_id: str, source: Path) -> debreath_job.PreparedCut | None:
    """The pipeline's media-preparation hook: cut the gaps, or hand back the file.

    Returning ``None`` when switched off leaves the pipeline exactly as it was.
    """
    if not cut_first_enabled():
        return None
    path = Path(source)
    prepared = debreath_job.prepare_cut_media(task_id, path)
    # A recording inside FluentFlow's own storage came from an upload; one outside
    # it is a file in the user's folder, and that is the case with somewhere to
    # deliver to.
    if prepared.used and not is_managed_path(path):
        return debreath_job.PreparedCut(
            path=prepared.path,
            used=prepared.used,
            state={**prepared.state, **_deliver_beside_original(path, prepared.path)},
            artifacts=prepared.artifacts,
        )
    return prepared


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _patch_result(task_id: str, client_id: str | None, fields: dict[str, Any]) -> dict[str, Any] | None:
    job = get_job(task_id, client_id=client_id)
    if not job:
        return None
    result = dict(job.get("result") or {})
    result.update(fields)
    return update_job_result(task_id, result, client_id=client_id)


def auto_note_will_run() -> bool:
    """Whether the automatic note will actually produce something.

    The switch being on is not enough. The note it writes is Claude reading the
    frames, and a machine with no Anthropic key and no subscription channel
    cannot do that — so on a fresh install the switch alone meant no note at
    all: the pipeline's own text note had been turned off to avoid writing two,
    and the visual one then refused.

    Asked before the job starts, because that is when the pipeline decides
    whether to write the text note itself. When this is False the user gets the
    text note from whichever provider they configured, which is a smaller thing
    than the visual note and says so, rather than nothing.
    """
    if not auto_note_enabled():
        return False
    return visual_note_channel.resolve_channel(
        resolve_secret(None, "anthropic_api_key")
    ).available


def note_is_wanted(task_id: str, client_id: str | None) -> bool:
    """Whether this finished task should get a note written for it now.

    A task the caller asked to leave without a note is left without one: the
    automation replaces the old note step, it does not override an instruction.
    """
    if not auto_note_will_run():
        return False
    job = get_job(task_id, client_id=client_id)
    if not job or str(job.get("status") or "") != "completed":
        return False
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    if str(result.get("summary_markdown") or "").strip():
        return False
    return bool(result.get("transcript_text") or result.get("raw_segments"))


def mark_note_running(task_id: str, client_id: str | None) -> None:
    """Say the note is on its way, so the page does not read as "no note".

    Without this the result carries the pipeline's skipped-note state and the
    editor says the note was skipped — which would be a plain lie for about a
    minute, and the kind that makes a user go looking for a button.
    """
    _patch_result(task_id, client_id, {
        "summary_skipped": False,
        "summary_status": "pending",
        "summary_error": None,
    })
    upsert_job(task_id=task_id, status="completed", stage="note", summary_status="pending")


def write_note(task_id: str, client_id: str | None) -> None:
    """Write the task's note from the file the transcript came from.

    Blocking: run it in a thread. Failures are recorded on the result in the
    user's words and never raised at the caller — the transcript and the cut file
    are already theirs, and losing those to a note failure would be the worst
    possible trade.
    """
    try:
        visual_note_job.claim(task_id)
    except visual_note_job.VisualNoteError:
        return
    try:
        visual_note_job.run_visual_note(
            task_id,
            client_id=client_id,
            api_key=resolve_secret(None, "anthropic_api_key"),
            claimed=True,
            replace_note=True,
            keyframe_extractor=extract_keyframes,
        )
        upsert_job(task_id=task_id, status="completed", stage="done", summary_status="completed")
    except visual_note_job.VisualNoteError as exc:
        # The one place the automation has to speak to a user: the note did not
        # get written, and what to do about it. `visual_note` already carries the
        # same sentence; this puts it where the note is supposed to be.
        _patch_result(task_id, client_id, {
            "summary_status": "failed",
            "summary_error": f"{exc}",
            "summary_skipped": False,
        })
        upsert_job(task_id=task_id, status="completed", stage="done", summary_status="failed")
        logger.warning("automatic note failed for %s: %s", task_id, exc)
    except Exception as exc:  # noqa: BLE001 - a note must not take the task with it
        _patch_result(task_id, client_id, {
            "summary_status": "failed",
            "summary_error": f"笔记生成失败：{type(exc).__name__}: {exc}",
            "summary_skipped": False,
        })
        upsert_job(task_id=task_id, status="completed", stage="done", summary_status="failed")
        logger.exception("automatic note failed for %s", task_id)
    finally:
        visual_note_job.release(task_id)


__all__ = [
    "AUTO_NOTE_ENV",
    "CUT_FIRST_ENV",
    "auto_note_enabled",
    "auto_note_will_run",
    "cut_first_enabled",
    "mark_note_running",
    "note_is_wanted",
    "preprocess_media",
    "write_note",
]
