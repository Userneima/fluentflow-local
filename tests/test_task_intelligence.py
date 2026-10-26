"""Agent requests use their current host even when paid models are configured."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import job_store, media_job, task_intelligence, task_submission
from backend.routers import local_agent
from scripts import fluentflow_mcp_server as mcp


def test_legacy_client_defaults_do_not_delegate_to_background_models():
    options = task_intelligence.agent_options({"skip_summary": False, "cut_judge": "auto"})
    assert options == {"intelligence": "agent", "skip_summary": "true", "cut_judge": "agent"}


def test_explicit_delegation_keeps_transcript_only_choice():
    assert task_intelligence.agent_options({"intelligence": "fluentflow"}) == {
        "intelligence": "fluentflow", "skip_summary": "false", "cut_judge": "fluentflow",
    }
    assert task_intelligence.agent_options({"intelligence": "fluentflow", "skip_summary": True})["skip_summary"] is True
    assert not task_intelligence.uses_agent({})  # historical/page tasks
    with pytest.raises(ValueError):
        task_intelligence.agent_options({"intelligence": "unknown"})


@pytest.mark.parametrize("name,value", [
    ("submit_local_media", "/Users/me/course.mp4"),
    ("submit_video_link", "https://example.com/video"),
    ("submit_transcript", "An entire transcript."),
])
def test_every_mcp_submission_and_schema_default_to_current_agent(monkeypatch, name, value):
    sent = []
    monkeypatch.setattr(mcp, "_require_local_edition", lambda *_: None)
    monkeypatch.setattr(mcp, "_agent_request", lambda *_a, **kw: sent.append(kw["payload"]) or {"ok": True})
    monkeypatch.setattr(mcp, "_watching", lambda answer, *_: answer)
    submit = getattr(mcp, name)
    submit(value)
    submit(value, intelligence="fluentflow")
    assert sent[0]["options"]["intelligence"] == "agent"
    assert sent[0]["options"]["skip_summary"] == "true"
    assert sent[0]["options"]["cut_judge"] == "agent"
    assert sent[1]["options"]["skip_summary"] == "false"
    assert sent[1]["options"]["cut_judge"] == "fluentflow"
    schema = next(t for t in mcp.TOOL_DEFINITIONS if t["name"] == name)["inputSchema"]["properties"]
    assert schema["intelligence"]["default"] == "agent"


def test_direct_agent_api_also_defaults_to_agent_and_rejects_invalid_executor(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "test-agent-first")
    seen = []

    async def submit(**kwargs):
        seen.append(kwargs)
        return {"task_id": "agent-default", "status": "queued"}

    monkeypatch.setattr(local_agent, "submit_video_source_job", submit)
    app = FastAPI()
    app.include_router(local_agent.router)
    client = TestClient(app)
    headers = {"x-fluentflow-access-token": "test-agent-first"}
    response = client.post("/agent/v1/tasks", headers=headers, json={"input": "https://example.com/v"})
    assert response.status_code == 200
    assert seen[0]["extra_metadata"]["cut_judge"] == "agent"
    assert seen[0]["raw_options"]["skip_summary"] == "true"
    bad = client.post("/agent/v1/tasks", headers=headers, json={
        "input": "https://example.com/v", "options": {"intelligence": "unknown"},
    })
    assert bad.status_code == 422 and len(seen) == 1


def test_resumed_media_pipeline_does_not_correct_translate_or_write_with_a_model(monkeypatch, tmp_path):
    """Exercise the real pipeline after STT, including English's hidden translation call."""
    def forbidden(*_a, **_kw):
        pytest.fail("agent mode must not reach a model stage or credential resolver")

    monkeypatch.setattr(media_job, "_run_subtitle_correction_stage", forbidden)
    monkeypatch.setattr(media_job, "_run_glossary_correction_stage", forbidden)
    monkeypatch.setattr(media_job, "generate_bilingual_segments_zh", forbidden)
    monkeypatch.setattr(media_job, "summarize_transcript_with_metadata", forbidden)
    task_id = "agent-first-resumed-media"
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
        job = job_store.get_job(task_id)
        assert job["status"] == "completed", job
        result = job["result"]
        assert result["subtitle_correction"]["reason"] == "agent_required"
        assert result["glossary_correction"]["reason"] == "agent_required"
        assert result["translation_status"] == "agent_required"
        assert result["display_segments"] == segments
        assert result["summary_markdown"] == "" and result["summary_skipped"]
    finally:
        job_store.delete_jobs([task_id])
