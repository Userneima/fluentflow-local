"""An agent writes a recording's note: whole by default, part by part when its
host cannot take the whole transcript in one tool result.

What has to hold, written from the requirements (2026-10-14, 2026-10-16) rather
than the code:

- By default one call returns the whole transcript, and the whole note is saved
  in one call (owner, 2026-10-16: a note is no strain for an agent; a host's
  limit on one tool result can be raised).
- With small_parts, what one call for material returns fits in one tool result of the agent's
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
  still the one from before the agent started. A change saved during the
  read-through does not ask for another read-through (2026-10-27).
- A conversation coming back to a note left half written learns which parts are
  missing without fetching the material, and is told to hand them to a fresh
  subagent (2026-10-27: the main conversation wrote the rest itself, slowly).
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


def _material(tmp_path, part=1, small=True):
    return an.material(TASK, part=part, small_parts=small, client_id="anonymous",
                       keyframe_extractor=_fake_extractor(tmp_path))


def _note(index: int) -> str:
    return f"## 第 {index} 部分讲的内容\n\n" + "最小二乘法的推导：误差平方和对参数求导，令导数为零，得到正规方程。" * 6


@pytest.fixture(autouse=True)
def no_parts_from_an_earlier_test():
    """Every test here uses the same task id, and artifacts live for the session."""
    an._drop_store(TASK)
    an._set_small_parts(TASK, False)
    yield
    an._drop_store(TASK)
    an._set_small_parts(TASK, False)


def test_by_default_the_whole_recording_comes_in_one_call_and_is_saved_in_one(job_store, tmp_path):
    job_store["result"]["display_segments"] = [
        {"start": 20.0 + i, "end": 20.8 + i, "text": LINE * 25} for i in range(70)
    ]

    material = _material(tmp_path, small=False)

    assert material["total_parts"] == 1
    assert material["transcript"].count(LINE) == 70 * 25
    saved = an.save(TASK, note_markdown=_note(1) * 40, client_id="anonymous", checker=None)
    assert "最小二乘法" in saved["result"]["summary_markdown"]


def test_asking_for_small_parts_again_switches_back_to_parts(job_store, tmp_path):
    job_store["result"]["display_segments"] = [
        {"start": 20.0 + i, "end": 20.8 + i, "text": LINE * 25} for i in range(70)
    ]
    assert _material(tmp_path, small=False)["total_parts"] == 1
    assert _material(tmp_path, small=True)["total_parts"] > 1
    assert _material(tmp_path, small=False)["total_parts"] == 1


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


def test_a_part_saved_after_the_note_was_edited_starts_over_and_keeps_the_edit(job_store, tmp_path, small_parts):
    _long(job_store)
    total = _material(tmp_path)["total_parts"]
    for index in range(1, total + 1):
        _save(index)
    job_store["result"]["summary_markdown"] += "\n\n用户在编辑器里加的一句。"
    edited = job_store["result"]["summary_markdown"]

    first = _save(1)
    assert first["completed"] is False
    assert first["remaining_parts"] == list(range(2, total + 1))
    assert job_store["result"]["summary_markdown"] == edited

    for index in range(2, total + 1):
        _save(index)
    restored = vn.restore_previous_note(TASK, client_id="anonymous")
    assert restored["result"]["summary_markdown"] == edited


def test_a_whole_note_handed_back_is_accepted(job_store, tmp_path, small_parts):
    _long(job_store)
    total = _material(tmp_path)["total_parts"]
    for index in range(1, total + 1):
        _save(index)

    whole = job_store["result"]["summary_markdown"] + "\n\n整篇交回时改的一句。"
    updated = an.save(TASK, note_markdown=whole, client_id="anonymous", checker=None)
    assert "整篇交回时改的一句" in updated["result"]["summary_markdown"]


def test_the_note_before_the_agent_stays_restorable_through_a_transcript_edit(job_store, tmp_path, small_parts):
    _long(job_store)
    _material(tmp_path)
    total = _material(tmp_path)["total_parts"]
    for index in range(1, total + 1):
        _save(index)

    segments = job_store["result"]["display_segments"]
    segments[-1] = {**segments[-1], "text": segments[-1]["text"] + "补一句。"}
    total = _material(tmp_path)["total_parts"]
    for index in range(1, total + 1):
        _save(index)

    restored = vn.restore_previous_note(TASK, client_id="anonymous")
    assert restored["result"]["summary_markdown"] == "原来的文字笔记"


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


def test_saving_the_whole_note_again_keeps_the_note_from_before_the_agent_restorable(job_store, tmp_path):
    """Requirement (2026-10-16, found in a real run): an agent that saves its
    whole note several times (a coverage fill, a fix) leaves the note from
    before it started as the one to put back, not its own earlier draft."""
    _material(tmp_path, small=False)
    first = an.save(TASK, note_markdown=_note(1) * 40, client_id="anonymous", checker=None)
    assert first["result"]["summary_markdown"] != "原来的文字笔记"

    an.save(TASK, note_markdown=_note(2) * 40, client_id="anonymous", checker=None)
    an.save(TASK, note_markdown=_note(3) * 40, client_id="anonymous", checker=None)
    vn.restore_previous_note(TASK, client_id="anonymous")

    assert job_store["result"]["summary_markdown"] == "原来的文字笔记"


def test_a_note_is_checked_on_its_first_save_only(job_store, tmp_path):
    """Owner, 2026-10-16: each note is checked once. A re-check lists other
    points every time, so the agent's fill drew new gaps, mostly the details
    of examples, at a model call and minutes each."""
    _material(tmp_path, small=False)
    calls = []

    def chat(system, user):
        calls.append(user)
        return '{"points": [{"point": "最小二乘法", "covered": true}]}'

    checker = (chat, "fake")
    first = an.save(TASK, note_markdown=_note(1) * 40, client_id="anonymous", checker=checker)
    checked = len(calls)
    assert checked > 0
    assert vn.visual_note_state(first["result"])["coverage"]["checker"] == "fake"

    again = an.save(TASK, note_markdown=_note(2) * 40, client_id="anonymous", checker=checker)

    assert len(calls) == checked, "the fill is not checked again"
    assert vn.visual_note_state(again["result"])["coverage"]["parts"][0]["checked"] is False


def test_a_part_saved_again_is_not_checked_again(job_store, tmp_path, small_parts):
    _long(job_store)
    _material(tmp_path)
    calls = []
    checker = (lambda system, user: calls.append(user) or '{"points": []}', "fake")

    an.save_part(TASK, part=1, note_markdown=_note(1), client_id="anonymous", checker=checker)
    checked = len(calls)
    answer = an.save_part(TASK, part=1, note_markdown=_note(1) + "补上的一句。", client_id="anonymous", checker=checker)

    assert checked > 0 and len(calls) == checked
    assert answer["coverage"]["parts"][0]["checked"] is False



def test_a_change_from_the_read_through_does_not_ask_for_another(job_store, tmp_path, small_parts):
    _long(job_store)
    total = _material(tmp_path)["total_parts"]
    for index in range(1, total):
        _save(index)
    assert "通读一遍" in _save(total)["next"]

    changed = _save(2, _note(2) + "\n\n通读后补上的一句。")

    assert "通读一遍" not in changed["next"]
    assert "改完就结束" in changed["next"]


def test_a_half_written_note_says_which_parts_are_left(job_store, tmp_path, small_parts):
    _long(job_store)
    total = _material(tmp_path)["total_parts"]
    assert an.unfinished_draft(TASK) is None
    _save(1)
    _save(2)

    draft = an.unfinished_draft(TASK)

    assert draft["saved_parts"] == [1, 2]
    assert draft["remaining_parts"] == list(range(3, total + 1))
    assert "fresh context" in draft["next"] and "small_parts=true" in draft["next"]
    for index in range(3, total + 1):
        _save(index)
    assert an.unfinished_draft(TASK) is None
