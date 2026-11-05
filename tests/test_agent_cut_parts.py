"""The cut material of a long recording comes in parts a host can show whole.

Requirement (2026-11-05 review before running a 3-hour lesson through Codex):
Codex shows about 10,000 tokens of one tool result and drops the middle, so
the timeline of a 3-hour recording (about 89,000 characters) was mostly
unseen. So:

- every part, as the agent receives it, fits in what Codex shows;
- reading every part gives back the whole timeline, line for line, in order,
  and the instructions, earlier decisions, screenshots and how to answer, once;
- each part says how many there are and which to read next, and the last
  says there is none;
- a short recording is one part with everything in it;
- a part that is not there is refused, saying how many there are.
"""

from __future__ import annotations

import json

import pytest

from backend.core import agent_cut


def _timeline(lines: int) -> str:
    rows = ["录像总长 10560 秒。", ""]
    for i in range(lines):
        if i % 40 == 0:
            rows.append(f"〔空白 #{i // 40 + 1} · {i * 6.0:.1f}–{i * 6.0 + 4:.1f} 秒 · 长 4.0 秒 · 画面有变化〕")
        rows.append(f"[{i * 6.0:.1f}] 这是第 {i} 句，讲到调研里怎么把问题拆开，再一条条去找证据。")
    return "\n".join(rows)


@pytest.fixture
def waiting(monkeypatch):
    state = {"status": agent_cut.WAITING, "shots": [{"label": "空白 #1", "path": "/tmp/x/1.jpg"}],
             "decision_limit_seconds": 2700}

    def serve(timeline: str) -> None:
        state["timeline"] = timeline

    monkeypatch.setattr(agent_cut.job_store, "get_job", lambda task_id, client_id=None: {"task_id": task_id, "result": {agent_cut.KEY: state}})
    monkeypatch.setattr(agent_cut, "_save", lambda task_id, client_id, change: (change(state, {}), state)[1])
    return serve


# What Codex shows of one result, in characters of mostly-Chinese JSON:
# about 10,000 tokens at about 0.8 tokens a character, less a margin.
CODEX_SHOWS_CHARS = 12_000


def _all_parts(task_id: str = "t1") -> list[dict]:
    parts = [agent_cut.material(task_id, None)]
    while parts[-1]["next_part"] is not None:
        parts.append(agent_cut.material(task_id, None, parts[-1]["next_part"]))
    return parts


def test_a_three_hour_timeline_is_read_whole_in_parts_that_fit(waiting):
    timeline = _timeline(2000)
    assert len(timeline) > 80_000  # as long as the real one
    waiting(timeline)
    parts = _all_parts()

    assert len(parts) > 2
    for index, part in enumerate(parts, start=1):
        assert part["part"] == index and part["total_parts"] == len(parts)
        assert len(json.dumps({"ok": True, **part}, ensure_ascii=False)) < CODEX_SHOWS_CHARS
    assert parts[-1]["next_part"] is None and "read_on" not in parts[-1]
    assert all("read_on" in part for part in parts[:-1])

    assert "\n".join(part["timeline"] for part in parts if "timeline" in part) == timeline
    for field in ("instructions", "cases", "shots", "how_to_answer"):
        assert sum(field in part for part in parts) == 1, field


def test_a_short_recording_is_one_part_with_everything(waiting):
    waiting(_timeline(20))
    (only,) = _all_parts()
    assert only["total_parts"] == 1 and only["next_part"] is None
    assert {"instructions", "cases", "shots", "how_to_answer", "timeline"} <= set(only)


def test_a_part_that_is_not_there_is_refused(waiting):
    waiting(_timeline(2000))
    total = agent_cut.material("t1", None)["total_parts"]
    with pytest.raises(agent_cut.AgentCutError, match=str(total)):
        agent_cut.material("t1", None, total + 1)
    with pytest.raises(agent_cut.AgentCutError):
        agent_cut.material("t1", None, 0)
