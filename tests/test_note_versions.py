"""Before either side of a note is overwritten, the version replaced is kept.

Requirements (owner, 2026-10-21, ahead of keeping notes in Feishu as well):
1. When FluentFlow writes a changed note over the md in the folder, the md as
   it was is kept and can be found again.
2. When an edit in Obsidian replaces the task's note, the task's note as it was
   is kept; two edits in a row leave both earlier versions findable, not only
   the last one.
3. A Feishu version about to be overwritten can be kept the same way.
4. Syncing when nothing changed adds no copies.
"""

from __future__ import annotations

import time

import pytest

from backend.core import job_store, note_versions, output_folder
from tests.test_output_folder_edits import NOTE, _edit, _note, _task, pdfs  # noqa: F401

pytestmark = pytest.mark.usefixtures("pdfs")


def _texts(md):
    return [p.read_text(encoding="utf-8") for p in note_versions.versions(md)]


def test_the_md_fluentflow_writes_over_is_kept(tmp_path):
    task, md = _task(tmp_path, NOTE)
    output_folder.sync(task)
    before = md.read_text(encoding="utf-8")

    job_store.update_job_result(task, {**_note(task), "summary_markdown": _note(task)["summary_markdown"] + "\n新加的一段。\n"})
    output_folder.sync(task)

    assert "新加的一段" in md.read_text(encoding="utf-8")
    assert before in _texts(md)
    kept = note_versions.versions(md)[-1]
    assert kept.parent == md.parent / output_folder.PROCESS_DIRNAME / note_versions.DIRNAME
    assert note_versions.LOCAL in kept.name


def test_every_note_an_obsidian_edit_replaces_is_kept(tmp_path):
    task, md = _task(tmp_path, NOTE)
    output_folder.sync(task)
    original = md.read_text(encoding="utf-8")
    _edit(md, original + "\n第一次改。\n")
    output_folder.sync(task)
    first = md.read_text(encoding="utf-8")
    _edit(md, first + "\n第二次改。\n", at=time.time() + 30)
    output_folder.sync(task)

    texts = _texts(md)
    assert original in texts, "the note before the first edit"
    assert first in texts, "the note before the second edit"
    assert all(note_versions.TASK in p.name for p in note_versions.versions(md))


def test_a_feishu_version_can_be_kept(tmp_path):
    md = tmp_path / "访谈" / "访谈_笔记.md"
    md.parent.mkdir()
    md.write_text("# 本地\n", encoding="utf-8")

    copy = note_versions.keep(md, "# 飞书里改过的\n", note_versions.FEISHU)

    assert copy is not None and note_versions.FEISHU in copy.name
    assert copy.read_text(encoding="utf-8") == "# 飞书里改过的\n"


def test_syncing_with_nothing_changed_adds_no_copies(tmp_path):
    task, md = _task(tmp_path, NOTE)
    output_folder.sync(task)
    job_store.update_job_result(task, {**_note(task), "summary_markdown": _note(task)["summary_markdown"] + "\n改一次。\n"})
    output_folder.sync(task)
    count = len(note_versions.versions(md))

    output_folder.sync(task)
    output_folder.sync(task)
    note_versions.keep(md, note_versions.versions(md)[-1].read_text(encoding="utf-8"), note_versions.LOCAL)

    assert len(note_versions.versions(md)) == count
