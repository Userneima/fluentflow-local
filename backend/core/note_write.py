"""One place where a written note becomes a result update.

Notes now arrive from three directions — the local AI pipeline, the user's
editor, and an external agent over the Agent API — and each has to leave the
result in the same shape. When every write path assembled its own field set, the
fields they disagreed on decided real behaviour: whether the editor showed the
note as saved, whether an export could find it, and whether an in-flight
regeneration was allowed to overwrite it. This module owns that field set so a
new writer cannot quietly invent a different one.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Final

DEFAULT_MAX_SUMMARY_EDIT_CHARS: Final[int] = 500_000

# Recorded in ``summary_source``. A result with no such field predates this
# module or came straight from the local AI pipeline — the pipeline writes the
# note as part of the job result and never goes through here.
NOTE_SOURCE_EDITOR: Final[str] = "editor"
NOTE_SOURCE_AGENT: Final[str] = "agent"


def max_summary_edit_chars() -> int:
    """Largest note a caller may write, in characters."""
    try:
        return max(
            int(os.environ.get("FLUENTFLOW_MAX_SUMMARY_EDIT_CHARS", str(DEFAULT_MAX_SUMMARY_EDIT_CHARS))),
            1,
        )
    except ValueError:
        return DEFAULT_MAX_SUMMARY_EDIT_CHARS


def apply_summary_edit(
    result: dict[str, Any],
    task_id: str,
    summary: str,
    *,
    source: str,
    source_label: str | None = None,
) -> dict[str, Any]:
    """Return a copy of ``result`` with the written note applied.

    ``summary_edited`` is set for every writer on purpose, not just for the
    human editor: it is the flag that makes an in-flight local regeneration lose
    to a note somebody else just wrote (``note_conflict_fingerprint`` compares
    the note body, and the editor renders the saved state from this field).
    """
    next_result = dict(result)
    edited_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    next_result.update({
        "task_id": next_result.get("task_id") or task_id,
        "summary_markdown": summary,
        "summary_skipped": False,
        "summary_status": "completed" if summary.strip() else next_result.get("summary_status") or "completed",
        "summary_error": None,
        "summary_edited": True,
        "summary_edited_at": edited_at,
        "summary_source": source,
        "summary_source_label": (source_label or "").strip() or None,
    })
    return next_result
