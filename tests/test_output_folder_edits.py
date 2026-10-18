"""The md note edited where it sits comes back into the task.

Requirements (owner, 2026-10-18): the md beside the PDF is opened and edited in
Obsidian (corrections, questions written in while watching).
1. Whichever side changed last is the note: an md edited after FluentFlow wrote
   it becomes the task's note (and its PDF); a note changed in FluentFlow after
   the md was written replaces an md nobody touched.
2. An empty md never replaces a note that has content.
3. When both changed since the last sync, the later one is the note and the
   other is kept beside the md as a copy; nothing is dropped.
4. The md an earlier version kept in 过程文件 is taken in, if edited, before it
   is removed.
5. A saved md is noticed without waiting for the note to change in FluentFlow.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest

from backend.core import job_store, note_pdf, output_folder
from backend.core.result_artifacts import _attach_result_artifacts
from backend.core.storage_paths import _artifact_storage_dir


@pytest.fixture
def pdfs(monkeypatch):
    calls: list[str] = []

    def export(task_id, *, client_id=None, output_dir=None, filename=None):
        path = Path(output_dir) / f"{filename}.pdf"
        path.write_bytes(b"%PDF fake")
        calls.append(task_id)
        return {"task_id": task_id, "pdf_path": str(path), "html_path": None}

    monkeypatch.setattr(note_pdf, "export_pdf", export)
    return calls


def _task(tmp_path: Path, note: str) -> tuple[str, Path]:
    folder = tmp_path / "lectures"
    folder.mkdir(exist_ok=True)
    source = folder / "访谈.mov"
    source.write_bytes(b"original")
    task_id = f"oe-{uuid.uuid4().hex[:8]}"
    frames = _artifact_storage_dir() / task_id / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    (frames / "note_0001.jpg").write_bytes(b"jpeg")
    result = _attach_result_artifacts(task_id, {
        "filename": source.name, "display_title": source.stem, "transcript_text": "一句。",
        "summary_markdown": note.format(task=task_id), "summary_status": "completed",
    })
    job_store.upsert_job(task_id=task_id, status="completed", result=result,
                         metadata={"folder_intake": {"chosen_with": "folder_path", "original_path": str(source)}})
    return task_id, folder / "访谈" / "访谈_笔记.md"


NOTE = "# 访谈\n\n讲者说 Sode.MD（存疑）。\n\n![图](/jobs/{task}/artifacts/frame?file=note_0001.jpg)\n"


def _edit(md: Path, text: str, *, at: float | None = None) -> None:
    md.write_text(text, encoding="utf-8")
    stamp = at if at is not None else time.time() + 5
    os.utime(md, (stamp, stamp))


def _note(task: str) -> dict:
    return job_store.get_job(task)["result"]


def test_an_md_edited_in_obsidian_becomes_the_note_and_its_pdf(tmp_path, pdfs):
    task, md = _task(tmp_path, NOTE)
    output_folder.sync(task)
    edited = md.read_text(encoding="utf-8").replace("Sode.MD（存疑）", "Soul.MD") + "\n（现在的 Claude Code 怎么样？）\n"
    _edit(md, edited)

    record = output_folder.sync(task)

    result = _note(task)
    assert "Soul.MD" in result["summary_markdown"] and "现在的 Claude Code" in result["summary_markdown"]
    # The picture points at the task's own frame again, not the folder copy.
    assert f"/jobs/{task}/artifacts/frame?file=note_0001.jpg" in result["summary_markdown"]
    assert result["summary_edited"] is True
    assert record["md_taken_in"]["kept"] == "file"
    assert md.read_text(encoding="utf-8") == edited, "the md stays as the user left it"
    assert len(pdfs) == 2, "the PDF follows the edit"


def test_an_empty_md_never_replaces_the_note(tmp_path, pdfs):
    task, md = _task(tmp_path, NOTE)
    output_folder.sync(task)
    _edit(md, "\n")

    output_folder.sync(task)

    assert "Sode.MD" in _note(task)["summary_markdown"]
    assert "Sode.MD" in md.read_text(encoding="utf-8"), "the note is written back over the empty file"


def test_a_note_changed_in_fluentflow_replaces_an_md_nobody_touched(tmp_path, pdfs):
    task, md = _task(tmp_path, NOTE)
    output_folder.sync(task)
    job_store.update_job_result(task, {**_note(task), "summary_markdown": "# 访谈\n\n重写过的笔记。\n"})

    output_folder.sync(task)

    assert "重写过的笔记" in md.read_text(encoding="utf-8")
    assert list(md.parent.glob("访谈_笔记_*.md")) == []


def test_when_both_changed_the_later_wins_and_the_other_is_kept_beside(tmp_path, pdfs):
    task, md = _task(tmp_path, NOTE)
    output_folder.sync(task)
    job_store.update_job_result(task, {**_note(task), "summary_markdown": "# 访谈\n\n网页上改的。\n"})
    _edit(md, "# 访谈\n\nObsidian 里后改的。\n", at=time.time() + 60)

    output_folder.sync(task)

    assert "Obsidian 里后改的" in _note(task)["summary_markdown"]
    kept = list(md.parent.glob("访谈_笔记_FluentFlow版本-*.md"))
    assert len(kept) == 1 and "网页上改的" in kept[0].read_text(encoding="utf-8")


def test_when_both_changed_and_the_task_is_later_the_edit_is_kept_beside(tmp_path, pdfs):
    task, md = _task(tmp_path, NOTE)
    output_folder.sync(task)
    _edit(md, "# 访谈\n\nObsidian 里先改的。\n", at=time.time() + 2)
    time.sleep(3)  # the task's own save comes later
    job_store.update_job_result(task, {**_note(task), "summary_markdown": "# 访谈\n\n网页上后改的。\n"})

    output_folder.sync(task)

    assert "网页上后改的" in _note(task)["summary_markdown"]
    assert "网页上后改的" in md.read_text(encoding="utf-8")
    kept = list(md.parent.glob("访谈_笔记_Obsidian修改-*.md"))
    assert len(kept) == 1 and "Obsidian 里先改的" in kept[0].read_text(encoding="utf-8")


def test_the_old_md_in_process_files_is_taken_in_before_it_is_removed(tmp_path, pdfs):
    task, md = _task(tmp_path, NOTE)
    output_folder.sync(task)
    legacy = md.parent / "过程文件" / md.name
    _edit(legacy, "# 访谈\n\n在旧位置改的。\n")

    output_folder.sync(task)

    assert not legacy.exists()
    assert "在旧位置改的" in _note(task)["summary_markdown"]
    assert "在旧位置改的" in md.read_text(encoding="utf-8")


def test_a_saved_md_is_noticed_without_a_note_change(tmp_path, pdfs):
    task, md = _task(tmp_path, NOTE)
    output_folder.sync(task)
    assert task not in output_folder.edited_notes()

    _edit(md, md.read_text(encoding="utf-8") + "\n一个疑问？\n")

    assert task in output_folder.edited_notes()
