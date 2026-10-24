"""Each note section gets the span of the recording it came from (decision 6.37).

Requirements (owner, 2026-10-23):
1. The table lives in the task's data, never in the note: making it changes no
   word of the note, not its version, not the task's place in the records.
2. A new note gets its table once it is saved; an older note gets one the
   first time the editor opens it; until then the editor works without it.
3. A heading renamed is matched again on its own; the other sections keep
   their times. A cut list exported again makes every time wrong: all again.
4. A section's main span is where it is talked about most; other spans it
   draws on are kept beside it. Times are seconds on the played file.
"""

from __future__ import annotations

import json
import re
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

## 二、计划方式

两周一个 Sprint。
"""

SEGMENTS = [
    {"start": float(i * 10), "end": float(i * 10 + 10), "text": f"第{i}句", "speaker": "SPEAKER_00"}
    for i in range(60)
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


class FakeModel:
    """Answers for every tagged heading: a main span by its tag number and,
    for the first one, a second place it draws on. Remembers what it was asked."""

    def __init__(self):
        self.prompts: list[str] = []

    def __call__(self, system: str, prompt: str) -> str:
        self.prompts.append(prompt)
        note = prompt.split("=== 笔记 ===")[1].split("=== 转录稿 ===")[0]
        ids = re.findall(r"〔(S\d+)〕", note)
        rows = []
        for sid in ids:
            n = int(sid[1:])
            rows.append({
                "id": sid,
                "primary": {"start": f"0:0{n}:00", "end": f"0:0{n}:50"},
                "others": [{"start": "0:00:10", "end": "0:00:40"}] if n == 1 else [],
            })
        return "好的：\n" + json.dumps({"sections": rows}, ensure_ascii=False)

    def tagged_headings(self, index: int = -1) -> list[str]:
        note = self.prompts[index].split("=== 笔记 ===")[1].split("=== 转录稿 ===")[0]
        return [line.split("〔")[0].strip() for line in note.splitlines() if "〔S" in line]


def _chat(model: FakeModel):
    return model, "claude_code_subscription", "opus"


def test_sections_are_the_level_two_and_three_headings_outside_code():
    sections = note_section_times.note_sections(NOTE)
    assert [(s["level"], s["heading"], s["parent"]) for s in sections] == [
        (2, "一、做得好的", None),
        (3, "（一）发版节奏", "一、做得好的"),
        (2, "二、计划方式", None),
    ]


def test_a_section_is_named_the_way_the_page_names_it():
    # frontend/src/lib/noteFollow.js builds the same text with JSON.stringify.
    assert note_section_times.section_key(3, "一、做得好的", "（一）发版节奏") == '[3,"一、做得好的","（一）发版节奏",1]'
    assert note_section_times.section_key(2, None, "二") == '[2,"","二",1]'


def test_the_table_is_written_beside_the_note_and_changes_nothing_else():
    task_id = _task()
    before = _job(task_id)
    model = FakeModel()

    outcome = note_section_times.align(task_id, chat=_chat(model))

    after = _job(task_id)
    assert outcome["state"] == "ready"
    assert after["result"]["summary_markdown"] == NOTE
    assert content_versions(after["result"]) == content_versions(before["result"])
    assert after["updated_at"] == before["updated_at"]
    table = after["result"]["note_section_times"]
    assert table["timeline"] == "debreath_media"
    assert table["cut_list_updated_at"] == "2026-10-18T17:00:33+08:00"
    rows = {row["heading"]: row for row in table["sections"]}
    assert rows["一、做得好的"]["primary"] == [60.0, 110.0]
    assert rows["一、做得好的"]["others"] == [[10.0, 40.0]]
    assert rows["（一）发版节奏"]["parent"] == "一、做得好的"
    assert [row["heading"] for row in table["sections"]] == ["一、做得好的", "（一）发版节奏", "二、计划方式"]
    # The part markers are not shown to the model, and the code block's line
    # is not a section.
    assert "第 1/2 部分" not in model.prompts[0]
    assert model.tagged_headings() == ["## 一、做得好的", "### （一）发版节奏", "## 二、计划方式"]


def test_a_renamed_heading_is_matched_again_alone():
    task_id = _task()
    model = FakeModel()
    note_section_times.align(task_id, chat=_chat(model))
    first = {row["heading"]: row for row in _job(task_id)["result"]["note_section_times"]["sections"]}

    job = _job(task_id)
    renamed = NOTE.replace("## 二、计划方式", "## 二、计划改成两周一个 Sprint")
    job_store.save_result_changes(task_id, job["result"], {**job["result"], "summary_markdown": renamed})
    assert note_section_times.status(_job(task_id)["result"])["state"] == "partial"

    note_section_times.align(task_id, chat=_chat(model))

    assert model.tagged_headings() == ["## 二、计划改成两周一个 Sprint"]
    rows = {row["heading"]: row for row in _job(task_id)["result"]["note_section_times"]["sections"]}
    assert set(rows) == {"一、做得好的", "（一）发版节奏", "二、计划改成两周一个 Sprint"}
    assert rows["一、做得好的"] == first["一、做得好的"]
    assert note_section_times.status(_job(task_id)["result"])["state"] == "ready"


def test_body_edits_cost_no_model_call():
    task_id = _task()
    model = FakeModel()
    note_section_times.align(task_id, chat=_chat(model))
    job = _job(task_id)
    job_store.save_result_changes(task_id, job["result"], {**job["result"], "summary_markdown": NOTE.replace("稳定。", "很稳定。")})

    assert note_section_times.align(task_id, chat=_chat(model))["state"] == "unchanged"
    assert len(model.prompts) == 1


def test_a_cut_exported_again_matches_every_section_again():
    task_id = _task()
    model = FakeModel()
    note_section_times.align(task_id, chat=_chat(model))
    job = _job(task_id)
    moved = {**job["result"], "debreath": {"cut_list_updated_at": "2026-10-24T09:00:00+08:00"}}
    job_store.save_result_changes(task_id, job["result"], moved)

    status = note_section_times.status(_job(task_id)["result"])
    assert status["state"] == "missing"
    assert status["sections"] == []  # old times are not shown on the new cut

    note_section_times.align(task_id, chat=_chat(model))
    assert len(model.tagged_headings()) == 3
    assert _job(task_id)["result"]["note_section_times"]["cut_list_updated_at"] == "2026-10-24T09:00:00+08:00"


def test_without_claude_the_note_is_untouched_and_the_reason_kept():
    task_id = _task()
    before = _job(task_id)

    def broken(system, prompt):
        raise RuntimeError("网络断了")

    outcome = note_section_times.align(task_id, chat=(broken, "claude_code_subscription", "opus"))

    after = _job(task_id)
    assert outcome["state"] == "failed"
    assert after["result"]["summary_markdown"] == before["result"]["summary_markdown"]
    status = note_section_times.status(after["result"])
    assert status["state"] == "failed" and "网络断了" in status["error"]
    assert status["sections"] == []


def test_a_note_still_being_written_is_not_matched():
    task_id = _task(summary_status="running")
    model = FakeModel()
    assert note_section_times.align(task_id, chat=_chat(model))["state"] == "skipped"
    assert model.prompts == []


def test_a_saved_note_is_matched_once_the_changes_stop(monkeypatch):
    queued: list[str] = []
    monkeypatch.setattr(note_section_times, "schedule_when_quiet", lambda task_id, delay=None: queued.append(task_id))
    task_id = _task(summary_status="running", summary_markdown="")

    # Off outside the running service: tests and scripts save notes freely.
    job = _job(task_id)
    job_store.save_result_changes(task_id, job["result"], {**job["result"], "summary_markdown": NOTE, "summary_status": "completed"})
    assert queued == []

    monkeypatch.setattr(note_section_times, "_enabled", True)
    job = _job(task_id)
    job_store.save_result_changes(task_id, job["result"], {**job["result"], "summary_markdown": NOTE + "\n补一句。"})
    assert queued == [task_id]

    # A table that covers every heading: the next edit queues nothing.
    note_section_times.align(task_id, chat=_chat(FakeModel()))
    queued.clear()
    job = _job(task_id)
    job_store.save_result_changes(task_id, job["result"], {**job["result"], "summary_markdown": NOTE + "\n再补一句。"})
    assert queued == []


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(read_router)
    app.include_router(edit_router)
    return TestClient(app)


def test_the_editor_reads_the_table_and_asks_for_one_when_there_is_none(monkeypatch):
    asked: list[str] = []
    monkeypatch.setattr(note_section_times, "schedule", lambda task_id: asked.append(task_id) or True)
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
    assert ready["state"] == "ready" and len(ready["sections"]) == 3
    assert asked == []
