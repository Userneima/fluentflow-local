"""Each note section gets the span of the recording it came from (decision 6.37).

Requirements (owner, 2026-10-23; review of the mock-up, 2026-10-24):
1. The table lives in the task's data, never in the note: making it changes no
   word of the note, not its version, not the task's place in the records; a
   note saved while the model was working stays as it was saved.
2. A new note gets its table once it is saved; an older note gets one when
   the editor opens it; until then the editor works without it. A failed try
   is tried again on a later opening, not at once, and the page can say why.
3. A renamed heading is matched again on its own: its sub-sections, and every
   other section, keep their times. A heading inserted with the same text as
   an existing one does not take the existing one's time. A cut list exported
   again makes every time wrong: all again.
4. Asked while a match is running, another match follows it.
5. The service matches saved notes on its own only with the mock-up switch on.
6. A section's main span is where it is talked about most; other spans it
   draws on are kept beside it. Times are seconds on the played file.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import job_store, note_section_times
from backend.core.local_request_scope import LOCAL_OWNER_ID
from backend.core.result_schema import content_versions
from backend.routers.local_job_edit import router as edit_router
from backend.routers.local_job_read import router as read_router

PAGE = {"x-fluentflow-client-id": LOCAL_OWNER_ID}

NOTE = """# 复盘会

开头一段。

<!-- 第 1/2 部分（00:00–10:00） -->

## 一、做得好的

### （一）发版节奏

稳定。

```
## 代码块里的不是标题
```

### （二）文档

写得勤。

## 二、计划方式

两周一个 Sprint。

## 小结

第一处小结。
"""

SEGMENTS = [
    {"start": float(i * 10), "end": float(i * 10 + 10), "text": f"第{i}句", "speaker": "SPEAKER_00"}
    for i in range(400)
]

_CREATED: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup(monkeypatch):
    monkeypatch.setattr(note_section_times, "_enabled", False)
    yield
    job_store.delete_jobs(_CREATED)
    _CREATED.clear()


def _task(note: str = NOTE, **extra) -> str:
    task_id = f"nst-{uuid.uuid4().hex[:8]}"
    _CREATED.append(task_id)
    result = {
        "task_id": task_id,
        "summary_markdown": note,
        "summary_status": "completed",
        "display_segments": SEGMENTS,
        "playback_media_kind": "debreath_media",
        "debreath": {"cut_list_updated_at": "2026-10-18T17:00:33+08:00"},
        **extra,
    }
    job_store.upsert_job(task_id=task_id, status="completed", client_id=LOCAL_OWNER_ID, result=result)
    return task_id


def _job(task_id: str) -> dict:
    return job_store.get_job(task_id)


def _save_note(task_id: str, note: str, **extra) -> None:
    job = _job(task_id)
    job_store.save_result_changes(task_id, job["result"], {**job["result"], "summary_markdown": note, **extra})


def _rows(task_id: str) -> dict:
    return {row["heading"]: row for row in _job(task_id)["result"]["note_section_times"]["sections"]}


def _clock(seconds: int) -> str:
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


class FakeModel:
    """Answers for every tagged heading: a main span from its tag number and,
    for S1, a second place it draws on. Remembers what it was asked."""

    def __init__(self, during=None):
        self.prompts: list[str] = []
        self.during = during

    def __call__(self, system: str, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.during:
            self.during()
        note = prompt.split("=== 笔记 ===")[1].split("=== 转录稿 ===")[0]
        rows = []
        for sid in re.findall(r"〔(S\d+)〕", note):
            start = 600 * len(self.prompts) + 60 * int(sid[1:])
            rows.append({
                "id": sid,
                "primary": {"start": _clock(start), "end": _clock(start + 50)},
                "others": [{"start": "0:00:10", "end": "0:00:40"}] if sid == "S1" else [],
            })
        return "好的：\n" + json.dumps({"sections": rows}, ensure_ascii=False)

    def tagged(self, index: int = -1) -> list[str]:
        note = self.prompts[index].split("=== 笔记 ===")[1].split("=== 转录稿 ===")[0]
        return [line.split("〔")[0].strip() for line in note.splitlines() if "〔S" in line]


def _chat(model):
    return model, "claude_code_subscription", "opus"


def test_sections_are_headings_outside_code_with_a_hash_of_their_own_body():
    sections = note_section_times.note_sections(NOTE)
    assert [(s["level"], s["heading"]) for s in sections] == [
        (2, "一、做得好的"), (3, "（一）发版节奏"), (3, "（二）文档"), (2, "二、计划方式"), (2, "小结"),
    ]
    # The page computes the same hash (frontend/src/lib/noteFollow.js pins
    # these values too).
    assert note_section_times.body_hash("") == "811c9dc5"
    assert note_section_times.body_hash("稳定。") == "84537921"
    assert sections[3]["body_hash"] == note_section_times.body_hash("两周一个 Sprint。")


def test_the_table_is_written_beside_the_note_and_changes_nothing_else():
    task_id = _task()
    # An old updated_at, so "unchanged" is not a coincidence of the same second.
    with sqlite3.connect(job_store.DEFAULT_DB_PATH) as conn:
        conn.execute("UPDATE jobs SET updated_at = ? WHERE task_id = ?", ("2001-01-01T00:00:00+00:00", task_id))
    before = _job(task_id)
    model = FakeModel()

    assert note_section_times.align(task_id, chat=_chat(model))["state"] == "ready"

    after = _job(task_id)
    assert after["updated_at"] == "2001-01-01T00:00:00+00:00"
    assert after["result"]["summary_markdown"] == NOTE
    assert content_versions(after["result"]) == content_versions(before["result"])
    table = after["result"]["note_section_times"]
    assert (table["timeline"], table["cut_list_updated_at"]) == ("debreath_media", "2026-10-18T17:00:33+08:00")
    rows = _rows(task_id)
    assert rows["一、做得好的"]["primary"] == [660.0, 710.0]  # S1 of the first ask
    assert rows["一、做得好的"]["others"] == [[10.0, 40.0]]
    assert [row["heading"] for row in table["sections"]] == ["一、做得好的", "（一）发版节奏", "（二）文档", "二、计划方式", "小结"]
    assert "第 1/2 部分" not in model.prompts[0]
    assert model.tagged() == ["## 一、做得好的", "### （一）发版节奏", "### （二）文档", "## 二、计划方式", "## 小结"]


def test_a_note_saved_while_the_model_works_stays_as_saved():
    task_id = _task()
    edited = NOTE.replace("稳定。", "稳定，这句是对照进行时改的。")
    model = FakeModel(during=lambda: _save_note(task_id, edited))

    note_section_times.align(task_id, chat=_chat(model))

    after = _job(task_id)["result"]
    assert after["summary_markdown"] == edited
    assert len(after["note_section_times"]["sections"]) == 5


def test_a_renamed_chapter_is_matched_alone_and_its_sub_sections_keep_their_times():
    task_id = _task()
    model = FakeModel()
    note_section_times.align(task_id, chat=_chat(model))
    first = _rows(task_id)

    _save_note(task_id, NOTE.replace("## 一、做得好的", "## 一、上季度做得好的"))
    assert note_section_times.status(_job(task_id)["result"])["state"] == "partial"
    note_section_times.align(task_id, chat=_chat(model))

    assert model.tagged() == ["## 一、上季度做得好的"]
    rows = _rows(task_id)
    for heading in ["（一）发版节奏", "（二）文档", "二、计划方式", "小结"]:
        assert rows[heading]["primary"] == first[heading]["primary"]
        assert rows[heading]["id"] == first[heading]["id"]
    assert "一、做得好的" not in rows
    assert note_section_times.status(_job(task_id)["result"])["state"] == "ready"


def test_a_new_heading_with_an_existing_text_does_not_take_the_existing_time():
    task_id = _task()
    model = FakeModel()
    note_section_times.align(task_id, chat=_chat(model))
    old = _rows(task_id)["小结"]

    inserted = NOTE.replace("## 二、计划方式", "## 小结\n\n新插的小结。\n\n## 二、计划方式")
    _save_note(task_id, inserted)
    note_section_times.align(task_id, chat=_chat(model))

    assert model.tagged() == ["## 小结"]
    summaries = [row for row in _job(task_id)["result"]["note_section_times"]["sections"] if row["heading"] == "小结"]
    assert len(summaries) == 2
    # The old one, last in the note, keeps its time and id; the new one is new.
    assert (summaries[1]["primary"], summaries[1]["id"]) == (old["primary"], old["id"])
    assert summaries[0]["primary"] != old["primary"]


def test_body_edits_cost_no_model_call():
    task_id = _task()
    model = FakeModel()
    note_section_times.align(task_id, chat=_chat(model))
    _save_note(task_id, NOTE.replace("稳定。", "很稳定。"))

    assert note_section_times.status(_job(task_id)["result"])["state"] == "ready"
    assert note_section_times.align(task_id, chat=_chat(model))["state"] == "unchanged"
    assert len(model.prompts) == 1


def test_a_cut_exported_again_matches_every_section_again():
    task_id = _task()
    model = FakeModel()
    note_section_times.align(task_id, chat=_chat(model))
    job = _job(task_id)
    job_store.save_result_changes(task_id, job["result"], {**job["result"], "debreath": {"cut_list_updated_at": "2026-10-24T09:00:00+08:00"}})

    status = note_section_times.status(_job(task_id)["result"])
    assert status["state"] == "missing"
    assert status["sections"] == []  # old times are not shown on the new cut

    note_section_times.align(task_id, chat=_chat(model))
    assert len(model.tagged()) == 5


def test_a_failed_try_is_kept_with_its_reason_and_tried_again_on_a_later_opening(monkeypatch):
    queued: list[tuple[str, float]] = []
    monkeypatch.setattr(note_section_times, "schedule_soon", lambda task_id, delay=0.0: queued.append((task_id, delay)))
    task_id = _task()
    before = _job(task_id)

    def broken(system, prompt):
        raise RuntimeError("网络断了")

    assert note_section_times.align(task_id, chat=(broken, "claude_code_subscription", "opus"))["state"] == "failed"
    after = _job(task_id)["result"]
    assert after["summary_markdown"] == before["result"]["summary_markdown"]
    status = note_section_times.request(after)
    assert status["state"] == "failed" and "网络断了" in status["error"]
    assert queued == []  # just failed: not again at once

    stale = {**after, "note_section_times": {**after["note_section_times"], "failed_at": "2001-01-01T00:00:00+00:00"}}
    note_section_times.request(stale)
    assert queued == [(task_id, 0.0)]


class DropsLast(FakeModel):
    """Leaves the last asked section out of its answer."""

    def __call__(self, system, prompt):
        answer = json.loads(super().__call__(system, prompt).split("\n", 1)[1])
        answer["sections"] = answer["sections"][:-1]
        return json.dumps(answer, ensure_ascii=False)


def test_sections_the_model_left_without_a_time_wait_like_a_failure(monkeypatch):
    queued: list[str] = []
    monkeypatch.setattr(note_section_times, "schedule_soon", lambda task_id, delay=0.0: queued.append(task_id))
    task_id = _task()
    assert note_section_times.align(task_id, chat=_chat(DropsLast()))["state"] == "ready"
    result = _job(task_id)["result"]
    assert note_section_times.status(result)["state"] == "partial"

    # An open page asks on every look: no new run for the same left-out section.
    for _ in range(3):
        note_section_times.request(_job(task_id)["result"])
    assert queued == []

    # A heading renamed meanwhile is asked about at once.
    _save_note(task_id, NOTE.replace("## 二、计划方式", "## 二、计划改成 Sprint"))
    note_section_times.request(_job(task_id)["result"])
    assert queued == [task_id]

    # And after the wait, the left-out one is tried again.
    queued.clear()
    _save_note(task_id, NOTE)
    stale = _job(task_id)["result"]
    stale = {**stale, "note_section_times": {**stale["note_section_times"], "unanswered_at": "2001-01-01T00:00:00+00:00"}}
    note_section_times.request(stale)
    assert queued == [task_id]


def test_same_name_sections_with_the_same_body_are_not_guessed_after_one_is_inserted():
    others = "".join(f"## 第{n}件事\n\n第{n}件事的话。\n\n" for n in range(1, 6))
    note = "## 讨论\n\n## 讨论\n\n" + others
    task_id = _task(note=note)
    model = FakeModel()
    note_section_times.align(task_id, chat=_chat(model))
    old = [row for row in _job(task_id)["result"]["note_section_times"]["sections"] if row["heading"] == "讨论"]

    _save_note(task_id, "## 讨论\n\n" + note)
    note_section_times.align(task_id, chat=_chat(model))

    # Which of three empty 讨论 is new cannot be told: all three are asked,
    # none keeps an old time it might not own; the other sections keep theirs.
    assert model.tagged() == ["## 讨论", "## 讨论", "## 讨论"]
    now = [row for row in _job(task_id)["result"]["note_section_times"]["sections"] if row["heading"] == "讨论"]
    assert not {row["id"] for row in now} & {row["id"] for row in old}


def test_a_note_still_being_written_is_not_matched():
    task_id = _task(summary_status="running")
    model = FakeModel()
    assert note_section_times.align(task_id, chat=_chat(model))["state"] == "skipped"
    assert model.prompts == []


def test_an_open_note_saved_in_the_editor_a_moment_ago_waits_for_quiet(monkeypatch):
    queued: list[tuple[str, float]] = []
    monkeypatch.setattr(note_section_times, "schedule_soon", lambda task_id, delay=0.0: queued.append((task_id, delay)))
    task_id = _task(summary_edited_at=note_section_times._now())

    note_section_times.request(_job(task_id)["result"])

    assert queued and queued[0][0] == task_id and 50 < queued[0][1] <= 60


def test_asked_while_running_another_match_follows(monkeypatch):
    task_id = _task()
    calls: list[str] = []
    release = threading.Event()
    started = threading.Event()

    def fake_align(tid, chat=None):
        calls.append(tid)
        if len(calls) == 1:
            started.set()
            release.wait(5)
        return {"state": "ready"}

    monkeypatch.setattr(note_section_times, "align", fake_align)
    note_section_times.schedule(task_id)
    assert started.wait(5)
    assert note_section_times.schedule(task_id) is True  # heading renamed meanwhile
    release.set()
    note_section_times._WORKER.submit(lambda: None).result(5)
    note_section_times._WORKER.submit(lambda: None).result(5)
    assert calls == [task_id, task_id]


def test_saved_notes_are_matched_on_their_own_only_with_the_switch_on(monkeypatch):
    queued: list[str] = []
    monkeypatch.setattr(note_section_times, "schedule_when_quiet", lambda task_id, delay=None: queued.append(task_id))
    task_id = _task(summary_status="running", summary_markdown="")

    monkeypatch.delenv(note_section_times.SWITCH_ENV, raising=False)
    note_section_times.start()
    _save_note(task_id, NOTE, summary_status="completed")
    assert queued == []

    monkeypatch.setenv(note_section_times.SWITCH_ENV, "on")
    note_section_times.start()
    _save_note(task_id, NOTE + "\n补一句。")
    assert queued == [task_id]

    # A table that covers every heading: the next edit queues nothing.
    note_section_times.align(task_id, chat=_chat(FakeModel()))
    queued.clear()
    _save_note(task_id, NOTE + "\n再补一句。")
    assert queued == []


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(read_router)
    app.include_router(edit_router)
    return TestClient(app)


def test_the_editor_reads_the_table_and_asks_for_one_when_there_is_none(monkeypatch):
    asked: list[str] = []
    monkeypatch.setattr(note_section_times, "schedule_soon", lambda task_id, delay=0.0: asked.append(task_id))
    task_id = _task()
    client = _client()

    read = client.get(f"/jobs/{task_id}/note/section-times", headers=PAGE)
    assert read.status_code == 200
    assert read.json()["state"] == "missing"

    assert client.post(f"/jobs/{task_id}/note/section-times", headers=PAGE).status_code == 200
    assert asked == [task_id]

    note_section_times.align(task_id, chat=_chat(FakeModel()))
    asked.clear()
    ready = client.post(f"/jobs/{task_id}/note/section-times", headers=PAGE).json()
    assert ready["state"] == "ready" and len(ready["sections"]) == 5
    assert asked == []
