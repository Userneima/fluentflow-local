"""Whether a task that reads ``completed`` is still being worked on.

A local task turns ``completed`` as soon as its transcript is stored; the cut
(survey-first flow, or a re-render from the processing report) and the note
come after. Anything that removes or replaces the task asks here first:
deleting a task whose note step is still running let that step write the task
back afterwards, with no source and a failed note (read-only review,
2026-10-16).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.core import debreath_job, visual_note_job

# The note step marks the note pending within a second of the pipeline storing
# the transcript; a task still in that hand-over is about to be worked on.
_HANDOVER_SECONDS = 120


def _seconds_since(stamp: Any) -> float | None:
    try:
        moment = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - moment).total_seconds()


def busy_reason(job: dict[str, Any]) -> str | None:
    """What a completed task is still doing, in the user's words, or None."""
    if str(job.get("status") or "") != "completed":
        return None
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    if debreath_job.is_running(result):
        return "这个任务还在剪辑或导出视频，等它结束再删。"
    summary_status = str(result.get("summary_status") or job.get("summary_status") or "").lower()
    if visual_note_job.is_running(result) or summary_status == "pending":
        return "这个任务的笔记还在写，等写完再删。"
    deferred = (
        result.get("note_deferred_to_visual_note")
        and summary_status == "skipped"
        and not str(result.get("summary_markdown") or "").strip()
    )
    age = _seconds_since(job.get("updated_at"))
    if deferred and age is not None and age < _HANDOVER_SECONDS:
        return "这个任务的笔记马上要开始写，等写完再删。"
    return None


__all__ = ["busy_reason"]
