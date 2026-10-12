"""A long recording's note is written in parts and must still read as one note.

What has to hold, written from the requirement rather than the code (found on a
3h18m podcast written in four parts, 2026-10-12):

- Section numbers continue across parts: four parts of two sections each are
  「一、」 to 「八、」, each number once, in recording order.
- Every part's 「存疑」 section ends up in one 「存疑」 section at the end of the
  note, with every item kept; a note whose parts had none has no such section.
- The note has exactly one H1. The task's title is used when no part wrote one;
  a title the first part wrote is kept; a title a later part wrote is not.
- The join is mechanical: no model call, and the marker saying which stretch of
  the recording each part covers stays above that part.
"""

from __future__ import annotations

import re

import pytest

import backend.core.agent_note as an
import backend.core.visual_note_job as vn
from backend.core.claude_vision import TranscriptPart, VisualNoteDraft
from backend.core.note_join import chinese_number
from test_visual_note_job import _fake_extractor, _recording_writer, _run, TASK, job_store, no_ambient_key  # noqa: F401


def _parts(count):
    return [
        TranscriptPart(text=f"第{i}段", index=i, total=count, starts_at=f"{i * 10}:00", ends_at=f"{i * 10 + 9}:59")
        for i in range(1, count + 1)
    ]


def _part_note(index, *, doubt=True, h1=""):
    lines = [f"# {h1}", ""] if h1 else []
    lines += [
        f"## 一、第{index}部分的开头", "", f"第{index}部分讲的第一件事。", "",
        f"## 二、第{index}部分的结论", "", f"第{index}部分讲的第二件事。",
    ]
    if doubt:
        lines += ["", "## 存疑", "", f"- 第{index}部分听不清的人名"]
    return "\n".join(lines)


def _join(notes, title="三小时播客"):
    drafts = [VisualNoteDraft(markdown=note, basis_note="", model="m") for note in notes]
    return vn._joined_draft(drafts, _parts(len(notes)), title=title).markdown


def _h2(markdown):
    return [line for line in markdown.splitlines() if line.startswith("## ")]


def test_section_numbers_run_on_across_parts():
    note = _join([_part_note(i) for i in range(1, 5)])

    numbered = [h for h in _h2(note) if "存疑" not in h]
    assert numbered == [
        f"## {chinese_number(n)}、第{(n + 1) // 2}部分的{'开头' if n % 2 else '结论'}"
        for n in range(1, 9)
    ]


def test_every_doubt_goes_into_one_section_at_the_end():
    note = _join([_part_note(i) for i in range(1, 5)])

    assert [h for h in _h2(note) if "存疑" in h] == ["## 存疑"]
    assert _h2(note)[-1] == "## 存疑", "the doubt section comes after every other section"
    tail = note.split("## 存疑", 1)[1]
    for i in range(1, 5):
        assert f"- 第{i}部分听不清的人名" in tail
        assert note.count(f"第{i}部分听不清的人名") == 1


def test_a_numbered_doubt_heading_takes_the_next_number():
    notes = [_part_note(i, doubt=False) + f"\n\n## 三、存疑\n\n- 第{i}部分的数字" for i in (1, 2)]

    note = _join(notes)

    assert _h2(note)[-1] == "## 五、存疑"
    assert "- 第1部分的数字" in note and "- 第2部分的数字" in note


def test_no_doubts_means_no_doubt_section():
    note = _join([_part_note(i, doubt=False) for i in (1, 2)])

    assert "存疑" not in note


def test_the_note_gets_the_tasks_title_when_no_part_wrote_one():
    note = _join([_part_note(i) for i in (1, 2, 3)], title="三小时播客")

    assert note.splitlines()[0] == "# 三小时播客"
    assert len(re.findall(r"^# ", note, flags=re.M)) == 1


def test_a_title_the_first_part_wrote_is_kept_and_later_ones_are_not():
    notes = [_part_note(1, h1="模型写的总标题"), _part_note(2, h1="第二部分自己的标题")]

    note = _join(notes, title="文件名")

    assert note.splitlines()[0] == "# 模型写的总标题"
    assert len(re.findall(r"^# ", note, flags=re.M)) == 1
    assert "第二部分自己的标题" not in note


def test_each_part_keeps_its_marker_in_order():
    note = _join([_part_note(i) for i in (1, 2, 3)])

    markers = re.findall(r"<!-- 第 (\d)/3 部分（[^）]+） -->", note)
    assert markers == ["1", "2", "3"]
    assert note.index("<!-- 第 2/3") < note.index("## 三、第2部分的开头")


def test_headings_inside_code_blocks_are_left_alone():
    code = "## 一、命令\n\n```bash\n## 一、这是脚本里的注释\n# 存疑\n```"
    note = _join([code, _part_note(2, doubt=False)])

    assert "## 一、这是脚本里的注释" in note
    assert "# 存疑" in note
    assert "## 二、第2部分的开头" in note


def test_a_note_written_in_one_request_is_left_as_it_was():
    single = _part_note(1)
    drafts = [VisualNoteDraft(markdown=single, basis_note="", model="m")]

    assert vn._joined_draft(drafts, _parts(1), title="标题").markdown == single


@pytest.mark.parametrize("value, expected", [
    (1, "一"), (10, "十"), (11, "十一"), (20, "二十"), (33, "三十三"),
    (100, "一百"), (105, "一百零五"), (110, "一百一十"), (121, "一百二十一"),
])
def test_chinese_numbers(value, expected):
    assert chinese_number(value) == expected


# ── the joined note is what the task stores ─────────────────────────────────

@pytest.mark.usefixtures("job_store")
def test_a_note_written_in_parts_is_stored_with_one_title_and_running_numbers(tmp_path, monkeypatch):
    monkeypatch.setattr(vn.claude_vision, "MAX_TRANSCRIPT_CHARS", 20)

    def numbered_writer(transcript, frames, **kwargs):
        index = kwargs["part"].index
        return VisualNoteDraft(markdown=_part_note(index), basis_note="", model="m",
                               frames_sent=list(frames), transcript_chars=len(transcript))

    updated = _run(tmp_path, "", writer=numbered_writer)

    note = vn.visual_note_state(updated["result"])["markdown"]
    assert note.splitlines()[0] == "# lecture", "the task's title, without the file extension"
    numbers = [h.split("、")[0] for h in _h2(note) if "存疑" not in h]
    assert numbers == [f"## {chinese_number(n)}" for n in range(1, len(numbers) + 1)]
    assert [h for h in _h2(note) if "存疑" in h] == ["## 存疑"]


@pytest.mark.usefixtures("job_store")
def test_an_agent_note_saved_in_parts_is_joined_the_same_way(tmp_path, monkeypatch):
    monkeypatch.setattr(vn.claude_vision, "MAX_TRANSCRIPT_CHARS", 20)
    material = an.material(TASK, client_id="anonymous", keyframe_extractor=_fake_extractor(tmp_path))
    total = material["total_parts"]
    assert total > 1

    updated = an.save(TASK, part_notes=[_part_note(i) for i in range(1, total + 1)], client_id="anonymous")

    note = updated["result"]["summary_markdown"]
    assert note.splitlines()[0] == "# lecture"
    assert [h for h in _h2(note) if "存疑" not in h][-1].startswith(f"## {chinese_number(2 * total)}、")
    assert _h2(note)[-1] == "## 存疑"


@pytest.mark.usefixtures("job_store")
def test_the_recording_writer_still_reaches_the_note(tmp_path, monkeypatch):
    """Parts whose sections are not numbered keep their headings as written."""
    monkeypatch.setattr(vn.claude_vision, "MAX_TRANSCRIPT_CHARS", 20)
    writer = _recording_writer()

    updated = _run(tmp_path, "", writer=writer)

    note = vn.visual_note_state(updated["result"])["markdown"]
    for call in writer.calls:
        assert f"## 第{call['part'].index}段" in note
