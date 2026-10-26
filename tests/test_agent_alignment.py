"""The user's current agent aligns; no local model, stale writes or note edits."""

import copy
import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import agent_alignment as aa, job_store, note_section_times as times
from backend.core.local_agent_package import _local_extra_next_actions
from backend.core.local_request_scope import LOCAL_OWNER_ID as OWNER
from backend.routers.local_agent import router
from scripts import fluentflow_mcp_server as mcp


@pytest.fixture
def task(monkeypatch):
    monkeypatch.setattr(times, "_enabled", False)
    monkeypatch.setattr(times, "_default_chat", lambda: pytest.fail("agent alignment called Claude"))
    task_id = "alignment-" + uuid.uuid4().hex
    job_store.upsert_job(task_id=task_id, status="completed", client_id=OWNER, result={
        "task_id": task_id, "summary_status": "completed",
        "summary_markdown": "# 会议\n\n## 一、进展\n稳定。\n\n### 发布\n已完成。\n\n## 二、后续\n下周再做。",
        "display_segments": [{"start": i * 10, "end": i * 10 + 10, "text": f"第{i}句话"} for i in range(12)],
        "playback_media_kind": "debreath_media", "debreath": {"cut_list_updated_at": "cut-1"},
    })
    yield task_id
    job_store.delete_jobs([task_id])


def job(task):
    return job_store.get_job(task)


def change(task, **changes):
    before = job(task)["result"]
    job_store.save_result_changes(task, before, {**before, **changes})


def material(task, **kw):
    return aa.material(task, client_id=OWNER, **kw)


def answers(packet):
    return [{"id": row["id"], "primary": {"start": "0:00:00", "end": 40},
             "others": [{"start": 60, "end": 80}]} for row in packet["sections"]]


def save(task, packet, sections=None):
    return aa.save(task, client_id=OWNER, material_version=packet["material_version"],
                   sections=answers(packet) if sections is None else sections, model="codex-current-session")


def test_agent_round_trip_only_changes_the_table_and_order_is_unchanged(task):
    before = job(task)
    packet = material(task)
    assert "=== 转录稿 ===" in packet["material"] and "〔S1〕" in packet["material"]
    assert packet["timeline"]["timeline"] == "debreath_media"
    assert times.align(task) == {"state": "skipped", "executor": "agent"}
    assert save(task, packet)["state"] == "ready"
    after = job(task)
    assert after["updated_at"] == before["updated_at"]
    for key, value in before["result"].items():
        assert after["result"][key] == value
    assert set(after["result"]) - set(before["result"]) == {times.RESULT_KEY}
    table = after["result"][times.RESULT_KEY]
    assert table["aligned_by"] == "agent" and table["model"] == "codex-current-session"
    assert table["sections"][0]["primary"] == [0.0, 40.0]
    assert material(task)["state"] == "ready"
    assert _local_extra_next_actions(after, {}) == []


def test_pages_are_lossless_and_bound_to_one_version(task, monkeypatch):
    monkeypatch.setattr(aa, "PAGE_CHARS", 150)
    first = material(task)
    joined, packet = first["material"], first
    while packet["next_offset"] is not None:
        packet = material(task, offset=packet["next_offset"], material_version=first["material_version"])
        assert packet["sections"] == first["sections"]
        joined += packet["material"]
    result = job(task)["result"]
    ask, _ = times.missing_sections(result)
    assert joined == times.build_prompt(result["summary_markdown"], ask, times.transcript_lines(result["display_segments"]), 120)
    with pytest.raises(aa.AlignmentError, match="已变化"):
        material(task, offset=first["next_offset"])
    change(task, summary_markdown=result["summary_markdown"] + "\n改了一句。")
    with pytest.raises(aa.AlignmentError, match="已变化"):
        material(task, offset=first["next_offset"], material_version=first["material_version"])


@pytest.mark.parametrize("changes", [
    {"summary_markdown": "## 改了标题\n新内容"},
    {"display_segments": [{"start": 0, "end": 120, "text": "修改了转录稿"}]},
    {"debreath": {"cut_list_updated_at": "cut-2"}},
    {"playback_media_kind": "source"},
    {times.RESULT_KEY: {"executor": "agent", "sections": []}},
])
def test_stale_material_never_overwrites_new_content_or_table(task, changes):
    packet = material(task)
    change(task, **changes)
    before = job(task)["result"]
    with pytest.raises(aa.AlignmentError) as caught:
        save(task, packet)
    assert caught.value.code == "stale"
    assert job(task)["result"] == before


def test_save_compares_the_basis_inside_the_write_transaction(task, monkeypatch):
    packet = material(task)
    real_save = job_store.save_result_changes
    def racing_write(task_id, before, after, **kw):
        real_save(task_id, before, {**before, "summary_markdown": "## 刚改过\n保留这句。"})
        return real_save(task_id, before, after, **kw)
    monkeypatch.setattr(job_store, "save_result_changes", racing_write)
    with pytest.raises(aa.AlignmentError) as caught:
        save(task, packet)
    assert caught.value.code == "stale"
    assert job(task)["result"]["summary_markdown"] == "## 刚改过\n保留这句。"
    assert not job(task)["result"][times.RESULT_KEY].get("sections")


@pytest.mark.parametrize("fault", ["missing", "duplicate", "unknown", "nan", "infinite", "negative", "backwards", "out_of_bounds", "bad_others", "bool", "rounds_to_zero"])
def test_invalid_answers_leave_the_task_unchanged(task, fault):
    packet = material(task)
    rows = answers(packet)
    if fault == "missing": rows.pop()
    elif fault == "duplicate": rows[-1]["id"] = rows[0]["id"]
    elif fault == "unknown": rows[-1]["id"] = "S999"
    elif fault == "bad_others": rows[0]["others"] = {}
    elif fault == "rounds_to_zero": rows[0]["primary"] = {"start": 0.01, "end": 0.02}
    else:
        rows[0]["primary"] = {"start": 0, "end": 40}
        key, value = {
            "nan": ("end", "NaN"), "infinite": ("end", "Infinity"),
            "negative": ("start", -1), "backwards": ("end", 0),
            "out_of_bounds": ("end", 121), "bool": ("start", False),
        }[fault]
        rows[0]["primary"][key] = value
    before = job(task)["result"]
    with pytest.raises(aa.AlignmentError):
        save(task, packet, rows)
    assert job(task)["result"] == before


def test_only_missing_section_is_requested_and_other_row_ids_survive(task):
    save(task, material(task))
    previous = copy.deepcopy(job(task)["result"][times.RESULT_KEY]["sections"])
    change(task, summary_markdown=job(task)["result"]["summary_markdown"].replace("### 发布", "### 新标题"))
    packet = material(task)
    assert [row["heading"] for row in packet["sections"]] == ["新标题"]
    save(task, packet)
    now = job(task)["result"][times.RESULT_KEY]["sections"]
    assert now[0] == previous[0] and now[2] == previous[2]
    assert now[1]["id"] != previous[1]["id"]


def test_background_retry_listener_and_late_answer_respect_agent(task, monkeypatch):
    old_result = job(task)["result"]
    queued = []
    monkeypatch.setattr(times, "_enabled", True)
    monkeypatch.setattr(times, "schedule_when_quiet", lambda *a, **kw: queued.append(a))
    monkeypatch.setattr(times, "schedule_soon", lambda *a, **kw: queued.append(a))
    packet = material(task)
    times.request(job(task)["result"])
    change(task, summary_markdown=old_result["summary_markdown"] + "\n改字。")
    assert not queued
    assert times.align(task)["executor"] == "agent"
    assert not times._save(task, old_result, {"error": "late Claude failure"})
    assert job(task)["result"][times.RESULT_KEY]["executor"] == "agent"
    assert packet["state"] == "needs_alignment"


def test_agent_written_note_does_not_start_background_alignment(task, monkeypatch):
    monkeypatch.setattr(times, "_enabled", True)
    monkeypatch.setattr(times, "schedule_when_quiet", lambda *a: pytest.fail("background call scheduled"))
    change(task, summary_written_from="agent_note", summary_markdown="## 新笔记\n发布了。")
    assert times.request(job(task)["result"])["executor"] == "agent"
    assert times.align(task)["state"] == "skipped"


def test_missing_note_transcript_busy_and_wrong_owner_are_refused(task):
    with pytest.raises(aa.AlignmentError) as caught:
        aa.material(task, client_id="another-owner")
    assert caught.value.status == 404
    for changes, code in [({"summary_markdown": ""}, "no_note"), ({"display_segments": []}, "no_transcript")]:
        before = job(task)["result"]
        change(task, **changes)
        with pytest.raises(aa.AlignmentError) as caught:
            material(task)
        assert caught.value.code == code
        change(task, **before)
    job_store.upsert_job(task_id=task, status="running", client_id=OWNER, result=job(task)["result"])
    with pytest.raises(aa.AlignmentError) as caught:
        material(task)
    assert caught.value.code == "busy"


def test_mcp_tools_use_authenticated_routes_and_report_stale(task, monkeypatch):
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    path = f"/agent/v1/tasks/{task}/agent-alignment/material"
    assert client.post(path).status_code == 403
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "test-agent-token")
    assert client.post(path).status_code == 401
    headers = {"x-fluentflow-access-token": "test-agent-token"}
    def bridge(method, path, **kw):
        response = client.request(method, path, json=kw.get("payload"), headers=headers)
        if response.status_code >= 400:
            return {"http_status": response.status_code, **response.json()}
        return response.json()
    monkeypatch.setattr(mcp, "_agent_request", bridge)
    packet = mcp.get_agent_alignment_material(task)
    assert packet["ok"]
    assert _local_extra_next_actions(job(task), {})[0]["action"] == "get_agent_alignment_material"
    result = mcp.save_agent_alignment(task, packet["material_version"], answers(packet), "codex-current-session")
    assert result["ok"] and result["state"] == "ready"
    stale = mcp.save_agent_alignment(task, packet["material_version"], answers(packet), "codex-current-session")
    assert stale["http_status"] == 409 and stale["detail"]["code"] == "stale"
    no_version = client.post(path.removesuffix("/material"), json={"sections": []}, headers=headers)
    assert no_version.status_code == 409 and no_version.json()["detail"]["code"] == "stale"
    info = client.get(f"/agent/v1/tasks/{task}", headers=headers).json()
    assert info["task"]["note_alignment"]["state"] == "ready"


def test_background_that_was_already_running_cannot_overwrite_agent_table(task):
    # Start as a web-only background call. The agent claims and saves while
    # that call is waiting for its model answer, then the old answer returns.
    def talk(system, prompt):
        packet = material(task)
        save(task, packet)
        return json.dumps({"sections": answers(packet)})
    assert times.align(task, chat=(talk, "claude_code_subscription", "opus"))["state"] == "skipped"
    assert job(task)["result"][times.RESULT_KEY]["aligned_by"] == "agent"
