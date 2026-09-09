from pathlib import Path

import backend.core.local_agent_package as local_agent_package
from backend.core.local_agent_package import build_agent_task_package


def _job(**overrides) -> dict:
    job = {
        "task_id": "t-1",
        "status": "completed",
        "stage": "done",
        "progress": 100,
        "created_at": "2026-07-29T10:00:00+08:00",
        "updated_at": "2026-07-29T10:05:00+08:00",
        "source_type": "video",
        "source_filename": "talk.mp4",
        "result": {
            "task_id": "t-1",
            "filename": "talk.mp4",
            "transcript_text": "大家好，今天讲本地版。",
            "summary_markdown": "# 笔记\n\n要点",
            "summary_status": "completed",
            "stt_provider": "local",
            "audio_duration_seconds": 12.5,
        },
        "metadata": {"route": "/process"},
    }
    job.update(overrides)
    return job


def test_local_package_has_no_cloud_sections():
    package = build_agent_task_package(_job())

    assert package["agent_task_package_version"] == "1"
    assert "usage" not in package  # no quota concepts locally
    assert "execution" not in package  # no desktop-sync concepts locally
    assert package["task"]["task_id"] == "t-1"
    assert package["note"]["status"] == "completed"
    assert package["note"]["markdown"].startswith("# 笔记")
    assert package["transcript"]["available"] is True
    assert package["processing_plan"]
    assert package["decision_log"]
    assert package["tool_trace"]["steps"]
    stt_steps = [s for s in package["tool_trace"]["steps"] if s["tool"] == "local_stt"]
    assert stt_steps and stt_steps[0]["vendor"] == "faster-whisper"


def test_failed_task_with_stored_source_offers_local_retry(monkeypatch):
    monkeypatch.setattr(
        local_agent_package, "find_source_file", lambda task_id: Path("/tmp/source.mp4")
    )
    package = build_agent_task_package(
        _job(status="failed", result={"task_id": "t-1", "transcript_text": ""})
    )
    actions = {action["action"]: action for action in package["next_actions"]}
    assert "retry_task" in actions
    assert actions["retry_task"]["path"] == "/agent/v1/tasks/t-1/retry"
    assert "本机" in actions["retry_task"]["reason"]


def test_failed_task_without_source_has_no_retry(monkeypatch):
    monkeypatch.setattr(local_agent_package, "find_source_file", lambda task_id: None)
    package = build_agent_task_package(
        _job(status="failed", result={"task_id": "t-1", "transcript_text": ""})
    )
    assert all(action["action"] != "retry_task" for action in package["next_actions"])
