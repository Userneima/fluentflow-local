"""An agent's note goes to Feishu when the task asked for it.

Requirement (10-18 review, item 13): a task submitted with "export to Feishu
automatically" on, whose note the user's agent writes, gets its Feishu
document once the note is saved, the same as a note FluentFlow writes. The
flow's own export step ran before the agent wrote anything. Not exported:
a task that did not ask, a task still being processed (its own export step
comes after), and a task that already has a document or an export error.
"""

from __future__ import annotations

import time
import uuid

import pytest

from backend.core import job_store, task_flow


@pytest.fixture
def exports(monkeypatch):
    calls: list[dict] = []

    def fake(**values):
        calls.append(values)
        return {"doc_title": "标题", "export_target": "lark_cli", "response": {"url": "https://x.feishu.cn/docx/abc"}}

    monkeypatch.setattr(task_flow, "_auto_export_local_lark", fake)
    monkeypatch.setattr(task_flow.test_copy, "enabled", lambda: False)
    return calls


def _task(*, status="completed", export=True, **result) -> str:
    task_id = f"fx-{uuid.uuid4().hex[:8]}"
    job_store.upsert_job(task_id=task_id, status=status, source_filename="周会.mp4",
                         metadata={"queue_options": {"export_to_lark": "true" if export else "false"}})
    job_store.update_job_result(task_id, {"summary_markdown": "# 周会\n\n正文", **result})
    return task_id


def test_a_saved_agent_note_is_exported_once(exports):
    task_id = _task()
    thread = task_flow.export_agent_note_if_asked(task_id, None)
    assert thread is not None
    thread.join(5)
    assert len(exports) == 1 and exports[0]["summary_markdown"].startswith("# 周会")
    assert job_store.get_job(task_id)["result"]["lark_response"]["url"].endswith("abc")
    # Saved again: the document is there, Feishu sync carries later edits.
    assert task_flow.export_agent_note_if_asked(task_id, None) is None
    assert len(exports) == 1


@pytest.mark.parametrize("kwargs", [
    {"export": False},
    {"status": "processing"},
    {"lark_response": {"url": "u"}},
    {"lark_error": "没有权限"},
])
def test_not_exported(exports, kwargs):
    assert task_flow.export_agent_note_if_asked(_task(**kwargs), None) is None
    assert exports == []


def test_a_save_during_the_export_makes_no_second_document_and_reaches_feishu(monkeypatch):
    """Codex review, 2026-11-06: a part saved again while the first export
    ran started a second export, and a second document."""
    import threading

    release = threading.Event()
    calls: list[str] = []
    pushed: list[str] = []

    def slow(**values):
        calls.append(values["summary_markdown"])
        release.wait(5)
        return {"doc_title": "标题", "export_target": "lark_cli", "response": {"url": "u"}}

    monkeypatch.setattr(task_flow, "_auto_export_local_lark", slow)
    monkeypatch.setattr(task_flow.test_copy, "enabled", lambda: False)
    from backend.core import feishu_sync

    # As in use: a note has its Feishu document once the export recorded it.
    monkeypatch.setattr(feishu_sync, "recorded_sync",
                        lambda result: {"doc": "x"} if (result or {}).get("lark_response") else None)
    monkeypatch.setattr(feishu_sync, "schedule_push_when_quiet", lambda task_id, delay=None: pushed.append(task_id))

    task_id = _task()
    first = task_flow.export_agent_note_if_asked(task_id, None)
    assert first is not None
    while not calls:  # the export has begun
        time.sleep(0.01)
    job_store.update_job_result(task_id, {**job_store.get_job(task_id)["result"], "summary_markdown": "# 周会\n\n改过的正文"})
    assert task_flow.export_agent_note_if_asked(task_id, None) is None
    release.set()
    first.join(5)
    assert len(calls) == 1
    assert pushed == [task_id], "the note saved meanwhile goes to the same document"


def test_the_task_reads_as_working_until_the_document_is_there(monkeypatch):
    """Codex push review, 2026-11-06: a wait must not answer "done" while the
    export is still to come."""
    import threading

    from backend.core import task_activity

    release = threading.Event()
    monkeypatch.setattr(task_flow, "_auto_export_local_lark", lambda **values: (
        release.wait(5), {"doc_title": "t", "export_target": "lark_cli", "response": {"url": "u"}})[1])
    monkeypatch.setattr(task_flow.test_copy, "enabled", lambda: False)
    task_id = _task()
    thread = task_flow.export_agent_note_if_asked(task_id, None)
    from backend.core import task_steps

    assert task_steps.status_of(task_id, task_steps.FEISHU) == task_steps.RUNNING
    assert task_activity.activity(job_store.get_job(task_id)).busy
    release.set()
    thread.join(5)
    assert task_steps.status_of(task_id, task_steps.FEISHU) == task_steps.DONE
    assert not task_activity.activity(job_store.get_job(task_id)).busy


def test_a_restart_during_the_export_still_exports_the_note(exports, monkeypatch):
    """Codex push review, 2026-11-06: the export is a thread; a restart then
    lost it for good."""
    task_id = _task()
    # What a restart leaves: the mark on the task, no thread.
    job_store.update_job_result(task_id, {**job_store.get_job(task_id)["result"],
                                          task_flow.AGENT_EXPORT_PENDING: 1.0})
    assert task_flow.resume_agent_exports() == 1
    deadline = time.time() + 5
    while task_flow.AGENT_EXPORT_PENDING in job_store.get_job(task_id)["result"] and time.time() < deadline:
        time.sleep(0.02)
    assert len(exports) == 1
    assert task_flow.AGENT_EXPORT_PENDING not in job_store.get_job(task_id)["result"]
    assert task_flow.resume_agent_exports() == 0


def test_an_export_cut_off_by_a_restart_has_one_recovery(exports):
    """Codex push review, 2026-11-06: the step-by-step recovery also picked
    the open Feishu step up and settled it before the document came."""
    from backend.core import task_actions, task_steps

    task_id = _task()
    job_store.update_job_result(task_id, {**job_store.get_job(task_id)["result"],
                                          task_flow.AGENT_EXPORT_PENDING: 1.0})
    task_steps.start(task_id, task_steps.FEISHU)
    assert task_id in task_steps.interrupted()
    assert task_id not in task_actions.stepped_tasks_cut_off()
