"""The calling agent corrects the transcript and translates the subtitles.

Written from what the owner asked for (2026-11-03), not from the code:

- On an agent submission the three steps are left to the agent, and
  ``wait_task`` says so once the task is done; FluentFlow calls no model.
- The agent takes material, judges, and hands its answer back; what it hands
  back lands in the transcript and the subtitle files the way a background
  correction does, on a cut task in the original's copy too.
- A sentence the user edited is not changed by the agent's answer.
- The material carries a version: when the transcript changed meanwhile the
  save is refused as ``stale`` and nothing is written.
- Every change is recorded with ``by: agent`` and the model, and the existing
  list and undo tools show it and take it back, one or all.
- A finished task the background handled (or skipped) can be done again.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import agent_correction as ac
from backend.core import glossary_correction as gc
from backend.core import job_store, media_job, task_intelligence, task_submission
from backend.core import subtitle_correction as sc
from backend.core.local_agent_package import _local_extra_next_actions
from backend.core.local_request_scope import LOCAL_OWNER_ID
from backend.core.result_artifacts import _attach_result_artifacts
from backend.core.storage_paths import _artifact_storage_dir
from backend.routers import local_agent

TOKEN = "agent-correction-token"
AGENT = {"x-fluentflow-access-token": TOKEN, "x-fluentflow-client-id": LOCAL_OWNER_ID}

GLOSSARY = "- OpenClaw：开源 agent 框架。转录常听成：OpenCloud\n"

SEGMENTS = [
    {"start": 0.0, "end": 4.0, "text": "今天我们讲一下"},
    {"start": 4.0, "end": 8.0, "text": "我要研究OpenCloud这个东西"},
    {"start": 8.0, "end": 12.0, "text": "训练的应该是GOM跟KIMI"},
    {"start": 12.0, "end": 16.0, "text": "OpenCloud跑起来很快"},
]

ENGLISH = [
    {"start": 0.0, "end": 2.0, "text": "So today we will"},
    {"start": 2.0, "end": 4.0, "text": "talk about agents."},
    {"start": 4.0, "end": 7.0, "text": "They are useful."},
]


def _forbid_models(monkeypatch):
    def forbidden(*_a, **_kw):
        pytest.fail("the agent's correction must not call a FluentFlow model")

    monkeypatch.setattr(local_agent, "_glossary_chat", forbidden)
    monkeypatch.setattr(gc, "decide", forbidden)
    monkeypatch.setattr(sc, "decide", forbidden)


@pytest.fixture
def agent(monkeypatch, tmp_path):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", TOKEN)
    _forbid_models(monkeypatch)
    folder = tmp_path / "训练营"
    folder.mkdir()
    (folder / "FluentFlow 校正文档.md").write_text(GLOSSARY, encoding="utf-8")
    app = FastAPI()
    app.include_router(local_agent.router)
    created: list[str] = []

    def make(segments=SEGMENTS, status="completed", **extra):
        task_id = f"ac-{uuid.uuid4().hex[:8]}"
        result = _attach_result_artifacts(task_id, {
            "filename": "课.mp4", "display_title": "课",
            "raw_segments": [dict(seg) for seg in segments], "display_segments": [dict(seg) for seg in segments],
            "transcript_text": sc.joined(segments), gc.FINGERPRINTS: gc.fingerprints(segments),
            "subtitle_correction": task_intelligence.deferred_step(),
            gc.META: task_intelligence.deferred_step(), "intelligence": "agent", **extra,
        })
        job_store.upsert_job(task_id=task_id, status=status, client_id=LOCAL_OWNER_ID, result=result,
                             metadata={"folder_intake": {"original_path": str(folder / "课.mp4")}})
        created.append(task_id)
        return task_id

    yield TestClient(app), make
    job_store.delete_jobs(created)


def _post(client, path, body):
    return client.post(path, headers=AGENT, json=body)


def _material(client, task_id, step, page=1):
    response = _post(client, f"/agent/v1/tasks/{task_id}/agent-correction/material", {"step": step, "page": page})
    assert response.status_code == 200, response.text
    return response.json()


def _save(client, task_id, packet, **body):
    return _post(client, f"/agent/v1/tasks/{task_id}/agent-correction", {
        "step": packet["step"], "page": packet["page"], "material_version": packet["material_version"],
        "model": "claude-opus-5-5", **body,
    })


def _result(task_id):
    return job_store.get_job(task_id)["result"]


def _artifact(task_id, key):
    saved = _result(task_id)
    return (_artifact_storage_dir() / task_id / saved["artifacts"][key]["filename"]).read_text(encoding="utf-8")


def _store_track(task_id, cues, status="ready", **extra):
    """The subtitle track as the local reading leaves it beside the task."""
    path = _artifact_storage_dir() / task_id / ac.TRACK_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"status": status, "cues": cues, "video": None, **extra}, ensure_ascii=False),
                    encoding="utf-8")


@pytest.fixture
def no_video(monkeypatch):
    # The tests' tasks have no recording; the stored track stands for its reading.
    monkeypatch.setattr(ac, "played_video", lambda task_id, result: None)


# ── the word list ─────────────────────────────────────────────────────────

def test_the_agents_glossary_fixes_land_in_the_transcript_and_subtitle_files_and_are_undone(agent):
    client, make = agent
    task_id = make()
    packet = _material(client, task_id, "glossary")
    assert packet["glossary"].startswith("- OpenClaw")
    assert [row["text"] for row in packet["sentences"]] == [seg["text"] for seg in SEGMENTS]

    saved = _save(client, task_id, packet, fixes=[
        {"id": 1, "from": "OpenCloud", "to": "OpenClaw", "basis": "glossary"},
        {"id": 3, "from": "OpenCloud", "to": "OpenClaw", "basis": "glossary"},
    ]).json()

    assert len(saved["applied"]) == 2 and saved["refused"] == []
    result = _result(task_id)
    assert "我要研究OpenClaw这个东西" in result["transcript_text"]
    assert "我要研究OpenClaw这个东西" in [seg["text"] for seg in result["display_segments"]]
    assert "我要研究OpenClaw" in _artifact(task_id, "transcript_srt")
    assert result[gc.META]["executor"] == "agent" and result[gc.META]["model"] == "claude-opus-5-5"
    listed = client.get(f"/agent/v1/tasks/{task_id}/glossary-corrections", headers=AGENT).json()["corrections"]
    assert {(r["by"], r["model"], r["sentence"]) for r in listed} == {("agent", "claude-opus-5-5", 2), ("agent", "claude-opus-5-5", 4)}

    one = _post(client, f"/agent/v1/tasks/{task_id}/glossary-corrections/revert", {"ids": [listed[0]["id"]]}).json()
    assert one["reverted"] == [listed[0]["id"]]
    _post(client, f"/agent/v1/tasks/{task_id}/glossary-corrections/revert", {})
    assert _result(task_id)["transcript_text"] == sc.joined(SEGMENTS)
    assert "我要研究OpenCloud" in _artifact(task_id, "transcript_srt")


def test_a_fix_the_word_list_does_not_back_needs_a_reason_and_no_sentence_is_rewritten(agent):
    client, make = agent
    task_id = make()
    packet = _material(client, task_id, "glossary")

    saved = _save(client, task_id, packet, fixes=[
        {"id": 2, "from": "GOM", "to": "GLM", "basis": "context"},
        {"id": 0, "from": "今天我们讲一下", "to": "今天我们来详细讲讲这个话题", "basis": "context", "why": "通顺"},
        {"id": 2, "from": "KIMI", "to": "Kimi K2", "basis": "context", "why": "说的是月之暗面的模型"},
    ]).json()

    assert [r["to"] for r in saved["applied"]] == ["Kimi K2"]
    assert len(saved["refused"]) == 2
    assert _result(task_id)["raw_segments"][0]["text"] == "今天我们讲一下"


def test_a_sentence_the_user_edited_is_locked_and_the_agents_fix_for_it_refused(agent):
    client, make = agent
    task_id = make()
    before = _result(task_id)
    edited = [dict(seg) for seg in before["raw_segments"]]
    edited[1]["text"] = "我要研究OpenCloud这个东西，用户改过"
    job_store.save_result_changes(task_id, before, {**before, "raw_segments": edited, "transcript_text": sc.joined(edited)})

    packet = _material(client, task_id, "glossary")
    assert next(row for row in packet["sentences"] if row["id"] == 1).get("locked") is True
    saved = _save(client, task_id, packet, fixes=[
        {"id": 1, "from": "OpenCloud", "to": "OpenClaw", "basis": "glossary"},
        {"id": 3, "from": "OpenCloud", "to": "OpenClaw", "basis": "glossary"},
    ]).json()

    assert [r["to"] for r in saved["applied"]] == ["OpenClaw"] and len(saved["refused"]) == 1
    texts = [seg["text"] for seg in _result(task_id)["raw_segments"]]
    assert texts[1] == "我要研究OpenCloud这个东西，用户改过" and texts[3] == "OpenClaw跑起来很快"


def test_a_transcript_changed_after_the_material_was_read_is_refused_and_nothing_written(agent):
    client, make = agent
    task_id = make()
    packet = _material(client, task_id, "glossary")
    before = _result(task_id)
    edited = [dict(seg) for seg in before["raw_segments"]]
    edited[0]["text"] = "今天我们讲一下吧"
    job_store.save_result_changes(task_id, before, {**before, "raw_segments": edited, "transcript_text": sc.joined(edited)})
    moved = _result(task_id)

    answer = _save(client, task_id, packet, fixes=[{"id": 1, "from": "OpenCloud", "to": "OpenClaw", "basis": "glossary"}])

    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "stale"
    assert _result(task_id) == moved


def test_saving_one_page_does_not_make_the_next_stale(agent, monkeypatch):
    monkeypatch.setitem(ac.PAGE_SENTENCES, "glossary", 2)
    client, make = agent
    task_id = make()
    first, second = _material(client, task_id, "glossary", 1), _material(client, task_id, "glossary", 2)
    assert (first["pages"], first["next_page"], second["next_page"]) == (2, 2, None)

    one = _save(client, task_id, first, fixes=[{"id": 1, "from": "OpenCloud", "to": "OpenClaw", "basis": "glossary"}]).json()
    assert one["status"] == "partial" and one["pages_left"] == [2]
    two = _save(client, task_id, second, fixes=[{"id": 3, "from": "OpenCloud", "to": "OpenClaw", "basis": "glossary"}])

    assert two.status_code == 200, two.text
    assert two.json()["pages_left"] == [] and _result(task_id)[gc.META]["status"] == "completed"
    assert _result(task_id)[gc.META]["applied"] == 2


def test_refused_while_the_task_is_still_being_worked_on(agent):
    client, make = agent
    task_id = make(summary_status="pending")

    answer = _post(client, f"/agent/v1/tasks/{task_id}/agent-correction/material", {"step": "glossary"})

    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "task_busy"


def test_a_save_must_name_the_model(agent):
    client, make = agent
    task_id = make()
    packet = _material(client, task_id, "glossary")

    answer = _save(client, task_id, packet, fixes=[], model="")

    assert answer.status_code == 422


# ── the subtitles in the picture ──────────────────────────────────────────

CUES = [
    {"start": 7.5, "end": 12.5, "text": "训练的应该是GLM-5跟Kimi"},
    {"start": 3.5, "end": 8.5, "text": "我要研究OpenClaw这个东西"},
]


def test_the_agent_judges_sentences_the_picture_reads_differently_and_each_change_rests_on_a_line(agent, no_video):
    client, make = agent
    task_id = make()
    _store_track(task_id, CUES)
    packet = _material(client, task_id, "subtitles")
    assert packet["state"] == "ready"
    item = next(i for i in packet["items"] if i["id"] == 2)
    assert "训练的应该是GLM-5跟Kimi" in item["subtitles"]

    saved = _save(client, task_id, packet, fixes=[
        {"id": 2, "from": "GOM", "to": "GLM-5"},
        {"id": 2, "from": "KIMI", "to": "Kimi K3"},  # not what the picture says
    ]).json()

    assert [r["to"] for r in saved["applied"]] == ["GLM-5"] and len(saved["refused"]) == 1
    result = _result(task_id)
    assert result["raw_segments"][2]["text"] == "训练的应该是GLM-5跟KIMI"
    assert "GLM-5" in _artifact(task_id, "transcript_srt")
    listed = client.get(f"/agent/v1/tasks/{task_id}/subtitle-corrections", headers=AGENT).json()
    record = listed["corrections"][0]
    assert (record["by"], record["model"], record["subtitle"]) == ("agent", "claude-opus-5-5", "训练的应该是GLM-5跟Kimi")
    assert listed["subtitle_correction"]["executor"] == "agent"

    _post(client, f"/agent/v1/tasks/{task_id}/subtitle-corrections/revert", {})
    assert _result(task_id)["raw_segments"][2]["text"] == "训练的应该是GOM跟KIMI"


def test_the_picture_is_read_on_first_ask_and_the_material_follows(agent, monkeypatch):
    client, make = agent
    task_id = make()
    monkeypatch.setattr(ac, "played_video", lambda task_id, result: None)
    monkeypatch.setattr(ac, "read_picture", lambda video, segments: {"status": "ready", "cues": CUES})

    first = _material(client, task_id, "subtitles")
    assert first["state"] == "reading"
    for _ in range(100):
        if task_id not in ac._reading:
            break
        asyncio.run(asyncio.sleep(0.02))
    again = _material(client, task_id, "subtitles")

    assert again["state"] == "ready" and {i["id"] for i in again["items"]} == {1, 2}


def test_a_video_without_subtitles_is_recorded_done_in_one_save(agent, no_video, monkeypatch):
    monkeypatch.setitem(ac.PAGE_SENTENCES, "subtitles", 2)
    client, make = agent
    task_id = make()
    _store_track(task_id, [], status="skipped", reason="no_burned_subtitles")
    packet = _material(client, task_id, "subtitles")
    assert packet["state"] == "nothing_to_do"

    saved = _save(client, task_id, packet, fixes=[]).json()

    assert saved["pages_left"] == []
    assert _result(task_id)["subtitle_correction"]["status"] == "skipped"
    assert ac.SUBTITLES not in ac.pending(_result(task_id))


# ── a cut task ────────────────────────────────────────────────────────────

def test_on_a_cut_task_the_original_copy_follows_so_a_rerender_keeps_the_fix(agent):
    client, make = agent
    source = [{**seg, "start": seg["start"] + 10, "end": seg["end"] + 10} for seg in SEGMENTS]
    task_id = make(source_raw_segments=source, source_display_segments=[dict(s) for s in source],
                   transcript_media="debreath_media")
    packet = _material(client, task_id, "glossary")

    _save(client, task_id, packet, fixes=[{"id": 1, "from": "OpenCloud", "to": "OpenClaw", "basis": "glossary"}])

    result = _result(task_id)
    assert result["source_raw_segments"][1]["text"] == "我要研究OpenClaw这个东西"
    assert result[gc.FIELD][0]["source_start"] == 14.0
    _post(client, f"/agent/v1/tasks/{task_id}/glossary-corrections/revert", {})
    assert _result(task_id)["source_raw_segments"][1]["text"] == "我要研究OpenCloud这个东西"


# ── translation ───────────────────────────────────────────────────────────

def test_the_agents_translation_becomes_the_bilingual_subtitles(agent):
    client, make = agent
    task_id = make(ENGLISH, source_language="en", translation_status="agent_required")
    packet = _material(client, task_id, "translation")
    assert [s["text"] for s in packet["sentences"]] == [s["text"] for s in ENGLISH]

    saved = _save(client, task_id, packet, translations=[
        {"start_id": 0, "end_id": 1, "text_en": "So today we will talk about agents.", "text_zh": "今天我们来讲 agent。"},
        {"start_id": 2, "end_id": 2, "text_zh": "它们很有用。"},
    ]).json()

    assert saved["translated"] == 2
    result = _result(task_id)
    assert [(s["start"], s["end"], s.get("text_zh")) for s in result["display_segments"]] == [
        (0.0, 4.0, "今天我们来讲 agent。"), (4.0, 7.0, "它们很有用。")]
    assert result["subtitle_mode"] == "bilingual_zh" and result["translation_status"] == "completed"
    assert result[ac.TRANSLATION_FIELD]["model"] == "claude-opus-5-5"
    assert "今天我们来讲 agent。" in _artifact(task_id, "transcript_bilingual_srt")
    assert result["raw_segments"] == [dict(s) for s in ENGLISH]
    assert ac.TRANSLATION not in ac.pending(result)


def test_overlapping_or_foreign_ranges_are_refused(agent, monkeypatch):
    monkeypatch.setitem(ac.PAGE_SENTENCES, "translation", 2)
    client, make = agent
    task_id = make(ENGLISH, source_language="en", translation_status="agent_required")
    packet = _material(client, task_id, "translation")

    saved = _save(client, task_id, packet, translations=[
        {"start_id": 0, "end_id": 1, "text_zh": "今天我们来讲 agent。"},
        {"start_id": 1, "end_id": 1, "text_zh": "重叠"},
        {"start_id": 2, "end_id": 2, "text_zh": "下一页的句子"},
    ]).json()

    assert saved["translated"] == 1 and len(saved["refused"]) == 2
    display = _result(task_id)["display_segments"]
    assert [s.get("text_zh") for s in display] == ["今天我们来讲 agent。", None]
    assert _result(task_id)["translation_status"] == "partial"


def test_a_recording_that_is_not_english_needs_no_translation(agent):
    client, make = agent
    task_id = make(source_language="zh")

    assert _material(client, task_id, "translation")["state"] == "nothing_to_do"


def test_a_translation_on_a_cut_task_is_kept_on_the_originals_clock_too(agent):
    client, make = agent
    source = [{**seg, "start": seg["start"] + 10, "end": seg["end"] + 10} for seg in ENGLISH]
    task_id = make(ENGLISH, source_language="en", source_raw_segments=source, transcript_media="debreath_media")
    packet = _material(client, task_id, "translation")

    _save(client, task_id, packet, translations=[{"start_id": 0, "end_id": 2, "text_zh": "整段"}])

    assert [(s["start"], s["end"]) for s in _result(task_id)["source_display_segments"]] == [(10.0, 17.0)]


# ── telling the agent ─────────────────────────────────────────────────────

def test_wait_task_tells_the_agent_once_the_task_is_done_and_stops_when_all_is_in(agent, no_video, monkeypatch):
    client, make = agent
    task_id = make(summary_markdown="# 笔记\n\n正文", summary_status="completed")
    monkeypatch.setattr(ac, "start_reading", lambda task_id, result, reader=None: "started")

    told = _post(client, f"/agent/v1/tasks/{task_id}/wait", {"timeout_seconds": 0}).json()
    assert told["done"] is True and "get_agent_correction_material" in told["next"]
    assert "step=subtitles" in told["next"] and "step=glossary" in told["next"]
    assert client.get(f"/agent/v1/tasks/{task_id}", headers=AGENT).json()["task"]["agent_corrections_left"] == [
        "subtitles", "glossary"]
    assert _local_extra_next_actions(job_store.get_job(task_id), {})[0]["action"] == "get_agent_correction_material"

    _store_track(task_id, CUES)
    _save(client, task_id, _material(client, task_id, "subtitles"), fixes=[])
    _save(client, task_id, _material(client, task_id, "glossary"), fixes=[])

    after = _post(client, f"/agent/v1/tasks/{task_id}/wait", {"timeout_seconds": 0}).json()
    assert "get_agent_correction_material" not in (after.get("next") or "")
    assert ac.pending(_result(task_id)) == []


def test_a_task_the_background_handled_can_be_done_again_by_the_agent(agent):
    client, make = agent
    task_id = make(**{gc.META: {"status": "skipped", "reason": "no_model"}, "intelligence": "fluentflow",
                      "subtitle_correction": {"status": "skipped", "reason": "no_burned_subtitles"}})
    assert ac.pending(_result(task_id)) == []

    packet = _material(client, task_id, "glossary")
    saved = _save(client, task_id, packet, fixes=[{"id": 1, "from": "OpenCloud", "to": "OpenClaw", "basis": "glossary"}])

    assert saved.status_code == 200 and "OpenClaw" in _result(task_id)["transcript_text"]


def test_an_agent_submission_leaves_all_three_to_the_agent(monkeypatch, tmp_path):
    """The real pipeline after transcription, on an English recording."""
    def forbidden(*_a, **_kw):
        pytest.fail("agent mode must not reach a model stage")

    monkeypatch.setattr(media_job, "_run_subtitle_correction_stage", forbidden)
    monkeypatch.setattr(media_job, "_run_glossary_correction_stage", forbidden)
    monkeypatch.setattr(media_job, "generate_bilingual_segments_zh", forbidden)
    monkeypatch.setattr(media_job, "summarize_transcript_with_metadata", forbidden)
    task_id = "agent-correction-pipeline-" + uuid.uuid4().hex[:6]
    segments = [{"start": 0.0, "end": 4.0, "text": "This is an English research lecture."}]

    async def run():
        ctx = task_submission._local_media_job_context(
            task_id=task_id, client_id="anonymous", source_type="video", source_filename="course.mp4",
            raw_title="course", display_title="course", suffix=".mp4", td=str(tmp_path),
            in_path=tmp_path / "course.mp4", content=b"", source_file_size_mb=1,
            duration_preflight_sec=5, options=task_intelligence.agent_options(),
        )
        ctx.ai_kwargs_builder = forbidden
        ctx.finalize_result_storage = lambda _task, result, _meta: result
        ctx.transcribe_checkpoint = {
            "base_result": {}, "tr": {"text": segments[0]["text"], "language": "en"},
            "segments_payload": segments, "transcript_text": segments[0]["text"],
            "in_path": str(tmp_path / "course.mp4"), "duration_sec": 5,
        }
        await media_job.execute_media_job(ctx)

    try:
        asyncio.run(run())
        result = job_store.get_job(task_id)["result"]
        assert ac.pending(result) == ["subtitles", "glossary", "translation"]
        assert "get_agent_correction_material" in ac.next_hint(result)
    finally:
        job_store.delete_jobs([task_id])


def test_the_mcp_tools_reach_these_routes(monkeypatch):
    from scripts import fluentflow_mcp_server as mcp

    calls: list[tuple] = []
    monkeypatch.setattr(mcp, "_agent_request",
                        lambda method, path, **kw: calls.append((method, path, kw.get("payload"))) or {"ok": True})

    mcp.get_agent_correction_material("t1", "glossary", page=2)
    mcp.save_agent_correction("t1", "glossary", 2, "v", "m", fixes=[])

    assert calls == [
        ("POST", "/agent/v1/tasks/t1/agent-correction/material", {"step": "glossary", "page": 2}),
        ("POST", "/agent/v1/tasks/t1/agent-correction",
         {"step": "glossary", "page": 2, "material_version": "v", "model": "m", "fixes": []}),
    ]
    names = {tool["name"] for tool in mcp.TOOL_DEFINITIONS}
    assert {"get_agent_correction_material", "save_agent_correction"} <= names
