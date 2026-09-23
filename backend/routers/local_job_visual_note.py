"""Local route for the second half of the flow: the note from the cut file.

Which Claude pays is decided by ``visual_note_channel``, not here: by default the
Claude Code login already on this machine, so ordinary use configures no
credential at all. The key resolved below is the fallback for a machine without
Claude Code: the user's own, read from the backend-owned local config or from
``ANTHROPIC_API_KEY`` in the project's .env, passed to the SDK, and never written
into a job result, an event, or a log line. Frames come from ffmpeg on this
machine through ``local_keyframe_provider``, or are disabled.

The entry has four shapes and only one of them spends anything.

- ``preview`` answers "would this work, what would it send, who pays, and what
  happens to the note I have" for free: which file it would read, how much
  transcript after the timestamps are moved onto that file's clock, the frame
  budget (zero for audio), the Claude channel, and when it would not work, the
  one sentence saying why. This is the cheap half and the one to reach for first.
- ``restore_previous_note`` puts back the note a run replaced. Free.
- ``use_generated_note`` makes an already-written note the task's note again.
  Free, and it exists so changing one's mind does not cost a model call.
- A plain call extracts frames and writes the note, and by default that note
  becomes the task's note. ``replace_note: false`` keeps it beside the existing
  one instead, for a caller that wants to compare before committing.

Refusals happen in the response the caller is waiting on, not several minutes
into a background task whose only trace is a status field: a task that is not
finished, no cut file to read yet, a cut file or cut list no longer on the
machine, a missing Anthropic credential, and a run already going.

The slot is claimed here rather than inside the worker. FastAPI runs background
tasks after the response is sent, so two quick submissions could both pass an
"is anything running?" read; the second would then be refused inside a worker
nobody is watching, having already answered "accepted".
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Request

from backend.core import visual_note_job
from backend.core.job_store import get_job
from backend.core.local_config import resolve_secret
from backend.core.local_keyframe_provider import extract_keyframes
from backend.core.local_request_scope import request_client_id

logger = logging.getLogger(__name__)

router = APIRouter()


def _local_client_scope(request: Request) -> Optional[str]:
    return request_client_id(request) or "anonymous"


def start_local_visual_note(
    request: Request,
    task_id: str,
    background_tasks: BackgroundTasks,
    payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Preview, switch, or accept a note run for one task — or refuse, here.

    The local Agent API calls this too, so an agent goes through the same guards
    the page does rather than a parallel implementation that can drift.
    """
    client_id = _local_client_scope(request)
    job = get_job(task_id, client_id=client_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    api_key = resolve_secret(None, "anthropic_api_key")
    body = payload or {}

    if bool(body.get("preview")):
        return {
            "ok": True,
            "preview": True,
            **visual_note_job.describe(task_id, job, api_key=api_key),
        }

    # Choosing between two notes that already exist costs nothing and reaches no
    # outside service, so neither switch goes near the eligibility checks or the
    # run slot.
    for flag, action in (
        ("restore_previous_note", visual_note_job.restore_previous_note),
        ("use_generated_note", visual_note_job.use_generated_note),
    ):
        if not bool(body.get(flag)):
            continue
        try:
            updated = action(task_id, client_id=client_id)
        except visual_note_job.VisualNoteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "ok": True,
            "task_id": task_id,
            "accepted": False,
            "preview": False,
            flag: True,
            "result": updated.get("result"),
        }

    described = visual_note_job.describe(task_id, job, api_key=api_key)
    if not described["eligible"]:
        raise HTTPException(status_code=409, detail=str(described["reason"]))
    replace_note = bool(body.get("replace_note", True))
    try:
        visual_note_job.claim(task_id)
    except visual_note_job.VisualNoteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    def _run() -> None:
        try:
            visual_note_job.run_visual_note(
                task_id,
                client_id=client_id,
                api_key=api_key,
                claimed=True,
                replace_note=replace_note,
                keyframe_extractor=extract_keyframes,
            )
        except visual_note_job.VisualNoteError as exc:
            logger.warning("visual note refused for %s: %s", task_id, exc)
        except Exception:  # noqa: BLE001 - already recorded on the job result
            logger.exception("visual note failed for %s", task_id)
        finally:
            # run_visual_note releases its own slot; this covers the case where
            # it never got to start, so the slot cannot leak.
            visual_note_job.release(task_id)

    background_tasks.add_task(_run)
    return {
        "ok": True,
        "task_id": task_id,
        "accepted": True,
        "preview": False,
        "model": described["model"],
        "channel": described["channel"],
        "channel_label": described["channel_label"],
        "frame_budget": described["frame_budget"],
        "transcript_chars": described["transcript_chars"],
        "source_filename": described["source_filename"],
        # What the run will read and what it will do to the existing note, echoed
        # back so a programmatic caller has it on the accept rather than having to
        # have asked for a preview first.
        "media": described["media"],
        "subtitle_timeline": described["subtitle_timeline"],
        "replace_note": replace_note,
    }


@router.post("/jobs/{task_id}/visual-note")
def start_job_visual_note(
    request: Request,
    task_id: str,
    background_tasks: BackgroundTasks,
    payload: Optional[dict[str, Any]] = Body(None),
) -> dict[str, Any]:
    """Write this task's note from its de-breathed media and remapped subtitles."""
    return start_local_visual_note(request, task_id, background_tasks, payload)
