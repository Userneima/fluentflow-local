"""An agent exports a task's note as a PDF file, screenshots included."""

from __future__ import annotations

import backend.core.note_pdf as np_
import backend.core.visual_note_job as vn
from backend.core.result_artifacts import artifact_target_path
from test_visual_note_job import TASK, job_store, no_ambient_key  # noqa: F401


def test_screenshots_point_at_the_frame_files_on_this_machine(job_store):  # noqa: F811
    frame = artifact_target_path(TASK, "frames/note_0001.jpg")
    frame.write_bytes(b"\xff\xd8\xff")
    page = np_.printable_html(TASK, f"# 笔记\n\n![公式](/jobs/{TASK}/artifacts/frame?file=note_0001.jpg)\n", "笔记")

    assert frame.as_uri() in page
    assert "<h1>笔记</h1>" in page


def test_without_chrome_or_edge_the_printable_page_is_still_written(job_store, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(np_, "_browser", lambda: None)

    exported = np_.export_pdf(TASK, client_id="anonymous", output_dir=str(tmp_path))

    assert exported["pdf_path"] is None
    assert "Chrome" in exported["reason"]
    assert (tmp_path / "lecture.mp4.html").is_file() or list(tmp_path.glob("*.html"))


def test_a_task_without_a_note_is_refused(job_store, tmp_path):  # noqa: F811
    job_store["result"]["summary_markdown"] = ""
    try:
        np_.export_pdf(TASK, client_id="anonymous", output_dir=str(tmp_path))
    except np_.NotePdfError as exc:
        assert "还没有笔记" in str(exc)
    else:
        raise AssertionError("expected a refusal")
    assert vn.get_job(TASK)
