"""Save a change to a task's transcript, then its transcript files.

Review (2026-11-03): a correction that wrote the txt, srt and vtt files before
the database write left the files with its version when that write was then
refused because the user had saved meanwhile; the output folder then copied
those files beside the recording. So the database goes first, and the files
are written from what it now holds; when the transcript moved again while
they were written, they are written once more from the newer one.
"""

from __future__ import annotations

from typing import Any

from backend.core import job_store
from backend.core.result_artifacts import _attach_result_artifacts

# What the transcript files are made from.
_FILE_BASIS = ("raw_segments", "display_segments", "transcript_text")


def save(
    task_id: str, before: dict[str, Any], after: dict[str, Any], *, client_id: str | None,
    unless_moved: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """``save_result_changes`` for a transcript change, the files after it.
    ``None`` (nothing written, no file touched) when a field of
    ``unless_moved`` changed since ``before`` was read."""
    saved = job_store.save_result_changes(task_id, before, after, client_id=client_id, unless_moved=unless_moved)
    if saved is None:
        return None
    for _ in range(3):
        current = saved["result"]
        with_files = _attach_result_artifacts(task_id, current, parts=("transcript",))
        again = job_store.save_result_changes(task_id, current, with_files, client_id=client_id,
                                              unless_moved=_FILE_BASIS)
        if again is not None:
            return again
        # Moved while the files were written: write them from the newer one.
        newer = job_store.get_job(task_id, client_id=client_id)
        if not newer:
            break
        saved = newer
    return saved


__all__ = ["save"]
