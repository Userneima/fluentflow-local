"""How long each step of a task took, as the task page and agents show it.

Requirements (owner, 2026-10-28: 「在 FluentFlow 里补上每个环节的耗时记录，任务详情里能看到一张分环节耗时表」),
in the user's terms:

- Every step that ran has a row with how long it took; a step that never ran
  or was skipped has none. A failed step still shows how long it ran before failing.
- Waiting in the queue before the first step is a row of its own.
- The cut is split into deciding what to cut, rendering the cut video and
  writing its report, and the parts add up to the cut's row.
- A step still running counts up to now and says it is still running.
- The total is the whole run, from submission to the last step's end; when
  steps ran side by side (the agent's note while the cut renders) the table
  says so, since the rows then add up to more than the total.
- A note the user's agent writes is timed from its first read of the material
  to its save, and reads done, though the flow had recorded the note skipped.
  Until it saves, it reads as still being written.
- When the user's agent judges the cut (2026-11-05), the cut's row starts
  where FluentFlow began preparing what the agent reads, and its parts show
  preparing, waiting for the agent to fetch it, the agent judging, and the
  work after the decision; they add up to the row.
- Transcript corrections the agent did are timed from its first page read to
  the save that left none, and matching the note's sections to playback is a
  row of its own once the agent did it.
- Only the first note is timed: reading or saving again later (an edit, a
  rewrite) does not change it, and a note FluentFlow wrote itself keeps its
  own time.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.core import job_store, task_steps

T0 = datetime(2026, 10, 28, 7, 17, 0, tzinfo=timezone.utc)


def at(seconds: float) -> str:
    return datetime.fromtimestamp(T0.timestamp() + seconds, tz=timezone.utc).isoformat()


def step(name, status, start=None, end=None, detail=None):
    return {
        "step_type": job_store.TASK_STEP_PREFIX + name, "status": status,
        "started_at": at(start) if start is not None else None,
        "finished_at": at(end) if end is not None else None,
        "result": detail,
    }


def rows_by_step(table):
    return {row["step"]: row for row in table["rows"]}


def test_each_step_that_ran_has_its_duration_and_the_rest_have_no_row():
    job = {"created_at": at(0), "result": {}}
    steps = [
        step("fetch", "done", 0, 65), step("transcribe", "done", 65, 158), step("correct", "done", 158, 158),
        step("cut", "skipped", None, 158), step("note", "skipped", None, 158), step("compress", "skipped", 158, 158),
        step("feishu", "skipped", None, 158),
    ]
    table = task_steps.timings(job, steps, now=T0)
    rows = rows_by_step(table)
    assert list(rows) == ["fetch", "transcribe", "correct"]
    assert rows["fetch"]["seconds"] == 65
    assert rows["transcribe"]["seconds"] == 93
    assert rows["fetch"]["label"] == "取素材"
    assert table["total_seconds"] == 158
    assert table["overlapping"] is False
    assert table["running"] is False


def test_a_failed_step_shows_how_long_it_ran():
    job = {"created_at": at(0), "result": {}}
    table = task_steps.timings(job, [step("fetch", "failed", 0, 265), step("transcribe", "skipped", None, 265)], now=T0)
    (row,) = table["rows"]
    assert (row["step"], row["status"], row["seconds"]) == ("fetch", "failed", 265)


def test_waiting_in_the_queue_is_its_own_row():
    job = {"created_at": at(0), "result": {}}
    table = task_steps.timings(job, [step("fetch", "done", 40, 60)], now=T0)
    assert [(row["step"], row["label"], row["seconds"]) for row in table["rows"]] == [
        ("queue", "排队", 40), ("fetch", "取素材", 20),
    ]
    assert table["total_seconds"] == 60


def test_no_queue_row_when_the_task_started_at_once():
    job = {"created_at": at(0), "result": {}}
    table = task_steps.timings(job, [step("fetch", "done", 0.4, 10)], now=T0)
    assert [row["step"] for row in table["rows"]] == ["fetch"]


def test_the_cut_is_split_into_judging_and_rendering():
    job = {"created_at": at(0), "result": {"debreath": {"started_at": at(130), "finished_at": at(180)}}}
    table = task_steps.timings(job, [step("cut", "done", 100, 190)], now=T0)
    cut = rows_by_step(table)["cut"]
    assert cut["seconds"] == 90
    assert cut["parts"] == [
        {"label": "判断剪哪里", "seconds": 30}, {"label": "生成剪后视频", "seconds": 50},
        {"label": "整理处理报告", "seconds": 10},
    ]
    assert sum(part["seconds"] for part in cut["parts"]) == cut["seconds"]


def test_a_render_from_an_earlier_run_does_not_split_this_cut():
    job = {"created_at": at(0), "result": {"debreath": {"started_at": at(10), "finished_at": at(20)}}}
    table = task_steps.timings(job, [step("cut", "done", 100, 190)], now=T0)
    assert rows_by_step(table)["cut"]["parts"] == []


def test_a_running_step_counts_up_to_now():
    job = {"created_at": at(0), "result": {}}
    now = datetime.fromisoformat(at(100))
    table = task_steps.timings(job, [step("fetch", "done", 0, 30), step("transcribe", "running", 30)], now=now)
    row = rows_by_step(table)["transcribe"]
    assert row["running"] is True
    assert row["seconds"] == 70
    assert table["running"] is True
    assert table["total_seconds"] == 100


def test_the_total_is_the_whole_run_and_side_by_side_steps_are_flagged():
    job = {"created_at": at(0), "result": {}}
    steps = [
        step("cut", "done", 0, 60),
        step("note", "done", None, 60, detail={"agent_started_at": at(40), "agent_finished_at": at(240)}),
    ]
    table = task_steps.timings(job, steps, now=T0)
    rows = rows_by_step(table)
    assert rows["note"]["seconds"] == 200
    assert rows["note"]["by_agent"] is True
    assert table["total_seconds"] == 240
    assert sum(row["seconds"] for row in table["rows"]) > table["total_seconds"]
    assert table["overlapping"] is True


# ── the agent's note, through the real step records ─────────────────────────

_counter = iter(range(10_000))


@pytest.fixture()
def task_id():
    task_id = f"timings-{next(_counter)}"
    job_store.upsert_job(task_id=task_id, status="completed", stage="done", progress=100,
                         result={"task_id": task_id})
    task_steps.plan(task_id)
    return task_id


def note_row(task_id):
    table = task_steps.timings(job_store.get_job(task_id), task_steps.steps_of([task_id]).get(task_id, []))
    return rows_by_step(table).get("note")


def test_an_agent_note_is_timed_from_reading_the_material_to_saving(task_id, monkeypatch):
    task_steps.finish(task_id, task_steps.NOTE, task_steps.SKIPPED)
    assert note_row(task_id) is None
    clock = iter([at(10), at(250)])
    monkeypatch.setattr(job_store, "now_iso", lambda: next(clock))
    task_steps.agent_began(task_id, task_steps.NOTE)
    writing = note_row(task_id)
    assert writing["running"] is True and writing["by_agent"] is True
    task_steps.agent_finished(task_id, task_steps.NOTE)
    done = note_row(task_id)
    assert (done["status"], done["seconds"], done["running"]) == ("done", 240, False)
    assert task_steps.status_of(task_id, task_steps.NOTE) == task_steps.DONE


def test_reading_and_saving_again_later_keeps_the_first_time(task_id, monkeypatch):
    task_steps.finish(task_id, task_steps.NOTE, task_steps.SKIPPED)
    clock = iter([at(10), at(250), at(9000), at(9100)])
    monkeypatch.setattr(job_store, "now_iso", lambda: next(clock))
    task_steps.agent_began(task_id, task_steps.NOTE)
    task_steps.agent_finished(task_id, task_steps.NOTE)
    task_steps.agent_began(task_id, task_steps.NOTE)
    task_steps.agent_finished(task_id, task_steps.NOTE)
    assert note_row(task_id)["seconds"] == 240


def test_several_reads_before_the_save_count_from_the_first(task_id, monkeypatch):
    task_steps.finish(task_id, task_steps.NOTE, task_steps.SKIPPED)
    stamped = []
    clock = iter([at(10), at(100), at(250)])

    def now():
        stamped.append(next(clock))
        return stamped[-1]
    monkeypatch.setattr(job_store, "now_iso", now)
    task_steps.agent_began(task_id, task_steps.NOTE)
    monkeypatch.setattr(job_store, "now_iso", lambda: at(250))
    task_steps.agent_began(task_id, task_steps.NOTE)
    task_steps.agent_finished(task_id, task_steps.NOTE)
    assert note_row(task_id)["seconds"] == 240


def test_a_note_fluentflow_wrote_keeps_its_own_time(task_id):
    task_steps.start(task_id, task_steps.NOTE)
    task_steps.finish(task_id, task_steps.NOTE, task_steps.DONE)
    before = note_row(task_id)
    task_steps.agent_began(task_id, task_steps.NOTE)
    task_steps.agent_finished(task_id, task_steps.NOTE)
    after = note_row(task_id)
    assert after["by_agent"] is False
    assert after["started_at"] == before["started_at"]


def test_an_agent_save_while_the_flow_still_owns_the_step_leaves_it_open(task_id):
    # The cut is still rendering: the flow closes the note step when it ends.
    task_steps.agent_began(task_id, task_steps.NOTE)
    task_steps.agent_finished(task_id, task_steps.NOTE)
    assert task_steps.status_of(task_id, task_steps.NOTE) == task_steps.PENDING
    assert note_row(task_id)["running"] is False


def test_a_fresh_run_clears_the_agent_time(task_id):
    task_steps.finish(task_id, task_steps.NOTE, task_steps.SKIPPED)
    task_steps.agent_began(task_id, task_steps.NOTE)
    task_steps.plan(task_id)
    assert note_row(task_id) is None


def test_a_cut_the_agent_judged_shows_the_preparing_and_the_judging():
    job = {"created_at": at(0), "result": {
        "agent_cut": {"prepare_started": at(100), "since": at(209), "material_at": at(214), "decided_at": at(302)},
        "debreath": {"started_at": at(335), "finished_at": at(1049)},
    }}
    steps = [step("transcribe", "done", 0, 100), step("cut", "done", 304, 1051)]
    row = rows_by_step(task_steps.timings(job, steps, now=T0))["cut"]
    assert row["by_agent"] is True
    assert row["started_at"] == at(100)
    assert [(part["label"], part["seconds"]) for part in row["parts"]] == [
        ("准备判断材料", 109), ("等 agent 取材料", 5), ("agent 判断剪哪里", 88),
        ("按决定算剪辑", 31), ("生成剪后视频", 714), ("整理处理报告", 2),
    ]
    assert row["seconds"] == 951
    assert sum(part["seconds"] for part in row["parts"]) == pytest.approx(row["seconds"], abs=3)


def test_corrections_by_the_agent_and_its_alignment_are_timed():
    job = {"created_at": at(0), "result": {}}
    steps = [
        step("correct", "done", 100, 101, {"agent_started_at": at(900), "agent_finished_at": at(1121)}),
        step("note", "done", None, 1000, {"agent_started_at": at(1130), "agent_finished_at": at(1456),
                                          "align_started_at": at(1460), "align_finished_at": at(1513)}),
    ]
    rows = rows_by_step(task_steps.timings(job, steps, now=T0))
    assert (rows["correct"]["by_agent"], rows["correct"]["seconds"]) == (True, 221)
    assert list(rows)[-1] == "align"
    assert (rows["align"]["label"], rows["align"]["seconds"], rows["align"]["by_agent"]) == ("对齐章节时间", 53, True)
