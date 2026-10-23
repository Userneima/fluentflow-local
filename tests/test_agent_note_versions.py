"""An agent's note writes name the note version they started from.

Requirements (owner, 2026-10-23, decision 6.30): an agent reads the note's
version with the task or the note material and passes it back when it saves.
1. A save naming the version the note has now goes through.
2. A save naming an older version is refused as stale and the note is left
   as it is; the answer tells the agent to read again, merge and save.
3. A save naming no version saves as before.
4. When the user edits the note in the page or Obsidian while the agent is
   writing, the agent's save naming the version it read is refused.
"""

from __future__ import annotations

import pytest

import backend.core.agent_note as an
import backend.core.visual_note_job as vn
from backend.core.job_store import VersionMoved
from backend.core.result_schema import content_versions
from test_visual_note_job import TASK, _fake_extractor, job_store, no_ambient_key  # noqa: F401

NOTE = "# 最小二乘法\n\n正文。"


@pytest.fixture()
def versioned_store(job_store, monkeypatch):  # noqa: F811
    """The test store, writing the way the real one does: a write that names
    a content version is refused when the task holds another one."""
    def save(task_id, _before, after, *, expect_versions=None, **_kw):
        if expect_versions:
            now = content_versions(job_store["result"])
            if any(now.get(key) != value for key, value in expect_versions.items()):
                raise VersionMoved(task_id)
        job_store["result"] = after
        return dict(job_store)

    monkeypatch.setattr(vn, "save_result_changes", save)
    return job_store


def _material(tmp_path):
    return an.material(TASK, client_id="anonymous", keyframe_extractor=_fake_extractor(tmp_path))


def _version(store) -> str:
    return content_versions(store["result"])["note_version"]


def test_the_material_carries_the_version_of_the_note_it_was_read_against(versioned_store, tmp_path):
    assert _material(tmp_path)["note_version"] == _version(versioned_store)


def test_a_save_naming_the_current_version_goes_through(versioned_store, tmp_path):
    material = _material(tmp_path)

    updated = an.save(TASK, note_markdown=NOTE, client_id="anonymous", checker=None,
                      base_version=material["note_version"])

    assert updated["result"]["summary_markdown"].startswith("# 最小二乘法")


def test_a_save_naming_an_older_version_is_refused_and_the_note_stays(versioned_store, tmp_path):
    material = _material(tmp_path)
    versioned_store["result"] = {**versioned_store["result"], "summary_markdown": "用户在网页上改过的笔记"}

    with pytest.raises(vn.NoteMovedError, match="重新读一遍"):
        an.save(TASK, note_markdown=NOTE, client_id="anonymous", checker=None,
                base_version=material["note_version"])

    assert versioned_store["result"]["summary_markdown"] == "用户在网页上改过的笔记"


def test_a_save_naming_no_version_saves_as_before(versioned_store, tmp_path):
    _material(tmp_path)
    versioned_store["result"] = {**versioned_store["result"], "summary_markdown": "用户在网页上改过的笔记"}

    updated = an.save(TASK, note_markdown=NOTE, client_id="anonymous", checker=None)

    assert updated["result"]["summary_markdown"].startswith("# 最小二乘法")


def test_an_edit_made_while_the_agent_writes_wins_over_its_save(versioned_store, tmp_path, monkeypatch):
    material = _material(tmp_path)
    real_cleaned = an._cleaned

    def edited_meanwhile(*args, **kwargs):
        # The user saves an edit in Obsidian after the save was checked and
        # before the note is written.
        versioned_store["result"] = {**versioned_store["result"], "summary_markdown": "在 Obsidian 里改的"}
        return real_cleaned(*args, **kwargs)

    monkeypatch.setattr(an, "_cleaned", edited_meanwhile)

    with pytest.raises(vn.NoteMovedError):
        an.save(TASK, note_markdown=NOTE, client_id="anonymous", checker=None,
                base_version=material["note_version"])

    assert versioned_store["result"]["summary_markdown"] == "在 Obsidian 里改的"


def test_putting_the_previous_note_back_checks_the_version_too(versioned_store, tmp_path):
    material = _material(tmp_path)
    an.save(TASK, note_markdown=NOTE, client_id="anonymous", checker=None, base_version=material["note_version"])

    with pytest.raises(vn.NoteMovedError):
        vn.restore_previous_note(TASK, client_id="anonymous", base_version=material["note_version"])
    assert versioned_store["result"]["summary_markdown"].startswith("# 最小二乘法")

    restored = vn.restore_previous_note(TASK, client_id="anonymous", base_version=_version(versioned_store))
    assert restored["result"]["summary_markdown"] == "原来的文字笔记"
