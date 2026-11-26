"""停下这一步 stops a note being written in parts at once.

Requirements (to-do 「点『停下这一步』后，写笔记还在接着请求模型」, 2026-11-26),
in the user's terms:

- Stopping the step while the first part is being written does not wait for
  the request in flight: the step ends within a second, not when the model
  answers.
- No later part is written or checked after the stop: nothing more is paid
  for.
- The step reads stopped (已停下), not cut off by a stopping service, and the
  task keeps the note it had.
- What was already answered is kept: carrying the step on reads it back and
  asks the model only for the parts it had not answered.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

import backend.core.claude_vision as cv
import backend.core.visual_note_job as vn
from backend.core import job_store as store, model_answers, model_usage, task_actions, task_activity, task_steps, task_stop
from backend.core.claude_vision import VisualNoteDraft
from test_task_resume import Edges, _ended, _in_place_task, _statuses, _until, _wire
from test_visual_note_job import TASK, _fake_extractor, job_store, no_ambient_key  # noqa: F401

NOTE = "# 最小二乘法\n\n## 一、定义\n\n" + "最小二乘法是一种拟合方法。\n" * 20


class SlowFirstPart:
    """A model whose first request does not come back until released; each
    request goes out the way a real exit sends it (``model_answers``)."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.release = threading.Event()
        self.first_out = threading.Event()

    def ask(self, what: str) -> str:
        def send() -> str:
            self.sent.append(what)
            if len(self.sent) == 1:
                self.first_out.set()
                self.release.wait(30)
            return NOTE

        return model_answers.remembered(model_answers.request_key("stop-test", what), send)


def test_stopping_mid_first_part_writes_and_checks_no_later_part(job_store, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(cv, "MAX_TRANSCRIPT_CHARS", 20)
    model = SlowFirstPart()
    checked: list[str] = []

    def writer(transcript, frames, *, part=None, **_kwargs):
        markdown = model.ask(f"part {part.index}/{part.total}")
        return VisualNoteDraft(markdown=markdown, basis_note="", model="claude-x", frames_sent=list(frames),
                               transcript_chars=len(transcript))

    def checker(system, user):
        checked.append(user)
        return '{"points": []}'

    outcome: dict[str, BaseException] = {}

    def run() -> None:
        with task_stop.bound(TASK):
            try:
                vn.run_visual_note(TASK, client_id="anonymous", api_key="sk-ant-test-not-a-real-key",
                                   keyframe_extractor=_fake_extractor(tmp_path), writer=writer,
                                   checker=(checker, "text_model"), reviser=lambda s, u: NOTE)
            except BaseException as exc:  # noqa: BLE001 - asserted below
                outcome["error"] = exc

    task_stop.begin(TASK)
    try:
        note = threading.Thread(target=model_usage.carry(run), daemon=True)
        note.start()
        assert model.first_out.wait(5)
        stopped_at = time.monotonic()
        task_stop.request(TASK)
        note.join(5)
        assert not note.is_alive()
        assert time.monotonic() - stopped_at < 1.5, "the request in flight is not waited for"
    finally:
        model.release.set()
        task_stop.begin(TASK)

    assert isinstance(outcome.get("error"), task_stop.StepStopped)
    assert not isinstance(outcome["error"], model_usage.ServiceStopping)
    assert model.sent == ["part 1/" + model.sent[0].split("/")[1]], "no later part is asked for"
    assert int(model.sent[0].split("/")[1]) > 1, "the note was written in several parts"
    assert checked == [], "nothing is checked after the stop"
    state = vn.visual_note_state(job_store["result"])
    assert state["status"] == vn.STATUS_FAILED and state["error"] == task_steps.STOPPED_NOTE
    assert job_store["result"]["summary_markdown"] == "原来的文字笔记", "the task keeps the note it had"
    assert not vn.is_claimed(TASK)


def test_a_stopped_note_ends_at_once_and_carrying_on_asks_only_what_was_not_answered(monkeypatch, tmp_path):
    task_id = f"stop-note-{int(time.time() * 1000) % 1_000_000}"
    _in_place_task(task_id, tmp_path, status="queued", result={})
    edges = Edges(task_id)
    _wire(monkeypatch, edges)
    model = SlowFirstPart()
    parts = ["第一段", "第二段", "第三段"]

    def write_note(task_id, client_id, *, on_local_work_done=None, planned_ranges=None):
        """The note's parts, each a request, in the order the frame note
        writes them (``visual_note_job._run_visual_note``)."""
        if on_local_work_done:
            on_local_work_done()
        vn.claim(task_id)
        try:
            written = []
            for part in parts:
                model_usage.stop_if_asked()
                written.append(model.ask(part))
            from backend.core import job_store as jobs

            job = jobs.get_job(task_id) or {}
            result = {**(job.get("result") or {}), "summary_markdown": "\n".join(written), "summary_status": "completed"}
            jobs.update_job_result(task_id, result)
        finally:
            vn.release(task_id)

    monkeypatch.setattr(task_actions.task_flow.local_intake_flow, "write_note", write_note)

    async def scenario():
        await task_actions._carry_on(store.get_job(task_id), task_steps.FETCH, route="/test")
        assert await _until(lambda: model.first_out.is_set())
        stopped_at = time.monotonic()
        answer = await task_actions.stop_step(task_id, None)
        assert answer["stopped_step"]["step"] == task_steps.NOTE
        assert await _until(lambda: _ended(task_id) and not task_actions.JOB_EVENTS.is_running(task_id)
                            and not vn.is_claimed(task_id), tries=100)
        assert time.monotonic() - stopped_at < 1.5, "the note's request in flight is not waited for"

        activity = task_activity.activity(store.get_job(task_id))
        assert not activity.busy and activity.label == "已停下"
        assert activity.resume_from == task_steps.NOTE
        assert _statuses(task_id)[task_steps.NOTE] == task_steps.STOPPED
        assert store.get_job(task_id)["result"].get("summary_error") == task_steps.STOPPED_NOTE
        assert model.sent == ["第一段"], "no later part is asked for after the stop"

        # The first part's answer comes back after the stop: kept for the step.
        model.release.set()
        assert await _until(lambda: store.count_model_answers(task_id) == 1)

        answer = await task_actions.resume_task(task_id, None)
        assert answer["resume_from"]["step"] == task_steps.NOTE
        assert await _until(lambda: _ended(task_id) and not task_actions.JOB_EVENTS.is_running(task_id))

    try:
        asyncio.run(scenario())
    finally:
        model.release.set()
        store.delete_model_answers(task_id)
    assert model.sent == ["第一段", "第二段", "第三段"], "carrying on pays only for the parts not answered"
    assert _statuses(task_id)[task_steps.NOTE] == task_steps.DONE


@pytest.fixture(autouse=True)
def _no_stop_left(monkeypatch):
    yield
    task_stop.begin(TASK)


# ── the review of the stop change (2026-11-26) ──────────────────────────────
#
# Requirements, in the user's terms:
#
# - Stopping the step while the cut is being judged ends the cut: the task
#   does not stay 正在剪辑 (busy, refusing 接着做, 停下 and deletion until the
#   service restarts), no later request is sent, and it ends at once.
# - A note written by a text model (a transcript file's note, the text model
#   standing in for Claude) stops the same way, between its chapters and in
#   its coverage check: nothing is asked after the stop, and it ends at once.
# - A stop that comes after the last answer came back does not save that
#   note: the task keeps the note it had.
# - A stop made just as a request is about to go out sends nothing.
# - A transcript file stopped while its first note is written reads 已停下
#   (not 已取消), like its note step, and carries on from its transcript.
# - A user's stop is no failure in the diagnostic log.

import backend.core.ai_client as ai_client  # noqa: E402
import backend.core.ai_summarizer as summ  # noqa: E402
from backend.core import debreath_job, fake_model, local_intake_flow, survey_cut, task_flow  # noqa: E402
from backend.core.ai_prompts import _CHAPTER_NOTE_SYSTEM  # noqa: E402


class HeldTextModel:
    """A text model answering as the fake model does, each request going out
    the way a real one does (``ai_client._chat`` → ``model_answers``), whose
    first request matching ``hold`` does not come back until released. Each
    request sent is counted with the moment it was sent."""

    def __init__(self, hold) -> None:
        self.hold = hold
        self.sent: list[tuple[float, str]] = []
        self.release = threading.Event()
        self.held = threading.Event()

    def create(self, client, model, *, may_replace=True, on_text=None, **kwargs):
        messages = kwargs.get("messages") or []
        system = str(messages[0].get("content") if messages else "")
        part = model_usage.current().item or ""
        self.sent.append((time.monotonic(), part))
        if not self.held.is_set() and self.hold(system, part):
            self.held.set()
            self.release.wait(30)
        return fake_model.openai_response(fake_model.reply_to_messages(messages)), model

    def sent_after(self, moment: float) -> list[str]:
        return [part for at, part in self.sent if at > moment]


HOLDS = {
    "a chapter": lambda system, part: system.startswith(_CHAPTER_NOTE_SYSTEM[:40]),
    "the coverage check": lambda system, part: part == "查漏",
}


@pytest.fixture()
def text_model(monkeypatch):
    """Install a held text model; the caller names where it holds."""
    monkeypatch.setattr(summ, "_get_client", lambda **_k: SimpleNamespace(base_url="https://api.deepseek.com"))
    monkeypatch.setattr(summ, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("FLUENTFLOW_NOTE_DEADLINE_SECONDS", raising=False)
    monkeypatch.delenv("FLUENTFLOW_NOTE_MODE", raising=False)
    made: list[HeldTextModel] = []

    def install(hold) -> HeldTextModel:
        model = HeldTextModel(hold)
        monkeypatch.setattr(ai_client, "_create", model.create)
        made.append(model)
        return model

    yield install
    for model in made:
        model.release.set()


CHAPTERED = {"provider": "deepseek", "api_key": "sk-test-not-a-real-key", "note_mode": "chapter_coverage"}


def _events(monkeypatch, module) -> list[dict]:
    logged: list[dict] = []
    monkeypatch.setattr(module, "log_event", lambda **kw: logged.append(kw))
    return logged


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg not installed")
def test_stopping_while_the_cut_is_judged_ends_the_cut(tmp_path, monkeypatch):
    from test_survey_cut import _recording, _segments

    (tmp_path / "周会").mkdir()
    source = _recording(tmp_path / "周会" / "周会.mp4")
    task_id = f"stop-cut-{int(time.time() * 1000) % 1_000_000}"
    segments = _segments()
    store.upsert_job(task_id=task_id, status="completed",
                     metadata={"folder_intake": {"original_path": str(source)}},
                     result={"raw_segments": segments, "display_segments": segments,
                             "transcript_text": "\n".join(s["text"] for s in segments),
                             "transcript_media": "source"})
    monkeypatch.setattr(survey_cut, "measure_pictures", lambda source, gaps: {})
    logged = _events(monkeypatch, debreath_job)
    model = SlowFirstPart()

    def chat(system, user, *a, **k):
        model.ask(user)
        return '{"keep_gaps": [], "remove": []}'

    outcome: dict[str, BaseException] = {}

    def run() -> None:
        with task_stop.bound(task_id):
            try:
                survey_cut.survey_and_cut(task_id, chat=(chat, "fake"))
            except BaseException as exc:  # noqa: BLE001 - asserted below
                outcome["error"] = exc

    task_stop.begin(task_id)
    try:
        cut = threading.Thread(target=model_usage.carry(run), daemon=True)
        cut.start()
        assert model.first_out.wait(10)
        stopped_at = time.monotonic()
        task_stop.request(task_id)
        cut.join(5)
        assert not cut.is_alive()
        assert time.monotonic() - stopped_at < 1.5, "the judge's request in flight is not waited for"
    finally:
        model.release.set()
        task_stop.begin(task_id)

    assert isinstance(outcome.get("error"), task_stop.StepStopped)
    assert len(model.sent) == 1, "nothing more is asked after the stop"
    job = store.get_job(task_id)
    assert not debreath_job.is_running(job["result"]), "the cut is left running after the stop"
    state = debreath_job.debreath_state(job["result"])
    assert state["status"] == debreath_job.STATUS_FAILED and state["error"] == task_steps.STOPPED_NOTE
    assert not task_activity.activity(job).busy
    [event] = [e for e in logged if e["event_name"].startswith("debreath_")]
    assert event["event_name"] == "debreath_stopped" and event["success"] is None, "a stop is no failure"


@pytest.mark.parametrize("hold", sorted(HOLDS))
def test_a_text_models_note_stops_between_chapters_and_in_its_check(text_model, monkeypatch, hold):
    """The text model standing in for Claude (``local_intake_flow``), bound to
    the task by the note's thread (``task_flow._write_note_holding_slot``)."""
    import backend.core.local_entry_guards as guards

    task_id = f"stop-text-{int(time.time() * 1000) % 1_000_000}"
    store.upsert_job(task_id=task_id, status="completed", stage="done",
                     metadata={"queue_options": {"ai_provider": "deepseek"}},
                     result={"task_id": task_id, "transcript_text": "今天讲最小二乘法。" * 50,
                             "summary_markdown": "", "summary_status": "pending",
                             "note_deferred_to_visual_note": True})
    channel = SimpleNamespace(name="anthropic_api_key", available=False, unavailable_reason="没有 Anthropic Key。")
    monkeypatch.setattr(local_intake_flow.visual_note_channel, "resolve_channel", lambda *_a, **_k: channel)
    monkeypatch.setattr(guards, "local_ai_kwargs", lambda **_kw: dict(CHAPTERED))
    model = text_model(HOLDS[hold])

    task_stop.begin(task_id)
    try:
        note = threading.Thread(target=model_usage.carry(lambda: task_flow._write_note_holding_slot(
            task_id, None, None, threading.Event())), daemon=True)
        note.start()
        assert model.held.wait(10), "the note reached " + hold
        stopped_at = time.monotonic()
        task_stop.request(task_id)
        note.join(5)
        assert not note.is_alive()
        assert time.monotonic() - stopped_at < 1.5, "the request in flight is not waited for"
        model.release.set()
        time.sleep(0.5)
        assert model.sent_after(stopped_at) == [], "nothing is asked after the stop"
    finally:
        model.release.set()
        task_stop.begin(task_id)
        store.delete_model_answers(task_id)
    result = store.get_job(task_id)["result"]
    assert result["summary_error"] == task_steps.STOPPED_NOTE
    assert result["summary_markdown"] == ""


def test_a_stop_after_the_text_models_last_answer_does_not_save_its_note(monkeypatch):
    import backend.core.local_entry_guards as guards

    task_id = f"stop-late-{int(time.time() * 1000) % 1_000_000}"
    store.upsert_job(task_id=task_id, status="completed", stage="done",
                     metadata={"queue_options": {"ai_provider": "deepseek"}},
                     result={"task_id": task_id, "transcript_text": "今天讲最小二乘法。",
                             "summary_markdown": "原来的笔记", "summary_status": "completed"})
    channel = SimpleNamespace(name="anthropic_api_key", available=False, unavailable_reason="没有 Anthropic Key。")
    monkeypatch.setattr(local_intake_flow.visual_note_channel, "resolve_channel", lambda *_a, **_k: channel)
    monkeypatch.setattr(guards, "local_ai_kwargs", lambda **_kw: dict(CHAPTERED))

    def answered_then_stopped(transcript, **_kw):
        task_stop.request(task_id)  # the user presses 停下这一步 as the answer arrives
        return SimpleNamespace(markdown="# 新写的笔记")

    monkeypatch.setattr(summ, "summarize_transcript_with_metadata", answered_then_stopped)
    task_stop.begin(task_id)
    try:
        task_flow._write_note_holding_slot(task_id, None, None, threading.Event())
    finally:
        task_stop.begin(task_id)
    result = store.get_job(task_id)["result"]
    assert result["summary_markdown"] == "原来的笔记", "the note written after the stop replaced the task's"
    assert result["summary_error"] == task_steps.STOPPED_NOTE


def test_a_stop_after_the_last_part_does_not_save_the_frame_note(job_store, tmp_path, monkeypatch):  # noqa: F811
    logged = _events(monkeypatch, vn)

    def writer(transcript, frames, *, part=None, **_kwargs):
        task_stop.request(TASK)  # the user presses 停下这一步 as the last answer arrives
        return VisualNoteDraft(markdown=NOTE, basis_note="", model="claude-x", frames_sent=list(frames),
                               transcript_chars=len(transcript))

    task_stop.begin(TASK)
    try:
        with task_stop.bound(TASK), pytest.raises(task_stop.StepStopped):
            vn.run_visual_note(TASK, client_id="anonymous", api_key="sk-ant-test-not-a-real-key",
                               keyframe_extractor=_fake_extractor(tmp_path), writer=writer)
    finally:
        task_stop.begin(TASK)
    assert job_store["result"]["summary_markdown"] == "原来的文字笔记", "the task keeps the note it had"
    state = vn.visual_note_state(job_store["result"])
    assert state["status"] == vn.STATUS_FAILED and state["error"] == task_steps.STOPPED_NOTE
    [event] = [e for e in logged if e["event_name"].startswith("visual_note_")]
    assert event["event_name"] == "visual_note_stopped" and event["success"] is None, "a stop is no failure"


def test_a_stop_just_before_the_request_goes_out_sends_nothing(monkeypatch):
    task_id = f"stop-race-{int(time.time() * 1000) % 1_000_000}"
    checked = model_usage.stop_if_asked

    def checked_then_stopped() -> None:
        checked()
        task_stop.request(task_id)  # pressed after the check, before the request's thread runs

    monkeypatch.setattr(model_usage, "stop_if_asked", checked_then_stopped)
    sent: list[str] = []
    task_stop.begin(task_id)
    try:
        with task_stop.bound(task_id), pytest.raises(task_stop.StepStopped):
            model_usage.unless_stopping(lambda: sent.append("request") or "answer")
        time.sleep(0.3)
    finally:
        task_stop.begin(task_id)
    assert sent == [], "the request went out after the stop"


# A transcript file stopped while its first note is written. The subtitle
# helpers come from the subtitle tests, under their own names.

from test_subtitle_resume import (  # noqa: E402
    _captions_task,
    _cut_off_reading,
    _ended as _subtitle_ended,
    _in_place_task as _subtitle_in_place_task,
    _statuses as _subtitle_statuses,
    _until as _subtitle_until,
)


@pytest.mark.parametrize("hold", sorted(HOLDS))
@pytest.mark.parametrize("kind", ["subtitle file", "link captions"])
def test_a_transcript_file_stopped_at_its_first_note_reads_stopped_and_carries_on(
        text_model, monkeypatch, tmp_path, kind, hold):
    import backend.core.transcript_note as transcript_note
    from backend.core import output_folder

    task_id = f"stop-first-note-{kind[:4]}-{int(time.time() * 1000) % 1_000_000}"
    if kind == "subtitle file":
        _subtitle_in_place_task(task_id, tmp_path, status="cancelled")
    else:
        _captions_task(task_id, status="cancelled")
    _cut_off_reading(task_id)
    task_steps.close_open(task_id, stopped=True)
    monkeypatch.setattr(transcript_note, "local_ai_kwargs", lambda **_kw: dict(CHAPTERED))
    model = text_model(HOLDS[hold])

    async def scenario():
        await task_actions.resume_task(task_id, None)
        assert await _subtitle_until(model.held.is_set), "the first note reached " + hold
        stopped_at = time.monotonic()
        await task_actions.stop_step(task_id, None)
        assert await _subtitle_until(lambda: _subtitle_ended(task_id)
                                     and not task_actions.JOB_EVENTS.is_running(task_id), tries=100)
        assert time.monotonic() - stopped_at < 1.5, "the note's request in flight is not waited for"
        model.release.set()
        await asyncio.sleep(0.5)
        assert model.sent_after(stopped_at) == [], "nothing is asked after the stop"

        job = store.get_job(task_id)
        assert job["status"] == "completed", "a transcript file stopped at its note reads 已取消"
        activity = task_activity.activity(job)
        assert not activity.busy and activity.label == "已停下"
        assert task_steps.STOPPED in _subtitle_statuses(task_id).values()
        assert job["result"]["summary_error"] == task_steps.STOPPED_NOTE
        assert "最小二乘法" in job["result"]["transcript_text"]

        await task_actions.resume_task(task_id, None)
        assert await _subtitle_until(lambda: _subtitle_ended(task_id)
                                     and not task_actions.JOB_EVENTS.is_running(task_id))

    try:
        asyncio.run(scenario())
    finally:
        model.release.set()
        store.delete_model_answers(task_id)
    output_folder._WORKER.submit(lambda: None).result(timeout=10)
    job = store.get_job(task_id)
    assert job["status"] == "completed" and job["result"]["summary_markdown"].strip()
    assert _subtitle_statuses(task_id)[task_steps.NOTE] == task_steps.DONE


def test_the_pipelines_own_text_note_is_the_tasks_work(monkeypatch, tmp_path):
    """With the automatic cut and Claude's note both off, the pipeline writes
    the text note itself (``media_job``); its chapters and check stop with
    the step only when that work is bound to the task (``task_stop.bound``)."""
    from backend.core import media_job, task_submission

    async def unchanged(_ctx, *, segments, **_kw):
        return segments, {}

    monkeypatch.setattr(media_job, "_run_subtitle_correction_stage", unchanged)
    monkeypatch.setattr(media_job, "_run_glossary_correction_stage", unchanged)
    bound_to: list[str | None] = []

    def summarize(transcript, **_kw):
        bound_to.append(task_stop.current())
        return SimpleNamespace(markdown="# 笔记\n\n正文。", requested_mode="direct", resolved_mode="direct",
                               chunk_count=1)

    monkeypatch.setattr(media_job, "summarize_transcript_with_metadata", summarize)
    task_id = f"stop-pipeline-note-{int(time.time() * 1000) % 1_000_000}"
    segments = [{"start": 0.0, "end": 4.0, "text": "今天讲最小二乘法。"}]

    async def run():
        ctx = task_submission._local_media_job_context(
            task_id=task_id, client_id="anonymous", source_type="video", source_filename="course.mp4",
            raw_title="course", display_title="course", suffix=".mp4", td=str(tmp_path),
            in_path=tmp_path / "course.mp4", content=b"", source_file_size_mb=1,
            duration_preflight_sec=5, options={"intelligence": "fluentflow"},
            announce_fallback=False,
        )
        ctx.summary_disabled = False
        ctx.note_deferred_to_visual_note = False
        ctx.ai_kwargs_builder = lambda **_kw: {"provider": "deepseek", "api_key": "sk-test-not-a-real-key"}
        ctx.finalize_result_storage = lambda _task, result, _meta: result
        ctx.transcribe_checkpoint = {
            "base_result": {}, "tr": {"text": segments[0]["text"], "language": "zh"},
            "segments_payload": segments, "transcript_text": segments[0]["text"],
            "in_path": str(tmp_path / "course.mp4"), "duration_sec": 5,
        }
        await media_job.execute_media_job(ctx)

    try:
        asyncio.run(run())
        assert store.get_job(task_id)["result"]["summary_markdown"].startswith("# 笔记")
    finally:
        store.delete_jobs([task_id])
    assert bound_to == [task_id], "the pipeline's note is not stopped by 停下这一步"
