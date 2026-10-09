"""A task owed a note gets one, or a reason, even if Claude went away.

Requirement: a recording submitted while the frame note could run had its own
text note switched off. If Claude's login expires (or the key is removed)
before its note step, the task must not end silently without a note:

- with a text-model key configured, it gets the text note instead, and the
  result says why;
- without one, it is marked failed with a reason the user can act on;
- either way the event log records that the frame note could not run.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from backend.core import job_store, local_intake_flow, visual_note_channel


@pytest.fixture()
def owed_task():
    task_id = f"owed-note-{uuid.uuid4().hex[:8]}"
    job_store.upsert_job(
        task_id=task_id, status="completed", client_id=None, stage="done",
        metadata={"queue_options": {"ai_provider": "deepseek", "ai_model": "deepseek-chat"}},
        result={
            "task_id": task_id,
            "transcript_text": "今天讲三件事。第一，排队。第二，笔记。第三，导出。",
            "summary_markdown": "",
            "summary_status": "skipped",
            "note_deferred_to_visual_note": True,
        },
    )
    yield task_id
    job_store.delete_jobs([task_id])


@pytest.fixture()
def claude_unreachable(monkeypatch):
    channel = SimpleNamespace(
        name="claude_code_subscription", available=False,
        unavailable_reason="这台机器上的 Claude 还没有登录，或者登录已经过期。",
    )
    monkeypatch.setattr(visual_note_channel, "resolve_channel", lambda *_a, **_k: channel)
    monkeypatch.setattr(local_intake_flow, "auto_note_will_run", lambda: False)
    events: list[dict] = []
    import backend.core.event_logger as event_logger
    monkeypatch.setattr(event_logger, "log_event", lambda **kw: events.append(kw))
    return events


def test_a_deferred_note_is_still_owed_after_claude_went_away(owed_task, claude_unreachable):
    assert local_intake_flow.note_is_wanted(owed_task, None) is True


def test_it_falls_back_to_the_text_note_and_says_why(owed_task, claude_unreachable, monkeypatch):
    import backend.core.ai_summarizer as summarizer
    import backend.core.local_entry_guards as guards

    monkeypatch.setattr(guards, "local_ai_kwargs", lambda **_kw: {"provider": "deepseek", "api_key": "sk-test"})
    monkeypatch.setattr(summarizer, "summarize_transcript_with_metadata",
                        lambda transcript, **kw: SimpleNamespace(markdown="# 三件事\n\n排队、笔记、导出。"))
    handed_off: list[bool] = []

    local_intake_flow.write_note(owed_task, None, on_local_work_done=lambda: handed_off.append(True))

    job = job_store.get_job(owed_task)
    assert job["result"]["summary_markdown"].startswith("# 三件事")
    assert job["result"]["summary_status"] == "completed"
    assert "登录" in job["result"]["note_fallback_reason"]
    assert handed_off == [True], "the queue is released before waiting on the text model"
    [event] = [e for e in claude_unreachable if e["event_name"] == "visual_note_unavailable"]
    assert event["metadata"]["fallback"] == "text_note"


def test_without_a_text_key_it_fails_with_a_reason_instead_of_staying_silent(owed_task, claude_unreachable, monkeypatch):
    import backend.core.local_entry_guards as guards

    monkeypatch.setattr(guards, "local_ai_kwargs", lambda **_kw: {"provider": "deepseek"})

    local_intake_flow.write_note(owed_task, None)

    result = job_store.get_job(owed_task)["result"]
    assert result["summary_status"] == "failed"
    assert "登录" in result["summary_error"] and "Key" in result["summary_error"]


def test_a_task_that_asked_for_a_transcript_only_is_still_left_alone(claude_unreachable):
    task_id = f"transcript-only-{uuid.uuid4().hex[:8]}"
    job_store.upsert_job(task_id=task_id, status="completed", client_id=None, result={
        "transcript_text": "你好", "summary_markdown": "", "summary_status": "skipped",
        "note_deferred_to_visual_note": False,
    })
    try:
        assert local_intake_flow.note_is_wanted(task_id, None) is False
    finally:
        job_store.delete_jobs([task_id])


# The text note written instead is the note the user asked for: their prompt,
# note mode and preset, the speaker labels the pipeline's own note stage would
# have used, and a note file they can download.

def test_the_text_note_written_instead_keeps_the_users_settings_and_speakers(claude_unreachable, monkeypatch):
    import backend.core.ai_summarizer as summarizer

    task_id = f"owed-settings-{uuid.uuid4().hex[:8]}"
    job_store.upsert_job(
        task_id=task_id, status="completed", client_id=None, stage="done",
        metadata={"queue_options": {
            "ai_provider": "deepseek", "ai_model": "deepseek-chat",
            "system_prompt": "只列行动项", "note_mode": "meeting",
            "prompt_preset": "action_items", "prompt_preset_label": "行动项",
        }},
        result={
            "task_id": task_id,
            "display_title": "周会",
            "transcript_text": "我们下周发版。好的我来写公告。",
            "raw_segments": [
                {"start": 0.0, "end": 2.0, "text": "我们下周发版。", "speaker": "SPEAKER_00"},
                {"start": 2.0, "end": 4.0, "text": "好的我来写公告。", "speaker": "SPEAKER_01"},
            ],
            "speaker_diarization": {"status": "completed"},
            "summary_markdown": "",
            "summary_status": "skipped",
            "note_deferred_to_visual_note": True,
        },
    )
    import backend.core.local_entry_guards as guards
    monkeypatch.setattr(guards, "resolve_secret", lambda value, name: "sk-test" if name == "deepseek_api_key" else value)
    called: dict = {}

    def summarize(transcript, **kw):
        called.update(transcript=transcript, **kw)
        return SimpleNamespace(markdown="# 行动项\n\n- 写公告", requested_mode="meeting", resolved_mode="meeting")

    monkeypatch.setattr(summarizer, "summarize_transcript_with_metadata", summarize)
    try:
        local_intake_flow.write_note(task_id, None)

        assert called["system_prompt"] == "只列行动项"
        assert called["note_mode"] == "meeting"
        assert called["speaker_labeled"] is True
        assert "说话人 A：" in called["transcript"] and "说话人 B：" in called["transcript"]
        result = job_store.get_job(task_id)["result"]
        assert result["summary_markdown"].startswith("# 行动项")
        assert result["prompt_preset"] == "action_items"
        assert result["prompt_preset_label"] == "行动项"
        assert result["speaker_diarization"]["note_input_labeled"] is True
        note_file = result["artifacts"]["summary_md"]
        assert note_file and note_file.get("filename", "").endswith("_summary.md")
    finally:
        job_store.delete_jobs([task_id])
