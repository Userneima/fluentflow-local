"""One pipeline at a time, whichever door a job came in through.

The queue is serial because this machine was measured collapsing when two
transcriptions ran together. That only holds if every entry joins the same
chain: a single upload, a video link, a file processed in place, a retry. The
first two used to start their worker directly, beside whatever was running.

The de-breath and the note-from-cut-media on a finished task cannot join the
chain (they are background work on a task that is already over), so they are
refused while the chain is busy instead of running beside it.
"""

from __future__ import annotations

from backend.core import task_reruns  # noqa: E402

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from backend.core.job_event_hub import JobEventHub
from backend.core import job_store
from backend.core.job_store import upsert_job
from backend.core.video_source import SavedVideoSource
from backend.routers import local_job_debreath, local_job_visual_note
from backend.routers import local_processing as lp
from backend.core import task_flow as tf, task_queue as tq, task_submission as ts  # noqa: F401
from backend.core import link_submission as lvs


class _Passed:
    duration_seconds = 12.0

    def as_metadata(self):
        return {"duration_seconds": 12.0}


@pytest.fixture(autouse=True)
def _fresh_chain(monkeypatch):
    """A hub without the keep-awake guard, and an empty chain, per test."""
    hub = JobEventHub()
    monkeypatch.setattr(lp, "JOB_EVENTS", hub)
    monkeypatch.setattr(tq, "JOB_EVENTS", lp.JOB_EVENTS)
    monkeypatch.setattr(tf, "JOB_EVENTS", lp.JOB_EVENTS)
    monkeypatch.setattr(ts, "JOB_EVENTS", lp.JOB_EVENTS)
    monkeypatch.setattr(lvs, "JOB_EVENTS", hub)
    tq._QUEUE_RECENT.clear()
    tq._QUEUE_LINE.clear()
    yield hub
    tq._QUEUE_RECENT.clear()
    tq._QUEUE_LINE.clear()


def _saved_video(path: Path) -> SavedVideoSource:
    return SavedVideoSource(
        ok=True, provider="test", source_url="https://example.com/v/1",
        download_url="https://example.com/v/1.mp4", video_id="v1", raw_title="讲座",
        display_title="讲座", title="讲座", filename=path.name, file_path=str(path),
        metadata_path="", size_bytes=path.stat().st_size, downloaded_at="now",
    )


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0.01)


async def _until(done, seconds: float = 30.0) -> None:
    """Wait for what should happen next, up to ``seconds``. After a pipeline the
    cut and the note step run before the queue moves on, and a slow CI runner
    took longer than a fixed fifth of a second for them (2026-10-16)."""
    for _ in range(int(seconds / 0.01)):
        if done():
            return
        await asyncio.sleep(0.01)


def test_a_single_upload_a_video_link_and_an_in_place_file_run_one_after_another(
    monkeypatch, tmp_path
):
    """Submitted one right after another, each pipeline starts only after the
    previous one has finished — including the video link, whose download is
    allowed to go ahead but whose transcription is not."""
    log: list[str] = []
    gates: dict[str, asyncio.Event] = {}

    async def fake_pipeline(ctx):
        task_id = ctx.task_id_value
        log.append(f"start:{task_id}")
        await gates[task_id].wait()
        upsert_job(task_id=task_id, status="completed", client_id=ctx.client_id, stage="done", progress=100)
        log.append(f"finish:{task_id}")

    recording = tmp_path / "in-place.mp4"
    recording.write_bytes(b"not really video")
    downloaded = tmp_path / "downloaded.mp4"
    downloaded.write_bytes(b"not really video either")

    monkeypatch.setattr(lp, "preflight_media_file", lambda _p: _Passed())
    monkeypatch.setattr(ts, "preflight_media_file", lp.preflight_media_file)
    monkeypatch.setattr(lvs, "preflight_media_file", lambda _p: _Passed())
    monkeypatch.setattr(tf, "_run_pipeline", fake_pipeline)
    monkeypatch.setattr(lvs, "download_video_source", lambda *a, **k: _saved_video(downloaded))
    monkeypatch.setattr(lvs, "get_preference", lambda name: None)

    async def scenario():
        upload = await lp.process_media(
            request=SimpleNamespace(client=None, headers={}),
            file=UploadFile(file=io.BytesIO(b"upload bytes"), filename="upload.mp4"),
            title=None, raw_title=None, display_title=None, export_to_lark=None,
            lark_export_route=None, lark_via_cli=None, folder_token=None,
            deepseek_api_key=None, openai_api_key=None, qwen_api_key=None,
            ai_provider=None, ai_model=None, note_mode=None, skip_summary=None,
            generate_visuals=None, stt_model=None, stt_speed=None,
            speaker_diarization=None, system_prompt=None, prompt_preset=None,
            prompt_preset_label=None, duration_limit_seconds=None, task_id=None,
        )
        assert upload.media_type == "text/event-stream"
        first = tq._QUEUE_RECENT[-1]["task_id"]
        gates[first] = asyncio.Event()

        link = await lvs.submit_video_source_job(
            input_text="https://example.com/v/1", title="", raw_options={}, client_id="local-single-user"
        )
        second = link["task_id"]
        gates[second] = asyncio.Event()

        in_place = await ts.queue_local_media_file(
            recording, client_id="local-single-user", options={}, duration_limit_seconds=None,
            route="/queue/process-local-files", origin={"chosen_with": "test"},
        )
        third = in_place["task_id"]
        gates[third] = asyncio.Event()

        await _until(lambda: log == [f"start:{first}"])
        assert log == [f"start:{first}"], "nothing may start beside the first job"
        gates[first].set()
        await _until(lambda: len(log) >= 3)
        assert log == [f"start:{first}", f"finish:{first}", f"start:{second}"]
        gates[second].set()
        await _until(lambda: len(log) >= 5)
        assert log[-2:] == [f"finish:{second}", f"start:{third}"]
        gates[third].set()
        await _until(lambda: len(log) >= 6)
        assert log[-1] == f"finish:{third}"
        return [first, second, third]

    ids = asyncio.run(scenario())
    assert len(set(ids)) == 3


def test_a_video_link_downloads_while_the_queue_works_and_waits_only_to_transcribe(monkeypatch, tmp_path):
    """The download is network and competes with nothing the queue protects, so
    it is not held back; the transcription that follows it is."""
    log: list[str] = []
    gate = asyncio.Event()
    downloaded = tmp_path / "d.mp4"
    downloaded.write_bytes(b"x")

    async def fake_pipeline(ctx):
        log.append(f"start:{ctx.task_id_value}")
        await gate.wait()
        upsert_job(task_id=ctx.task_id_value, status="completed", client_id=ctx.client_id, stage="done", progress=100)
        log.append(f"finish:{ctx.task_id_value}")

    def fake_download(*a, **k):
        log.append("download")
        return _saved_video(downloaded)

    monkeypatch.setattr(lp, "preflight_media_file", lambda _p: _Passed())
    monkeypatch.setattr(ts, "preflight_media_file", lp.preflight_media_file)
    monkeypatch.setattr(lvs, "preflight_media_file", lambda _p: _Passed())
    monkeypatch.setattr(tf, "_run_pipeline", fake_pipeline)
    monkeypatch.setattr(lvs, "download_video_source", fake_download)
    monkeypatch.setattr(lvs, "get_preference", lambda name: None)
    recording = tmp_path / "r.mp4"
    recording.write_bytes(b"x")

    async def scenario():
        first = (await ts.queue_local_media_file(
            recording, client_id="local-single-user", options={}, duration_limit_seconds=None,
            route="/queue/process-local-files", origin={},
        ))["task_id"]
        second = (await lvs.submit_video_source_job(
            input_text="https://example.com/v/2", title="", raw_options={}, client_id="local-single-user"
        ))["task_id"]
        # The download does not wait for the queue, so it may come before or
        # after the transcription it will follow starts; what must hold is
        # that it happens while that transcription is still running.
        await _until(lambda: "download" in log)
        assert f"finish:{first}" not in log, "the download waited for the transcription"
        assert await _until_true(lambda: f"start:{first}" in log), "the first recording never started"
        gate.set()
        await _until(lambda: len(log) >= 5)
        assert log[-3:] == [f"finish:{first}", f"start:{second}", f"finish:{second}"]

    asyncio.run(scenario())


def test_the_queue_is_busy_while_a_runner_holds_its_link_and_idle_once_it_let_go(_fresh_chain):
    hub = _fresh_chain

    async def scenario():
        gate = asyncio.Event()

        async def worker(previous, done):
            try:
                await gate.wait()
            finally:
                done.set()

        assert tq.queue_is_busy() is False
        await tq._start_behind_queue(task_id="a", client_id=None, route="/t", stage="x", chained_worker=worker)
        await asyncio.sleep(0.01)
        busy_while_running = tq.queue_is_busy()
        gate.set()
        await asyncio.sleep(0.05)
        return busy_while_running, tq.queue_is_busy(), hub.is_running("a")

    assert asyncio.run(scenario()) == (True, False, False)


def test_a_link_whose_runner_is_gone_does_not_count_as_busy():
    """The stale-chain case the waiters heal: an unset event with no runner
    behind it must not block a de-breath forever either."""
    tq._queue_tail_record("vanished", asyncio.Event())

    assert tq.queue_is_busy() is False


# ---- the entries that cannot join the chain refuse while it is busy -------------

_COMPLETED = {"task_id": "t-done", "status": "completed", "result": {"task_id": "t-done"}}


def _debreath_client(monkeypatch, busy: bool) -> TestClient:
    monkeypatch.setattr(local_job_debreath, "get_job", lambda task_id, client_id=None: dict(_COMPLETED))
    monkeypatch.setattr(task_reruns, "get_job", local_job_debreath.get_job)
    monkeypatch.setattr(task_reruns, "queue_is_busy", lambda: busy)
    monkeypatch.setattr(task_reruns.debreath_job, "resolve_source", lambda task_id: Path("/tmp/x.mp4"))
    monkeypatch.setattr(task_reruns.debreath_job, "claim", lambda task_id: None)
    monkeypatch.setattr(task_reruns.debreath_job, "run_debreath", lambda *a, **k: None)
    monkeypatch.setattr(task_reruns.debreath_job, "release", lambda task_id: None)
    app = FastAPI()
    app.include_router(local_job_debreath.router)
    return TestClient(app)


def test_a_manual_debreath_is_refused_in_chinese_while_a_transcription_runs(monkeypatch):
    r = _debreath_client(monkeypatch, busy=True).post("/jobs/t-done/debreath", json={})

    assert r.status_code == 409
    assert "正在处理" in r.json()["detail"]


def test_a_manual_debreath_goes_ahead_when_the_queue_is_idle(monkeypatch):
    r = _debreath_client(monkeypatch, busy=False).post("/jobs/t-done/debreath", json={})

    assert r.status_code == 200 and r.json()["accepted"] is True


def _visual_note_client(monkeypatch, busy: bool) -> TestClient:
    described = {
        "eligible": True, "reason": None, "model": "m", "channel": "c", "channel_label": "l",
        "frame_budget": 0, "transcript_chars": 1, "source_filename": "x.mp4",
        "media": {}, "subtitle_timeline": {},
    }
    monkeypatch.setattr(task_reruns, "get_job", lambda task_id, client_id=None: dict(_COMPLETED))
    monkeypatch.setattr(task_reruns, "resolve_secret", lambda *a, **k: "key")
    monkeypatch.setattr(task_reruns, "queue_is_busy", lambda: busy)
    monkeypatch.setattr(task_reruns.visual_note_job, "describe", lambda *a, **k: dict(described))
    monkeypatch.setattr(task_reruns.visual_note_job, "claim", lambda task_id: None)
    monkeypatch.setattr(task_reruns.visual_note_job, "run_visual_note", lambda *a, **k: None)
    monkeypatch.setattr(task_reruns.visual_note_job, "release", lambda task_id: None)
    app = FastAPI()
    app.include_router(local_job_visual_note.router)
    return TestClient(app)


def test_a_note_run_is_refused_in_chinese_while_a_transcription_runs(monkeypatch):
    r = _visual_note_client(monkeypatch, busy=True).post("/jobs/t-done/visual-note", json={})

    assert r.status_code == 409
    assert "正在处理" in r.json()["detail"]


def test_a_note_preview_is_free_and_answers_even_while_the_queue_is_busy(monkeypatch):
    r = _visual_note_client(monkeypatch, busy=True).post("/jobs/t-done/visual-note", json={"preview": True})

    assert r.status_code == 200 and r.json()["preview"] is True


def test_a_note_run_goes_ahead_when_the_queue_is_idle(monkeypatch):
    r = _visual_note_client(monkeypatch, busy=False).post("/jobs/t-done/visual-note", json={})

    assert r.status_code == 200 and r.json()["accepted"] is True


# ---- the request path does not block the loop on media inspection ----------------

def _off_the_loop(seen: dict):
    def probe(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        return _Passed()

    return probe


def test_inspecting_a_file_before_queueing_it_happens_off_the_event_loop(monkeypatch, tmp_path):
    """ffprobe on a multi-gigabyte recording takes seconds; while it ran on the
    loop every other request, including the progress a page was polling,
    stood still."""
    seen: dict = {}
    recording = tmp_path / "r.mp4"
    recording.write_bytes(b"x")
    monkeypatch.setattr(lp, "preflight_media_file", _off_the_loop(seen))
    monkeypatch.setattr(ts, "preflight_media_file", lp.preflight_media_file)

    async def no_pipeline(previous, done, ctx):
        done.set()

    monkeypatch.setattr(tf, "_run_serially", no_pipeline)

    asyncio.run(ts.queue_local_media_file(
        recording, client_id="local-single-user", options={}, duration_limit_seconds=None,
        route="/queue/process-local-files", origin={},
    ))

    assert seen["on_loop"] is False


def test_the_cookie_check_reads_the_browser_store_off_the_event_loop(monkeypatch):
    seen: dict = {}

    def probe(browser):
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        return {"ok": True, "browser": browser}

    from backend.routers import local_video_sources as link_routes

    monkeypatch.setattr(link_routes, "check_browser_cookies", probe)
    app = FastAPI()
    app.include_router(link_routes.router)

    r = TestClient(app).post("/video-sources/cookie-check", json={"browser": "chrome"})

    assert r.status_code == 200
    assert seen["on_loop"] is False


def test_a_task_submitted_as_a_link_gets_its_note_written_too(tmp_path, monkeypatch):
    """Requirement: a link gets the same note an upload gets. The pipeline
    leaves the note to the step after it, and the link entry used to stop at
    the pipeline, so link tasks ended with no note at all."""
    noted: list[str] = []
    downloaded = tmp_path / "d.mp4"
    downloaded.write_bytes(b"x")

    async def fake_pipeline(ctx):
        upsert_job(task_id=ctx.task_id_value, status="completed", client_id=ctx.client_id, stage="done", progress=100)

    async def record_note(task_id, client_id, *, on_local_work_done=None):
        noted.append(task_id)

    monkeypatch.setattr(lvs, "preflight_media_file", lambda _p: _Passed())
    monkeypatch.setattr(tf, "_run_pipeline", fake_pipeline)
    monkeypatch.setattr(tf, "_write_note_after_transcript", record_note)
    monkeypatch.setattr(lvs, "download_video_source", lambda *a, **k: _saved_video(downloaded))
    monkeypatch.setattr(lvs, "get_preference", lambda name: None)

    async def scenario():
        task_id = (await lvs.submit_video_source_job(
            input_text="https://example.com/v/3", title="", raw_options={}, client_id="local-single-user"
        ))["task_id"]
        await _until(lambda: noted)
        return task_id

    task_id = asyncio.run(scenario())
    assert noted == [task_id]


def _gated_pipeline(log: list[str], gates: dict[str, asyncio.Event]):
    async def fake_pipeline(ctx):
        task_id = ctx.task_id_value
        log.append(f"start:{task_id}")
        gates.setdefault(task_id, asyncio.Event())
        await gates[task_id].wait()
        upsert_job(task_id=task_id, status="completed", client_id=ctx.client_id, stage="done", progress=100)
        log.append(f"finish:{task_id}")
    return fake_pipeline


def test_the_submit_answer_says_where_each_recording_really_is_in_line(monkeypatch, tmp_path):
    """Requirement: a recording submitted while others are already queued is
    told its real place in line, not "1 of 1"."""
    log: list[str] = []
    gates: dict[str, asyncio.Event] = {}
    monkeypatch.setattr(lp, "preflight_media_file", lambda _p: _Passed())
    monkeypatch.setattr(ts, "preflight_media_file", lp.preflight_media_file)
    monkeypatch.setattr(tf, "_run_pipeline", _gated_pipeline(log, gates))
    files = []
    for name in ("a", "b", "c"):
        path = tmp_path / f"{name}.mp4"
        path.write_bytes(b"x")
        files.append(path)

    async def scenario():
        answers = []
        for path in files:
            # Submitted one at a time, each as its own "batch of one".
            answers.append(await ts.queue_local_media_file(
                path, client_id="local-single-user", options={}, duration_limit_seconds=None,
                route="/queue/process-local-files", origin={"chosen_with": "test"},
            ))
        await _settle()
        for answer in answers:
            gates.setdefault(answer["task_id"], asyncio.Event()).set()
            await _settle()
        return answers

    answers = asyncio.run(scenario())
    assert [(a["queue_position"], a["queue_total"]) for a in answers] == [(1, 1), (2, 2), (3, 3)]


def test_resubmitting_a_running_task_id_does_not_let_the_next_job_jump_the_queue(monkeypatch):
    """Requirement: submitting a task id that is already running changes
    nothing, and the next recording still waits for the job that is working."""
    log: list[str] = []
    gates: dict[str, asyncio.Event] = {}
    monkeypatch.setattr(tf, "_run_pipeline", _gated_pipeline(log, gates))

    def ctx(task_id):
        return SimpleNamespace(task_id_value=task_id, client_id=None, event_route=None)

    async def start(task_id):
        return await tq._start_behind_queue(
            task_id=task_id, client_id=None, route="/test", stage="processing",
            chained_worker=lambda previous, done: tf._run_serially(previous, done, ctx(task_id)),
        )

    async def scenario():
        assert await start("running-a") is True
        await _until(lambda: log == ["start:running-a"])
        assert await start("running-a") is False, "the same id is not started twice"
        assert await start("waiting-b") is True
        await _settle()
        assert log == ["start:running-a"], "b started beside a"
        gates["running-a"].set()
        await _until(lambda: log[-1] == "start:waiting-b")
        gates["waiting-b"].set()
        await _until(lambda: len(log) >= 4)

    asyncio.run(scenario())


# ── a job that never waits its turn ─────────────────────────────────────────
#
# Requirement (independent review of 2026-11-25, decision 1.19): a task with nothing to transcribe (a subtitle file by path, a link's
# captions, a link whose download failed) never waits for its turn, and it must
# not free the line for the recording behind it while the recording ahead of
# it still transcribes. With one job at a time, two transcriptions side by side
# is what this machine was measured collapsing under.


def _recording(name: str, running: set, overlap: list, hold: dict):
    async def worker(previous, done):
        try:
            await tq.wait_for_queue_turn(previous, name, None)
            running.add(name)
            if len(running) > 1:
                overlap.append(set(running))
            gate = hold.get(name)
            if gate is not None:
                await gate.wait()
            else:
                await asyncio.sleep(0.05)
        finally:
            running.discard(name)
            done.set()
    return worker


def test_a_recording_queued_behind_a_subtitle_file_waits_for_the_recording_ahead(monkeypatch, tmp_path):
    """A transcribes; a subtitle file is submitted and done in a moment; B,
    submitted next, starts only once A is done. The heartbeat is short so the
    wait also outlasts B's check that the task ahead of it is still there:
    the subtitle task is gone by then, and B still waits for A."""
    from backend.core import transcript_note as tn

    monkeypatch.setattr(tq, "_QUEUE_HEARTBEAT_SECONDS", 0.02)
    monkeypatch.setattr(tq, "QUEUE_CONCURRENCY", 1)
    srt = tmp_path / "a.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n", encoding="utf-8")

    async def read_at_once(*_a, **_k):
        return None

    monkeypatch.setattr(tn, "_run_subtitle_in_place", read_at_once)
    running: set = set()
    overlap: list = []
    order: list[str] = []

    async def scenario():
        hold = {"serial-sub-a": asyncio.Event()}
        for task_id in ("serial-sub-a", "serial-sub-b"):
            upsert_job(task_id=task_id, status="queued", client_id=None, stage="queued", progress=0)
        a = _recording("serial-sub-a", running, overlap, hold)
        b = _recording("serial-sub-b", running, overlap, hold)

        async def b_logged(previous, done):
            await tq.wait_for_queue_turn(previous, "serial-sub-b", None)
            order.append("b")
            await b(None, done)

        await tq._start_behind_queue(task_id="serial-sub-a", client_id=None, route="t", stage="x", chained_worker=a)
        await _settle()
        await tn.submit_subtitle_in_place(srt, client_id=None, options={}, route="t", origin={})
        await tq._start_behind_queue(task_id="serial-sub-b", client_id=None, route="t", stage="x",
                                     chained_worker=b_logged)
        await _settle()
        await asyncio.sleep(0.2)  # ten heartbeats
        assert order == [], "B started while A still transcribed"
        hold["serial-sub-a"].set()
        await _until(lambda: order == ["b"])
        await _settle()

    asyncio.run(scenario())
    assert not overlap, f"recordings ran together: {overlap}"


def test_a_job_that_ends_without_waiting_passes_its_place_on_and_does_not_free_the_line(monkeypatch):
    """Any entry that ends before waiting (a failed download, a link's
    captions): the next recording still waits for the one ahead of it, and
    starts as soon as that one is done."""
    monkeypatch.setattr(tq, "_QUEUE_HEARTBEAT_SECONDS", 0.02)
    monkeypatch.setattr(tq, "QUEUE_CONCURRENCY", 1)
    running: set = set()
    overlap: list = []
    started: list[str] = []

    async def ends_at_once(_previous, done):
        done.set()

    async def scenario():
        hold = {"serial-end-a": asyncio.Event()}
        for task_id in ("serial-end-a", "serial-end-x", "serial-end-y", "serial-end-b"):
            upsert_job(task_id=task_id, status="queued", client_id=None, stage="queued", progress=0)
        a = _recording("serial-end-a", running, overlap, hold)
        b = _recording("serial-end-b", running, overlap, hold)

        async def b_logged(previous, done):
            await tq.wait_for_queue_turn(previous, "serial-end-b", None)
            started.append("b")
            await b(None, done)

        await tq._start_behind_queue(task_id="serial-end-a", client_id=None, route="t", stage="x", chained_worker=a)
        await _settle()
        # Two in a row: the second passes on the first's place, which passes on A's.
        for task_id in ("serial-end-x", "serial-end-y"):
            await tq._start_behind_queue(task_id=task_id, client_id=None, route="t", stage="x",
                                         chained_worker=ends_at_once)
        await tq._start_behind_queue(task_id="serial-end-b", client_id=None, route="t", stage="x",
                                     chained_worker=b_logged)
        await asyncio.sleep(0.2)
        assert started == [], "B started while A still transcribed"
        hold["serial-end-a"].set()
        assert await _until_true(lambda: started == ["b"]), "B never started once A was done"
        await _settle()

    asyncio.run(scenario())
    assert not overlap, f"recordings ran together: {overlap}"


def test_a_job_that_ends_without_waiting_does_not_hold_an_idle_line(monkeypatch):
    """Nothing ahead of it: the next recording starts at once."""
    started: list[str] = []

    async def ends_at_once(_previous, done):
        done.set()

    async def recording(previous, done):
        try:
            await tq.wait_for_queue_turn(previous, "serial-idle-b", None)
            started.append("b")
        finally:
            done.set()

    async def scenario():
        for task_id in ("serial-idle-x", "serial-idle-b"):
            upsert_job(task_id=task_id, status="queued", client_id=None, stage="queued", progress=0)
        await tq._start_behind_queue(task_id="serial-idle-x", client_id=None, route="t", stage="x",
                                     chained_worker=ends_at_once)
        await _settle()
        await tq._start_behind_queue(task_id="serial-idle-b", client_id=None, route="t", stage="x",
                                     chained_worker=recording)
        assert await _until_true(lambda: started == ["b"]), started

    asyncio.run(scenario())


async def _until_true(predicate, seconds: float = 30.0) -> bool:
    for _ in range(int(seconds / 0.01)):
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


# Requirement (review of 5d6a580, 2026-11-26): no job waits forever in any
# chain, and one transcription at a time still holds. A job whose runner ended
# without releasing its link (its setup raised before its own ``finally``, or
# it was stopped before it began) is healed past by the job behind it; when
# the user then carries that task on under the same task id, its new runner
# queues behind the job that healed past it. That job must still free the
# line when it ends: it used to pass its place on through the dead link, whose
# task id was running again, and the carried-on task waited on it for ever.


def _carried_on_scenario(how_a_dies: str, when_resumed: str):
    log: list[str] = []

    async def a_raises(_previous, _done):
        # e.g. ``run_pipeline_then_note``'s step bookkeeping raising before its
        # try/finally (a locked database): the runner ends, its link unset.
        raise RuntimeError("database is locked")

    async def scenario():
        for task_id in ("serial-heal-a", "serial-heal-b"):
            upsert_job(task_id=task_id, status="queued", client_id=None, stage="queued", progress=0)
        hold_b = asyncio.Event()

        async def b(previous, done):
            try:
                await tq.wait_for_queue_turn(previous, "serial-heal-b", None)
                log.append("b")
                await hold_b.wait()
            finally:
                done.set()

        async def a_again(previous, done):
            try:
                await tq.wait_for_queue_turn(previous, "serial-heal-a", None)
                log.append("a-again")
            finally:
                done.set()

        async def resume_a():
            upsert_job(task_id="serial-heal-a", status="queued", client_id=None, stage="queued", progress=0)
            assert await tq._start_behind_queue(task_id="serial-heal-a", client_id=None, route="t", stage="x",
                                                chained_worker=a_again)

        await tq._start_behind_queue(task_id="serial-heal-a", client_id=None, route="t", stage="x",
                                     chained_worker=a_raises)
        if how_a_dies == "stopped before it began":
            # Cancelled before its first step: its coroutine never runs, so no
            # ``finally`` of its own releases the link.
            await tq.JOB_EVENTS.cancel("serial-heal-a")
        await _settle()
        await tq._start_behind_queue(task_id="serial-heal-b", client_id=None, route="t", stage="x",
                                     chained_worker=b)
        if when_resumed == "at once":
            await resume_a()
        assert await _until_true(lambda: "b" in log), "B never got past the job that died"
        if when_resumed == "while B works":
            await resume_a()
        await _settle()
        assert log == ["b"], "the carried-on task started beside B"
        hold_b.set()
        assert await _until_true(lambda: log == ["b", "a-again"], 2.0), f"the carried-on task never started: {log}"

    asyncio.run(scenario())


@pytest.mark.parametrize("how_a_dies", ["raised before releasing", "stopped before it began"])
@pytest.mark.parametrize("when_resumed", ["while B works", "at once"])
def test_a_task_carried_on_behind_the_job_that_got_past_it_is_not_stuck(monkeypatch, how_a_dies, when_resumed):
    monkeypatch.setattr(tq, "_QUEUE_HEARTBEAT_SECONDS", 0.02)
    monkeypatch.setattr(tq, "QUEUE_CONCURRENCY", 1)
    _carried_on_scenario(how_a_dies, when_resumed)


def test_a_job_that_got_past_a_dead_link_frees_the_line_at_once_even_when_that_task_runs_again(monkeypatch):
    """A link left unset with no runner behind it (however that came about):
    B goes past it; the user then carries that task on under the same id
    behind B, and C queues behind that. When B ends the carried-on task starts
    at once, not after a heartbeat spent asking about a task id that now
    names another runner, and C follows it."""
    monkeypatch.setattr(tq, "QUEUE_CONCURRENCY", 1)
    monkeypatch.setattr(tq, "_QUEUE_HEARTBEAT_SECONDS", 0.02)
    log: list[str] = []

    def waits(name, hold=None):
        async def worker(previous, done):
            try:
                await tq.wait_for_queue_turn(previous, name, None)
                log.append(name)
                if hold is not None:
                    await hold.wait()
            finally:
                done.set()
        return worker

    async def scenario():
        for task_id in ("serial-free-a", "serial-free-b", "serial-free-c"):
            upsert_job(task_id=task_id, status="queued", client_id=None, stage="queued", progress=0)
        hold_b = asyncio.Event()
        tq._queue_tail_record("serial-free-a", tq._ChainLink(None))  # nobody will release it
        await tq._start_behind_queue(task_id="serial-free-b", client_id=None, route="t", stage="x",
                                     chained_worker=waits("serial-free-b", hold_b))
        assert await _until_true(lambda: log == ["serial-free-b"]), "B never got past the dead link"
        await tq._start_behind_queue(task_id="serial-free-a", client_id=None, route="t", stage="x",
                                     chained_worker=waits("serial-free-a"))
        await tq._start_behind_queue(task_id="serial-free-c", client_id=None, route="t", stage="x",
                                     chained_worker=waits("serial-free-c"))
        # From here a missed release costs a whole (long) heartbeat at least.
        monkeypatch.setattr(tq, "_QUEUE_HEARTBEAT_SECONDS", 30.0)
        assert await _until_true(lambda: log == ["serial-free-b"]), log
        hold_b.set()
        done = await _until_true(lambda: log == ["serial-free-b", "serial-free-a", "serial-free-c"], 1.0)
        if not done:
            for task in list(tq._LINK_RELAYS):
                task.cancel()
        assert done, f"the line stopped behind B: {log}"

    asyncio.run(scenario())


@pytest.mark.parametrize("stopped", [None, "r2 waiting", "s1 running", "s2 running", "r1 running"])
def test_recordings_and_jobs_that_never_wait_mixed_run_one_at_a_time_and_all_finish(monkeypatch, stopped):
    """r1, s1, r2, s2, r3 submitted in turn (r = a recording, s = a job with
    nothing to transcribe), with one of them stopped: no two recordings run
    together, and every one not stopped runs."""
    monkeypatch.setattr(tq, "_QUEUE_HEARTBEAT_SECONDS", 0.02)
    monkeypatch.setattr(tq, "QUEUE_CONCURRENCY", 1)
    running: set = set()
    overlap: list = []
    order: list[str] = []

    def recording(name, hold):
        async def worker(previous, done):
            try:
                await tq.wait_for_queue_turn(previous, f"serial-mix-{name}", None)
                running.add(name)
                order.append(name)
                if len(running) > 1:
                    overlap.append(set(running))
                await hold.wait()
            finally:
                running.discard(name)
                done.set()
        return worker

    def never_waits(name, hold):
        async def worker(_previous, done):
            done.set()
            try:
                order.append(name)
                await hold.wait()
            finally:
                done.set()
        return worker

    async def scenario():
        holds = {name: asyncio.Event() for name in ("r1", "s1", "r2", "s2", "r3")}
        kinds = {"r1": recording, "s1": never_waits, "r2": recording, "s2": never_waits, "r3": recording}
        for name, hold in holds.items():
            upsert_job(task_id=f"serial-mix-{name}", status="queued", client_id=None, stage="queued", progress=0)
            await tq._start_behind_queue(task_id=f"serial-mix-{name}", client_id=None, route="t", stage="x",
                                         chained_worker=kinds[name](name, hold))
            await asyncio.sleep(0.02)
        holds["s1"].set()
        holds["s2"].set()
        if stopped:
            await tq.JOB_EVENTS.cancel(f"serial-mix-{stopped.split()[0]}")
        await asyncio.sleep(0.2)
        if stopped != "r1 running":
            assert "r2" not in order and "r3" not in order, f"a recording started while r1 transcribed: {order}"
        for name in ("r1", "r2", "r3"):
            holds[name].set()
            await asyncio.sleep(0.15)
        expected = {"r1", "s1", "r2", "s2", "r3"} - ({"r2"} if stopped == "r2 waiting" else set())
        assert await _until_true(lambda: set(order) == expected, 2.0), order

    asyncio.run(scenario())
    assert not overlap, f"recordings ran together: {overlap}"


# ── taking a place in line before anything is written ──────────────────────
#
# The steps of a task are read and written off the event loop (decision 8.30),
# so the queue takes the task's place in line before those awaits. Review of
# 2026-11-27: arriving order and a stop pressed meanwhile both depend on it.

def test_two_submissions_keep_the_order_they_arrived_in(monkeypatch):
    """Two submissions arriving together are queued in the order they arrived:
    the first one's step write is held, and the second must not overtake it."""
    from backend.core import task_steps

    entered: list[str] = []
    release = asyncio.Event()
    real_plan = task_steps.plan_off_loop

    async def slow_plan(task_id):
        entered.append(task_id)
        if len(entered) == 1:
            await release.wait()  # the first arrival is held here
            # Long enough that, without a place in line taken before this
            # write, the second one reaches its own place first.
            await asyncio.sleep(0.2)
        else:
            release.set()  # the second one lets it go, and rushes on
        await real_plan(task_id)

    monkeypatch.setattr(task_steps, "plan_off_loop", slow_plan)

    async def worker(_previous, done):
        await asyncio.sleep(0.05)
        done.set()

    async def scenario():
        first = asyncio.ensure_future(tq._start_behind_queue(
            task_id="order-a", client_id=None, route="t", stage="x", chained_worker=worker))
        assert await _until_true(lambda: entered == ["order-a"]), entered
        second = asyncio.ensure_future(tq._start_behind_queue(
            task_id="order-b", client_id=None, route="t", stage="x", chained_worker=worker))
        assert await _until_true(lambda: "order-b" in entered), entered
        in_line = [link["task_id"] for link in tq._QUEUE_LINE if link["task_id"] in {"order-a", "order-b"}]
        assert in_line == ["order-a", "order-b"], in_line
        assert await asyncio.gather(first, second) == [True, True]

    asyncio.run(scenario())


def test_a_stop_pressed_while_the_steps_are_written_keeps_the_task_stopped(monkeypatch):
    """A task stopped between taking its place in line and starting its runner
    does not start afterwards: ``JOB_EVENTS.cancel`` finds no runner then, and
    a runner that started would clear the stop (``task_stop.begin``)."""
    from backend.core import task_actions, task_flow
    from backend.core import task_steps

    task_id = "queue-stopped-while-planning"
    upsert_job(task_id=task_id, status="queued", client_id=None, stage="queued", progress=0,
               metadata={task_flow.RESTART_RESUMED_KEY: {"step": task_steps.FETCH, "at": "2026-11-27T00:00:00"}})
    ran: list[str] = []
    planning = asyncio.Event()
    release = asyncio.Event()
    real_plan = task_steps.plan_off_loop

    async def slow_plan(tid):
        await real_plan(tid)
        planning.set()
        await release.wait()

    monkeypatch.setattr(task_steps, "plan_off_loop", slow_plan)

    async def worker(_previous, done):
        ran.append(task_id)
        done.set()

    async def scenario():
        start = asyncio.ensure_future(tq._start_behind_queue(
            task_id=task_id, client_id=None, route="t", stage="x", chained_worker=worker))
        assert await _until_true(planning.is_set), "the steps were never written"
        # The user presses 停止 now, while the queue still holds the plan write.
        await task_actions.stop_step(task_id, None)
        release.set()
        assert await start is False, "the stopped task started anyway"
        await _settle()
        assert ran == []
        steps = task_steps.statuses(task_steps.steps_of([task_id]).get(task_id, []))
        assert steps[task_steps.FETCH] == task_steps.STOPPED, steps
        assert not (job_store.get_job(task_id).get("metadata") or {}).get(task_flow.RESTART_RESUMED_KEY)

    asyncio.run(scenario())
