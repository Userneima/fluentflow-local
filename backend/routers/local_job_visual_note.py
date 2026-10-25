"""Local route for the second half of the flow: the note from the cut file.

Which Claude pays is decided by ``visual_note_channel``, not here: by default the
user's own Anthropic API key, resolved below from the backend-owned local config
or from ``ANTHROPIC_API_KEY`` in the project's .env, passed to the SDK, and never
written into a job result, an event, or a log line. The Claude Code login on this
machine is used only when ``FLUENTFLOW_VISUAL_NOTE_CHANNEL=subscription`` opts
into it. Frames come from ffmpeg on this machine through
``local_keyframe_provider``, or are disabled.

The note reads whatever file the transcript describes: the cut file when the
transcript was made from it, the original recording when the cut was declined or
found nothing to remove (``visual_note_job.cut_media``).

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
finished, no usable media (the cut still running or failed, a plan-only cut
with no rendered file, the cut file or the original recording no longer on the
machine, a cut list gone), a missing Anthropic credential, a run already going, and a
transcription running or queued that the frame extraction must not run beside.

The slot is claimed here rather than inside the worker. FastAPI runs background
tasks after the response is sent, so two quick submissions could both pass an
"is anything running?" read; the second would then be refused inside a worker
nobody is watching, having already answered "accepted".
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Request

from backend.core.task_reruns import start_local_visual_note

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/jobs/{task_id}/visual-note")
def start_job_visual_note(
    request: Request,
    task_id: str,
    background_tasks: BackgroundTasks,
    payload: Optional[dict[str, Any]] = Body(None),
) -> dict[str, Any]:
    """Write this task's note from its de-breathed media and remapped subtitles."""
    return start_local_visual_note(request, task_id, background_tasks, payload)
