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


def test_a_task_put_back_in_the_queue_counts_its_wait_from_then(task_id, monkeypatch):
    # Carried on after a stop or a restart (``reopen_job``): the card must not
    # say it has waited since the stage it was cut off in began.
    times = iter(["2026-11-21T20:17:59+08:00", "2026-11-21T21:05:00+08:00"])
    monkeypatch.setattr(job_store, "_now_iso", lambda: next(times))
    job_store.upsert_job(task_id=task_id, status="running", stage="glossary_correction", progress=60,
                         metadata={"other": 1})
    assert job_store.reopen_job(task_id)
    job = job_store.get_job(task_id)
    assert job["stage"] == "queued"
    assert job["metadata"][job_store.STAGE_STARTED_AT] == "2026-11-21T21:05:00+08:00"
    assert job["metadata"]["other"] == 1


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
    # yt-dlp can print inf for a rate or time it cannot tell.
    [endless] = _reported([f"{prefix} 1024 104857600 NA inf inf"])
    assert endless.loaded_bytes == 1024 and endless.speed_bps is None and endless.eta_seconds is None


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


LOGIN_EXPIRED = "本机 Claude 的登录已过期，在终端运行 `claude` 重新登录。"


def _submission_event(monkeypatch, channel, *, text_model_ready, login_state=None):
    from backend.core import claude_code_note, task_submission
    monkeypatch.setattr(task_submission.visual_note_channel, "resolve_channel", lambda *_a, **_k: channel)
    monkeypatch.setattr(claude_code_note, "login_state", lambda **_kw: login_state)
    logged: list[dict] = []
    monkeypatch.setattr(task_submission, "log_event", lambda **kw: logged.append(kw))
    task_submission._log_visual_note_unavailable("t", source_type="video", source_filename="a.mp4",
                                                 text_model_ready=text_model_ready)
    [event] = logged
    return event


def test_the_submission_tells_a_missing_key_an_expired_login_and_no_model_apart(monkeypatch):
    # Review, 2026-11-26: every case was a green successful fallback.
    no_key = SimpleNamespace(name="anthropic_api_key", unavailable_reason=NO_KEY)
    expired = SimpleNamespace(name=visual_note_channel.CHANNEL_SUBSCRIPTION, unavailable_reason=LOGIN_EXPIRED)

    missing_key = _submission_event(monkeypatch, no_key, text_model_ready=True)
    assert missing_key["success"] is True, "the ordinary setup without Claude is a notice (6.86)"
    assert missing_key["metadata"]["fallback"] == "text_note"

    login = _submission_event(monkeypatch, expired, text_model_ready=True, login_state=False)
    assert login["success"] is False, "an expired login is the user's to fix"
    assert login["metadata"]["reason"] == "login_expired" and login["metadata"]["fallback"] == "text_note"

    nothing = _submission_event(monkeypatch, no_key, text_model_ready=False)
    assert nothing["success"] is False and nothing["metadata"]["fallback"] == "none"


@pytest.fixture()
def event_log(monkeypatch):
    """The event log as one list both the submission and the note step write
    to and the note step reads back, the way the database holds it."""
    import backend.core.event_logger as event_logger
    from backend.core import task_submission
    logged: list[dict] = []
    monkeypatch.setattr(task_submission, "log_event", lambda **kw: logged.append(kw))
    monkeypatch.setattr(event_logger, "log_event", lambda **kw: logged.append(kw))
    monkeypatch.setattr(event_logger, "list_task_events", lambda _task: [
        {"event_name": e["event_name"], "success": int(bool(e["success"])), "error_reason": e["error_reason"]}
        for e in logged
    ])
    return logged


def _submit_then_write(owed_task, monkeypatch, channel, *, text_key, login_state=None):
    import backend.core.ai_summarizer as summarizer
    import backend.core.local_entry_guards as guards
    from backend.core import claude_code_note, task_submission
    monkeypatch.setattr(visual_note_channel, "resolve_channel", lambda *_a, **_k: channel)
    monkeypatch.setattr(claude_code_note, "login_state", lambda **_kw: login_state)
    kwargs = {"provider": "deepseek", "api_key": "sk-test"} if text_key else {"provider": "deepseek"}
    monkeypatch.setattr(guards, "local_ai_kwargs", lambda **_kw: kwargs)
    monkeypatch.setattr(summarizer, "summarize_transcript_with_metadata",
                        lambda transcript, **kw: SimpleNamespace(markdown="# 三件事"))
    task_submission._log_visual_note_unavailable(owed_task, source_type="video", source_filename="a.mp4",
                                                 text_model_ready=text_key)
    local_intake_flow.write_note(owed_task, None)


def _unavailable(log):
    return [e for e in log if e["event_name"] == "visual_note_unavailable"]


def test_an_expired_login_stays_one_failure_through_the_note_step(owed_task, event_log, monkeypatch):
    expired = SimpleNamespace(name=visual_note_channel.CHANNEL_SUBSCRIPTION, available=False, unavailable_reason=LOGIN_EXPIRED)
    _submit_then_write(owed_task, monkeypatch, expired, text_key=True, login_state=False)
    [event] = _unavailable(event_log)
    assert event["success"] is False and event["metadata"]["reason"] == "login_expired"
    assert job_store.get_job(owed_task)["result"]["summary_markdown"] == "# 三件事"


def test_no_model_at_all_is_one_failure_never_a_successful_fallback(owed_task, event_log, monkeypatch):
    no_key = SimpleNamespace(name="anthropic_api_key", available=False, unavailable_reason=NO_KEY)
    _submit_then_write(owed_task, monkeypatch, no_key, text_key=False)
    events = _unavailable(event_log)
    assert events and all(e["success"] is False and e["metadata"]["fallback"] == "none" for e in events)
    assert len(events) == 1


def test_a_missing_key_with_a_text_model_is_one_notice(owed_task, event_log, monkeypatch):
    no_key = SimpleNamespace(name="anthropic_api_key", available=False, unavailable_reason=NO_KEY)
    _submit_then_write(owed_task, monkeypatch, no_key, text_key=True)
    [event] = _unavailable(event_log)
    assert event["success"] is True and event["metadata"]["fallback"] == "text_note"


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


# ── a step that asks the model in batches says how far it has got ─────────
#
# Review, 2026-11-26: the card measured the wait on the model from the start
# of the whole step and called it slow at 4 minutes, but the word-list
# correction of a lecture is many batches, four at a time; every answer came
# on time and the card still said the model was slow. Each answer is recorded,
# so the card measures from the last one and says 「已校正 3/14 段」.


def _batch_records(monkeypatch):
    from backend.core import model_usage
    seen: list[tuple[str, int, int, bool]] = []
    real = model_usage.record_batches

    def spy(stage, done, total, *, answered=True):
        seen.append((stage, done, total, answered))
        real(stage, done, total, answered=answered)

    monkeypatch.setattr(model_usage, "record_batches", spy)
    return seen


def test_a_batched_correction_records_each_answer(task_id, monkeypatch):
    from backend.core import glossary_correction, model_usage
    job_store.upsert_job(task_id=task_id, status="running", stage="glossary_correction", progress=60)
    seen = _batch_records(monkeypatch)
    sentence = "今天我们讲一下 Cloud Cool 的用法，" * 20
    segments = [{"start": i, "end": i + 1, "text": f"{i}{sentence}"} for i in range(20)]
    asked: list[str] = []

    def chat(_system, user):
        asked.append(user)
        return '{"fixes": []}'

    with model_usage.scope(task_id, "correct"):
        _fixes, calls, _unreadable = glossary_correction.decide(segments, "Claude Code", chat)
    batches = len(asked)
    assert batches >= 3 and calls == batches
    assert seen[0] == ("glossary_correction", 0, batches, False), "the count is there before any answer"
    assert [done for _s, done, _t, _a in seen[1:]] == list(range(1, batches + 1)), "one per answer, in order"
    stored = job_store.get_job(task_id)["metadata"][model_usage.MODEL_BATCHES]
    assert stored["stage"] == "glossary_correction"
    assert (stored["done"], stored["total"]) == (batches, batches)
    assert stored["last_answer_at"] and stored["updated_at"] >= job_store.get_job(task_id)["metadata"][job_store.STAGE_STARTED_AT]


def test_a_failed_batch_is_not_counted_as_answered(task_id, monkeypatch):
    from backend.core import model_usage
    job_store.upsert_job(task_id=task_id, status="running", stage="glossary_correction", progress=60)
    seen = _batch_records(monkeypatch)

    def ask(item):
        if item == 2:
            raise RuntimeError("quota")
        return item

    with model_usage.scope(task_id, "correct"), pytest.raises(RuntimeError):
        model_usage.stoppable_map(ask, [1, 2, 3], workers=1, check_stop=lambda: None, stage="glossary_correction")
    assert [done for _s, done, _t, answered in seen if answered] == [1, 3]


def test_the_translation_records_each_chunk(task_id, monkeypatch):
    from backend.core import ai_summarizer, model_usage
    job_store.upsert_job(task_id=task_id, status="running", stage="translation", progress=61)
    seen = _batch_records(monkeypatch)
    segments = [{"start": i, "end": i + 1, "text": "word " * 40} for i in range(30)]

    def chat(_system, _user):
        return "[]"

    with model_usage.scope(task_id, "correct"):
        result = ai_summarizer.generate_bilingual_segments_zh(segments, chat=chat, max_chunk_chars=1500)
    assert result.chunk_count >= 3
    assert seen[0] == ("translation", 0, result.chunk_count, False)
    assert [done for _s, done, _t, _a in seen[1:]] == list(range(1, result.chunk_count + 1))
    stored = job_store.get_job(task_id)["metadata"][model_usage.MODEL_BATCHES]
    assert (stored["stage"], stored["done"]) == ("translation", result.chunk_count)


def test_batch_progress_never_fails_the_step(monkeypatch):
    from backend.core import model_usage

    def broken(*_a, **_k):
        raise OSError("database is locked")

    monkeypatch.setattr(job_store, "update_job_metadata", broken)
    with model_usage.scope("any-task", "correct"):
        assert model_usage.stoppable_map(lambda x: x * 2, [1, 2], workers=2, check_stop=lambda: None,
                                         stage="translation") == [2, 4]


def test_putting_a_task_back_in_the_queue_keeps_a_metadata_write_made_meanwhile(task_id, monkeypatch):
    # Review, 2026-11-26: reopen_job read the metadata and wrote it back
    # without the write lock, so a write landing in between was lost.
    import threading
    job_store.upsert_job(task_id=task_id, status="cancelled", stage="glossary_correction", progress=60,
                         metadata={"other": 1})
    real = job_store._merged_metadata_for_upsert
    writer: list[threading.Thread] = []

    def read_then_someone_writes(*args, **kwargs):
        merged = real(*args, **kwargs)
        thread = threading.Thread(target=job_store.update_job_metadata, args=(task_id, {"meanwhile": True}))
        thread.start()
        writer.append(thread)
        thread.join(timeout=0.5)  # with the lock held it waits for reopen to commit
        return merged

    monkeypatch.setattr(job_store, "_merged_metadata_for_upsert", read_then_someone_writes)
    assert job_store.reopen_job(task_id)
    writer[0].join(timeout=15)
    metadata = job_store.get_job(task_id)["metadata"]
    assert metadata.get("meanwhile") is True, "the write made meanwhile is kept"
    assert metadata["other"] == 1
    assert job_store.get_job(task_id)["stage"] == "queued"
