from pathlib import Path

import backend.core.local_task_detail as local_task_detail
from backend.core.local_task_detail import build_task_detail, build_task_snapshot


def test_local_task_snapshot_exposes_only_local_execution_semantics():
    snapshot = build_task_snapshot(
        {
            "task_id": "local-1",
            "status": "completed",
            "source_type": "video",
            "result": {
                "transcript_text": "Transcript",
                "summary_markdown": "# Note",
                "stt_provider": "local",
                "stt_model": "small",
            },
        }
    )

    assert snapshot["route"]["transcription"] == "local"
    assert snapshot["route"]["stt_provider"] == "local"
    assert "ai_note_requires_account" not in snapshot["route"]
    transcription = next(step for step in snapshot["steps"] if step["id"] == "transcription")
    assert transcription["detail"] == "本地 · faster-whisper / small"


def test_local_task_detail_uses_local_recovery_guidance():
    detail = build_task_detail(
        {
            "task_id": "local-2",
            "status": "failed",
            "stage": "summary",
            "source_type": "video",
            "error_reason": "invalid_api_key",
            "result": {"transcript_text": "Transcript"},
        }
    )

    assert detail["diagnosis"]["code"] == "invalid_api_key"
    assert "设置页" in detail["diagnosis"]["next_action"]
    assert "登录" not in detail["diagnosis"]["next_action"]


def test_local_task_detail_does_not_offer_hosted_source_retry():
    detail = build_task_detail(
        {
            "task_id": "local-3",
            "status": "failed",
            "stage": "source_fetch",
            "source_type": "video",
            "error_reason": "download failed",
            "metadata": {
                "source_storage": "oss",
                "oss_upload_session_id": "private-session",
            },
        }
    )

    action_ids = {action["id"] for action in detail["actions"]}
    assert "retry" not in action_ids
    assert "resubmit" in action_ids


def test_local_task_detail_offers_retry_when_stored_source_exists(monkeypatch):
    monkeypatch.setattr(
        local_task_detail, "find_source_file",
        lambda task_id: Path("/tmp/source.mp4") if task_id == "local-4" else None,
    )

    detail = build_task_detail(
        {
            "task_id": "local-4",
            "status": "failed",
            "stage": "stt",
            "source_type": "video",
            "error_reason": "boom",
            "result": {},
        }
    )

    action_ids = {action["id"] for action in detail["actions"]}
    assert "retry" in action_ids
