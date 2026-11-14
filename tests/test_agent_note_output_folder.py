"""An agent's note reaches the output folder beside the recording right away.

Requirement (owner, 2026-11-14): when an agent saves a note with
``save_agent_note``, the md and PDF in the task's output folder carry it as soon
as the save returns and the folder has caught up, the first time as much as
later. Before this, the first save only started the minute-long wait meant for
an editor saving every few seconds, so the agent reported the note done while
the folder still had none; and a save that landed while the folder was being
filled for the first time was never written there at all.
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

import pytest

import backend.core.agent_note as an
import backend.core.debreath_job as dj
from backend.core import job_store, note_pdf, output_folder
from test_visual_note_job import SEGMENTS, _cut_media, no_ambient_key  # noqa: F401


@pytest.fixture
def pdfs(monkeypatch):
    calls: list[str] = []

    def export(task_id, *, client_id=None, output_dir=None, filename=None):
        calls.append(task_id)
        path = Path(output_dir) / f"{filename}.pdf"
        path.write_bytes(b"%PDF fake")
        return {"task_id": task_id, "pdf_path": str(path), "html_path": None}

    monkeypatch.setattr(note_pdf, "export_pdf", export)
    return calls


def _finished_task(tmp_path: Path) -> tuple[str, Path]:
    """A finished, cut task on a recording in the user's folder, with no note
    yet: what an agent submission looks like when the note is left to it."""
    folder = tmp_path / "lectures"
    folder.mkdir()
    source = folder / "lecture.mp4"
    source.write_bytes(b"original")
    task_id = f"an-of-{uuid.uuid4().hex[:8]}"
    _cut_media(task_id)
    result = {
        "task_id": task_id,
        "filename": "lecture.mp4",
        "summary_markdown": "",
        "summary_status": "skipped",
        "transcript_text": "今天讲最小二乘法",
        "display_segments": list(SEGMENTS),
        "debreath": {
            "status": "completed", "stage": "done", "rendered": True, "render_verified": True,
            "media_filename": "debreath/lecture_debreath.mp4",
            "plan": {"cut_count": 1, "removed_seconds": 10.0},
        },
        "artifacts": {
            dj.MEDIA_KIND: {"kind": dj.MEDIA_KIND, "filename": "debreath/lecture_debreath.mp4"},
            dj.CUT_LIST_KIND: {"kind": dj.CUT_LIST_KIND, "filename": "debreath/lecture_cut_list.json"},
        },
    }
    job_store.upsert_job(
        task_id=task_id, status="completed", result=result, client_id="anonymous",
        source_filename="lecture.mp4",
        metadata={"folder_intake": {"chosen_with": "folder_path", "original_path": str(source)}},
    )
    return task_id, source


def _wait_for_worker() -> None:
    output_folder._WORKER.submit(lambda: None).result(timeout=10)


def _md(source: Path) -> Path:
    return source.parent / "lecture" / "lecture_笔记.md"


def test_the_first_agent_note_is_in_the_folder_without_the_editors_wait(tmp_path, pdfs, monkeypatch):
    # The wait meant for an editor's saves: the agent's note must not depend on it.
    monkeypatch.setattr(output_folder, "SYNC_QUIET_SECONDS", 3600.0)
    task_id, source = _finished_task(tmp_path)
    output_folder.sync(task_id)  # the end of the task: the folder, with no note yet
    assert not _md(source).exists()

    an.save(task_id, note_markdown="# 最小二乘法\n\n正文。", client_id="anonymous")
    _wait_for_worker()

    assert "正文" in _md(source).read_text(encoding="utf-8")
    assert job_store.get_job(task_id)["result"]["output_folder"]["md_path"] == str(_md(source))


def test_a_note_saved_while_the_folder_is_first_filled_is_not_lost(tmp_path, pdfs, monkeypatch):
    """The folder's first fill read the task before the note was saved and
    recorded the folder after: the save saw no folder to refresh, the fill saw
    no note, and nothing wrote it afterwards."""
    monkeypatch.setattr(output_folder, "SYNC_QUIET_SECONDS", 3600.0)
    task_id, source = _finished_task(tmp_path)
    read_it = threading.Event()
    go_on = threading.Event()
    real = output_folder._sync_files

    def slow_fill(*args, **kwargs):
        read_it.set()
        assert go_on.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(output_folder, "_sync_files", slow_fill)
    output_folder.schedule_sync(task_id)
    assert read_it.wait(10)
    an.save(task_id, note_markdown="# 最小二乘法\n\n正文。", client_id="anonymous")
    go_on.set()
    _wait_for_worker()
    _wait_for_worker()

    assert "正文" in _md(source).read_text(encoding="utf-8")
