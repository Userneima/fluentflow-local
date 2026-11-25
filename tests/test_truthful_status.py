"""What the service records so the page can say what it is doing (owner's run
of 2026-11-21, docs/01-待办.md「界面如实、及时显示后台在做什么」):

- when the task entered the stage it is at, so a wait on a model is said with
  its length (「正在按词表校正转录稿（已等 2 分 10 秒）」);
- a download's pace and time left, so slow and stalled read differently, and
  nothing (never 0) when the downloader did not say;
- the text model writing the note in Claude's place is a notice, recorded once
  per reason, not a failure;
- a transcription cut off by a restart uses the audio it already extracted.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core import job_store, local_intake_flow, media_job, video_source, visual_note_channel
from backend.core.video_source import VideoSourceProgress


@pytest.fixture()
def task_id():
    value = f"truthful-{uuid.uuid4().hex[:8]}"
    yield value
    job_store.delete_jobs([value])


def test_a_stage_change_records_when_it_started_and_staying_does_not_move_it(task_id, monkeypatch):
    times = iter(["2026-11-21T20:17:59+08:00", "2026-11-21T20:18:30+08:00", "2026-11-21T20:23:19+08:00"])
    monkeypatch.setattr(job_store, "_now_iso", lambda: next(times))
    job_store.upsert_job(task_id=task_id, status="running", stage="glossary_correction", progress=60)
    job_store.upsert_job(task_id=task_id, status="running", stage="glossary_correction", progress=60,
                         metadata={"other": 1})
    first = job_store.get_job(task_id)["metadata"]
    assert first[job_store.STAGE_STARTED_AT] == "2026-11-21T20:17:59+08:00", "staying in a stage keeps its start"
    assert first["other"] == 1
    job_store.upsert_job(task_id=task_id, status="running", stage="transcript_ready", progress=90)
    after = job_store.get_job(task_id)["metadata"]
    assert after[job_store.STAGE_STARTED_AT] == "2026-11-21T20:23:19+08:00"
    assert after["other"] == 1, "the rest of the metadata is kept"


def _reported(lines):
    reports: list[VideoSourceProgress] = []
    handle = video_source._yt_dlp_progress_reporter(reports.append)
    for line in lines:
        handle(line)
    return reports


def test_yt_dlp_progress_carries_its_pace_and_time_left():
    prefix = video_source._PROGRESS_PREFIX
    [report] = _reported([f"{prefix} 41943040 104857600 NA 3355443.2 80"])
    assert (report.speed_bps, report.eta_seconds) == (3355443, 80)
    # The template asks yt-dlp for them; NA (not measured yet) is nothing.
    assert "%(progress.speed)s %(progress.eta)s" in video_source._PROGRESS_TEMPLATE
    [early] = _reported([f"{prefix} 1024 104857600 NA NA NA"])
    assert early.speed_bps is None and early.eta_seconds is None
    [older] = _reported([f"{prefix} 1024 104857600 NA"])
    assert older.loaded_bytes == 1024 and older.speed_bps is None


def test_a_direct_download_measures_its_own_pace(monkeypatch):
    clock = iter([100.0, 104.0])
    monkeypatch.setattr(video_source.time, "monotonic", lambda: next(clock))
    pace = video_source._DownloadPace()
    assert pace.update(0, 10_000_000) == (None, None), "nothing to say before a window has passed"
    speed, eta = pace.update(4_000_000, 10_000_000)
    assert speed == 1_000_000 and eta == 6


@pytest.fixture()
def owed_task():
    task_id = f"owed-note-{uuid.uuid4().hex[:8]}"
    job_store.upsert_job(
        task_id=task_id, status="completed", client_id=None, stage="done",
        metadata={"queue_options": {"ai_provider": "deepseek"}},
        result={"task_id": task_id, "transcript_text": "今天讲三件事。", "summary_markdown": "",
                "summary_status": "skipped", "note_deferred_to_visual_note": True},
    )
    yield task_id
    job_store.delete_jobs([task_id])


NO_KEY = "还没有配置 Anthropic 的 API Key，无法请 Claude 看画面。"


@pytest.fixture()
def no_anthropic_key(monkeypatch):
    channel = SimpleNamespace(name="anthropic_api_key", available=False, unavailable_reason=NO_KEY)
    monkeypatch.setattr(visual_note_channel, "resolve_channel", lambda *_a, **_k: channel)
    import backend.core.ai_summarizer as summarizer
    import backend.core.local_entry_guards as guards
    monkeypatch.setattr(guards, "local_ai_kwargs", lambda **_kw: {"provider": "deepseek", "api_key": "sk-test"})
    monkeypatch.setattr(summarizer, "summarize_transcript_with_metadata",
                        lambda transcript, **kw: SimpleNamespace(markdown="# 三件事"))
    logged: list[dict] = []
    import backend.core.event_logger as event_logger
    monkeypatch.setattr(event_logger, "log_event", lambda **kw: logged.append(kw))
    return logged


def test_the_text_model_standing_in_is_a_notice(owed_task, no_anthropic_key, monkeypatch):
    import backend.core.event_logger as event_logger
    monkeypatch.setattr(event_logger, "list_task_events", lambda _task: [])
    local_intake_flow.write_note(owed_task, None)
    [event] = [e for e in no_anthropic_key if e["event_name"] == "visual_note_unavailable"]
    assert event["success"] is True and event["metadata"]["fallback"] == "text_note"
    assert job_store.get_job(owed_task)["result"]["summary_markdown"] == "# 三件事"


def test_the_same_reason_noted_at_submission_is_not_recorded_again(owed_task, no_anthropic_key, monkeypatch):
    import backend.core.event_logger as event_logger
    monkeypatch.setattr(event_logger, "list_task_events", lambda _task: [
        {"event_name": "visual_note_unavailable", "success": 1, "error_reason": NO_KEY},
    ])
    local_intake_flow.write_note(owed_task, None)
    assert not [e for e in no_anthropic_key if e["event_name"] == "visual_note_unavailable"]
    assert job_store.get_job(owed_task)["result"]["summary_markdown"] == "# 三件事"


def test_the_submission_notes_the_fallback_as_a_notice(monkeypatch):
    from backend.core import task_submission
    channel = SimpleNamespace(name="anthropic_api_key", available=False, unavailable_reason=NO_KEY)
    monkeypatch.setattr(task_submission.visual_note_channel, "resolve_channel", lambda *_a, **_k: channel)
    logged: list[dict] = []
    monkeypatch.setattr(task_submission, "log_event", lambda **kw: logged.append(kw))
    task_submission._log_visual_note_unavailable("t", source_type="video", source_filename="a.mp4")
    [event] = logged
    assert event["success"] is True


def _ctx(task_id, source, *, resumable=True):
    return SimpleNamespace(task_id_value=task_id, save_checkpoint=(lambda _c: None) if resumable else None)


def test_extracted_audio_is_used_again_when_the_transcription_is_carried_on(tmp_path, monkeypatch):
    monkeypatch.setattr(media_job, "_artifact_storage_dir", lambda: tmp_path / "artifacts")
    source = tmp_path / "talk.mp4"
    source.write_bytes(b"video")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "stt.wav").write_bytes(b"wav")
    (scratch / "playback.mp3").write_bytes(b"mp3")
    ctx = _ctx("t1", source)

    media_job._keep_audio_for_resume(ctx, source, out_audio=scratch / "stt.wav", playback=scratch / "playback.mp3",
                                     enhanced=None, voice_presence=None, stt_audio_take="plain")
    for leftover in scratch.iterdir():
        leftover.unlink()  # the run's scratch folder is gone after a restart
    kept = media_job._kept_audio_for_resume(ctx, source)
    assert kept is not None
    out_audio, playback, enhanced, presence, take = kept
    assert Path(out_audio).read_bytes() == b"wav" and Path(playback).read_bytes() == b"mp3"
    assert enhanced is None and take == "plain"
    # The transcription checkpoint keeps the same take without copying it onto itself.
    assert media_job._keep_for_resume("t1", playback) == str(playback)

    source.write_bytes(b"another video")
    assert media_job._kept_audio_for_resume(ctx, source) is None, "not for a changed file"
    assert media_job._kept_audio_for_resume(_ctx("t1", source, resumable=False), source) is None


def test_audio_is_not_kept_for_a_run_that_cannot_be_carried_on(tmp_path, monkeypatch):
    monkeypatch.setattr(media_job, "_artifact_storage_dir", lambda: tmp_path / "artifacts")
    source = tmp_path / "talk.mp4"
    source.write_bytes(b"video")
    wav = tmp_path / "stt.wav"
    wav.write_bytes(b"wav")
    media_job._keep_audio_for_resume(_ctx("t2", source, resumable=False), source, out_audio=wav, playback=None,
                                     enhanced=None, voice_presence=None, stt_audio_take="plain")
    assert not (tmp_path / "artifacts").exists()
