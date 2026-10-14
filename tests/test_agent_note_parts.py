"""An agent writes a long recording's note part by part.

What has to hold, written from the requirement (2026-10-14) rather than the code:

- What one call for material returns fits in one tool result of the agent's
  host. Claude Code takes about 25,000 tokens; Chinese runs about one token per
  character (measured on a 60,000-character part: 54,766 tokens), so the whole
  answer stays under 25,000 characters.
- Each part is saved as soon as it is written. The task's note does not change
  until every part is in; then it is the parts joined, and the note before the
  agent started can still be put back.
- Saved parts live in FluentFlow, not in the conversation: a later call (another
  conversation) carries on from what is saved, and can read a saved part back.
- Once the note is whole, the answer asks the agent for a read-through; re-saving
  one part updates that part in the note, and the note that can be put back is
  still the one from before the agent started.
- A part saved against a transcript that has since changed is not used.
- Re-saving a part never overwrites changes made to the note after it was joined
  (an edit in the editor, another writer); a whole note handed back is accepted.
"""

from __future__ import annotations

import json

import pytest

import backend.core.agent_note as an
import backend.core.visual_note_job as vn
from test_visual_note_job import TASK, _fake_extractor, job_store, no_ambient_key  # noqa: F401

LINE = "这一句讲的是最小二乘法的推导过程和它的前提条件，误差平方和对参数求导后令其为零。"


def _long(job_store, *, lines: int = 12):
    """Segments inside the fixture's kept span (20-100 s)."""
    job_store["result"]["display_segments"] = [
        {"start": 20.0 + i * 5, "end": 24.0 + i * 5, "text": LINE * 4} for i in range(lines)
    ]


def _material(tmp_path, part=1):
    return an.material(TASK, part=part, client_id="anonymous", keyframe_extractor=_fake_extractor(tmp_path))


def _note(index: int) -> str:
    return f"## 第 {index} 部分讲的内容\n\n" + "最小二乘法的推导：误差平方和对参数求导，令导数为零，得到正规方程。" * 6


@pytest.fixture(autouse=True)
def no_parts_from_an_earlier_test():
    """Every test here uses the same task id, and artifacts live for the session."""
    an._drop_store(TASK)
    yield
    an._drop_store(TASK)


@pytest.fixture
def small_parts(monkeypatch):
    monkeypatch.setattr(an, "AGENT_PART_CHARS", 700)


def _save(index: int, markdown: str | None = None):
    return an.save_part(TASK, part=index, note_markdown=markdown or _note(index), client_id="anonymous", checker=None)


def test_one_call_for_material_fits_one_tool_result(job_store, tmp_path):
    job_store["result"]["display_segments"] = [
        {"start": 20.0 + i, "end": 20.8 + i, "text": LINE * 25} for i in range(70)
    ]

    first = _material(tmp_path)
    assert first["total_parts"] > 1
    for index in range(1, first["total_parts"] + 1):
        material = _material(tmp_path, part=index)
        assert len(json.dumps({"ok": True, **material}, ensure_ascii=False)) < 25_000


def test_the_note_changes_only_when_every_part_is_in(job_store, tmp_path, small_parts):
    _long(job_store)
    total = _material(tmp_path)["total_parts"]
    assert total >= 3

    first = _save(1)
    assert first["completed"] is False
    assert first["remaining_parts"] == list(range(2, total + 1))
    assert job_store["result"]["summary_markdown"] == "原来的文字笔记"

    for index in range(2, total):
        _save(index)
    assert job_store["result"]["summary_markdown"] == "原来的文字笔记"

    last = _save(total)
    assert last["completed"] is True
    note = job_store["result"]["summary_markdown"]
    for index in range(1, total + 1):
        assert f"第 {index} 部分讲的内容" in note
    assert "通读" in last["next"]

    restored = vn.restore_previous_note(TASK, client_id="anonymous")
    assert restored["result"]["summary_markdown"] == "原来的文字笔记"


def test_saved_parts_outlive_the_conversation_and_can_be_read_back(job_store, tmp_path, small_parts):
    _long(job_store)
    _material(tmp_path)
    _save(1, _note(1) + "\n\n这是第一部分独有的一句。")

    # Nothing from the first call is passed in: everything comes from FluentFlow.
    read = an.saved_part(TASK, part=1, client_id="anonymous")
    assert "这是第一部分独有的一句" in read["markdown"]
    assert read["total_parts"] == _material(tmp_path)["total_parts"]
    with pytest.raises(an.AgentNoteError):
        an.saved_part(TASK, part=2, client_id="anonymous")


def test_resaving_a_part_after_the_note_is_whole_updates_it(job_store, tmp_path, small_parts):
    _long(job_store)
    total = _material(tmp_path)["total_parts"]
    for index in range(1, total + 1):
        _save(index)

    again = _save(2, _note(2) + "\n\n通读后补上的一句。")

    assert again["completed"] is True
    assert "通读后补上的一句" in job_store["result"]["summary_markdown"]
    restored = vn.restore_previous_note(TASK, client_id="anonymous")
    assert restored["result"]["summary_markdown"] == "原来的文字笔记"


def test_a_part_saved_against_an_older_transcript_is_not_used(job_store, tmp_path, small_parts):
    _long(job_store)
    _material(tmp_path)
    _save(1)

    segments = job_store["result"]["display_segments"]
    segments[0] = {**segments[0], "text": "改过的转录：" + segments[0]["text"]}
    total = _material(tmp_path)["total_parts"]
    for index in range(2, total + 1):
        result = _save(index)

    assert result["completed"] is False
    assert result["remaining_parts"] == [1]
    assert job_store["result"]["summary_markdown"] == "原来的文字笔记"


def test_a_part_never_overwrites_changes_made_after_the_join(job_store, tmp_path, small_parts):
    _long(job_store)
    total = _material(tmp_path)["total_parts"]
    for index in range(1, total + 1):
        _save(index)
    job_store["result"]["summary_markdown"] += "\n\n用户在编辑器里加的一句。"

    with pytest.raises(an.AgentNoteError, match="note_markdown"):
        _save(1)
    assert "用户在编辑器里加的一句" in job_store["result"]["summary_markdown"]

    whole = job_store["result"]["summary_markdown"] + "\n\n整篇交回时改的一句。"
    updated = an.save(TASK, note_markdown=whole, client_id="anonymous", checker=None)
    assert "整篇交回时改的一句" in updated["result"]["summary_markdown"]


def test_a_part_out_of_range_or_too_short_is_refused(job_store, tmp_path, monkeypatch):
    # Parts long enough for the length check, which skips a stretch under 1,000 characters.
    monkeypatch.setattr(an, "AGENT_PART_CHARS", 1500)
    _long(job_store)
    total = _material(tmp_path)["total_parts"]

    with pytest.raises(an.AgentNoteError):
        _save(total + 1)
    with pytest.raises(an.AgentNoteError, match="没有替换现有笔记"):
        _save(1, "## 太短\n\n一句。")
    assert job_store["result"]["summary_markdown"] == "原来的文字笔记"
