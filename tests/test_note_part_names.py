"""A long recording written in parts calls each person the same thing throughout.

What has to hold, from the requirement (found on a 3.5-hour interview written in
three parts, 2026-10-19: the first part called the guest by name, the other two
called her 「受访者」, because she was named only in the opening minute):

- Every part after the first is told whom the earlier parts named and what they
  called them, and to keep those names. A part whose predecessors named no one
  is told nothing extra; the first part never is.
- A name the first part gave is not replaced by what a later part calls the
  same person.
- When the transcript carries speaker labels, every part gets the rule for
  turning labels into names, not only the first, and no part grows past the
  request size because of it.
"""

from __future__ import annotations

import pytest

import backend.core.visual_note_job as vn
from backend.core import claude_vision
from backend.core.claude_vision import TranscriptPart, VisualNoteDraft
from backend.core.cut_timeline import SPEAKER_LABEL_PREAMBLE
from test_visual_note_job import _run, job_store, no_ambient_key  # noqa: F401


def _part(index, total=3):
    return TranscriptPart(text=f"第{index}段", index=index, total=total)


def _draft(people=""):
    return VisualNoteDraft(markdown="## 节\n\n内容", basis_note="", model="m", people=people)


def test_a_later_part_is_told_the_names_the_earlier_parts_used():
    part = vn.with_known_people(_part(2), [_draft("罗福莉：受访者，小米大模型负责人\n小俊：主持人")])
    instruction = claude_vision.part_instruction(part)
    assert "罗福莉：受访者，小米大模型负责人" in instruction
    assert "小俊：主持人" in instruction
    assert "同一个称呼" in instruction


def test_no_names_means_no_extra_instruction():
    plain = claude_vision.part_instruction(_part(2))
    assert claude_vision.part_instruction(vn.with_known_people(_part(2), [_draft("")])) == plain
    assert claude_vision.part_instruction(vn.with_known_people(_part(1), [])) == plain.replace("第 2 部分", "第 1 部分")


def test_the_first_name_given_stays():
    part = vn.with_known_people(
        _part(3), [_draft("罗福莉：受访者"), _draft("- 罗福莉：嘉宾\n小俊：主持人")]
    )
    lines = part.known_people.splitlines()
    assert lines == ["罗福莉：受访者", "小俊：主持人"]


def test_every_part_carries_the_speaker_label_rule(monkeypatch):
    lines = "\n".join(f"[{i:02d}:00] 说话人 A：第{i}句话的内容。" for i in range(60))
    transcript = SPEAKER_LABEL_PREAMBLE + lines
    limit = len(SPEAKER_LABEL_PREAMBLE) + 400
    parts = claude_vision.transcript_parts(transcript, max_chars=limit)
    assert len(parts) > 1
    for part in parts:
        assert part.text.startswith(SPEAKER_LABEL_PREAMBLE.strip())
        assert part.text.count("以下每行的格式是") == 1
        assert len(part.text) <= limit
    body = "\n".join(p.text[len(SPEAKER_LABEL_PREAMBLE):] for p in parts)
    assert body.split("\n") == lines.split("\n"), "every line once, in order"


def test_an_unlabelled_transcript_is_split_as_before():
    lines = "\n".join(f"[{i:02d}:00] 第{i}句话。" for i in range(60))
    parts = claude_vision.transcript_parts(lines, max_chars=200)
    assert "\n".join(p.text for p in parts) == lines
    assert not any("说话人" in p.text for p in parts)


@pytest.mark.usefixtures("job_store")
def test_the_running_note_passes_names_from_part_to_part(tmp_path, monkeypatch):
    monkeypatch.setattr(vn.claude_vision, "MAX_TRANSCRIPT_CHARS", 20)
    seen = []

    def writer(transcript, frames, **kwargs):
        part = kwargs["part"]
        seen.append(part.known_people)
        return VisualNoteDraft(markdown=f"## 第{part.index}段\n\n" + "内容" * 50, basis_note="", model="m",
                               people="罗福莉：受访者" if part.index == 1 else "",
                               frames_sent=list(frames), transcript_chars=len(transcript))

    _run(tmp_path, "", writer=writer)
    assert len(seen) > 1
    assert seen[0] == ""
    assert all(known == "罗福莉：受访者" for known in seen[1:])
