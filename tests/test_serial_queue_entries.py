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

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from backend.core.job_event_hub import JobEventHub
from backend.core.job_store import upsert_job
from backend.core.video_source import SavedVideoSource
from backend.routers import local_job_debreath, local_job_visual_note
from backend.routers import local_processing as lp
from backend.routers import local_video_sources as lvs


class _Passed:
    duration_seconds = 12.0

    def as_metadata(self):
        return {"duration_seconds": 12.0}


@pytest.fixture(autouse=True)
def _fresh_chain(monkeypatch):
    """A hub without the keep-awake guard, and an empty chain, per test."""
    hub = JobEventHub()
    monkeypatch.setattr(lp, "JOB_EVENTS", hub)
    monkeypatch.setattr(lvs, "JOB_EVENTS", hub)
    lp._QUEUE_RECENT.clear()
    lp._QUEUE_LINE.clear()
    yield hub
    lp._QUEUE_RECENT.clear()
    lp._QUEUE_LINE.clear()


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
    monkeypatch.setattr(lvs, "preflight_media_file", lambda _p: _Passed())
    monkeypatch.setattr(lp, "_run_pipeline", fake_pipeline)
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
        first = lp._QUEUE_RECENT[-1]["task_id"]
        gates[first] = asyncio.Event()

        link = await lvs.submit_video_source_job(
            input_text="https://example.com/v/1", title="", raw_options={}, client_id="local-single-user"
        )
        second = link["task_id"]
        gates[second] = asyncio.Event()

        in_place = await lp.queue_local_media_file(
            recording, client_id="local-single-user", options={}, duration_limit_seconds=None,
            route="/queue/process-local-files", origin={"chosen_with": "test"},
        )
        third = in_place["task_id"]
        gates[third] = asyncio.Event()

        await _settle()
        assert log == [f"start:{first}"], "nothing may start beside the first job"
        gates[first].set()
        await _settle()
        assert log == [f"start:{first}", f"finish:{first}", f"start:{second}"]
        gates[second].set()
        await _settle()
        assert log[-2:] == [f"finish:{second}", f"start:{third}"]
        gates[third].set()
        await _settle()
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
    monkeypatch.setattr(lvs, "preflight_media_file", lambda _p: _Passed())
    monkeypatch.setattr(lp, "_run_pipeline", fake_pipeline)
    monkeypatch.setattr(lvs, "download_video_source", fake_download)
    monkeypatch.setattr(lvs, "get_preference", lambda name: None)
    recording = tmp_path / "r.mp4"
    recording.write_bytes(b"x")

    async def scenario():
        first = (await lp.queue_local_media_file(
            recording, client_id="local-single-user", options={}, duration_limit_seconds=None,
            route="/queue/process-local-files", origin={},
        ))["task_id"]
        second = (await lvs.submit_video_source_job(
            input_text="https://example.com/v/2", title="", raw_options={}, client_id="local-single-user"
        ))["task_id"]
        await _settle()
        assert log == [f"start:{first}", "download"]
        gate.set()
        await _settle()
        assert log == [f"start:{first}", "download", f"finish:{first}", f"start:{second}", f"finish:{second}"]

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

        assert lp.queue_is_busy() is False
        await lp._start_behind_queue(task_id="a", client_id=None, route="/t", stage="x", chained_worker=worker)
        await asyncio.sleep(0.01)
        busy_while_running = lp.queue_is_busy()
        gate.set()
        await asyncio.sleep(0.05)
        return busy_while_running, lp.queue_is_busy(), hub.is_running("a")

    assert asyncio.run(scenario()) == (True, False, False)


def test_a_link_whose_runner_is_gone_does_not_count_as_busy():
    """The stale-chain case the waiters heal: an unset event with no runner
    behind it must not block a de-breath forever either."""
    lp._queue_tail_record("vanished", asyncio.Event())

    assert lp.queue_is_busy() is False


# ---- the entries that cannot join the chain refuse while it is busy -------------

_COMPLETED = {"task_id": "t-done", "status": "completed", "result": {"task_id": "t-done"}}


def _debreath_client(monkeypatch, busy: bool) -> TestClient:
    monkeypatch.setattr(local_job_debreath, "get_job", lambda task_id, client_id=None: dict(_COMPLETED))
    monkeypatch.setattr(local_job_debreath, "queue_is_busy", lambda: busy)
    monkeypatch.setattr(local_job_debreath.debreath_job, "resolve_source", lambda task_id: Path("/tmp/x.mp4"))
    monkeypatch.setattr(local_job_debreath.debreath_job, "claim", lambda task_id: None)
    monkeypatch.setattr(local_job_debreath.debreath_job, "run_debreath", lambda *a, **k: None)
    monkeypatch.setattr(local_job_debreath.debreath_job, "release", lambda task_id: None)
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
    monkeypatch.setattr(local_job_visual_note, "get_job", lambda task_id, client_id=None: dict(_COMPLETED))
    monkeypatch.setattr(local_job_visual_note, "resolve_secret", lambda *a, **k: "key")
    monkeypatch.setattr(local_job_visual_note, "queue_is_busy", lambda: busy)
    monkeypatch.setattr(local_job_visual_note.visual_note_job, "describe", lambda *a, **k: dict(described))
    monkeypatch.setattr(local_job_visual_note.visual_note_job, "claim", lambda task_id: None)
    monkeypatch.setattr(local_job_visual_note.visual_note_job, "run_visual_note", lambda *a, **k: None)
    monkeypatch.setattr(local_job_visual_note.visual_note_job, "release", lambda task_id: None)
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

    async def no_pipeline(previous, done, ctx):
        done.set()

    monkeypatch.setattr(lp, "_run_serially", no_pipeline)

    asyncio.run(lp.queue_local_media_file(
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

    monkeypatch.setattr(lvs, "check_browser_cookies", probe)
    app = FastAPI()
    app.include_router(lvs.router)

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
    monkeypatch.setattr(lp, "_run_pipeline", fake_pipeline)
    monkeypatch.setattr(lp, "_write_note_after_transcript", record_note)
    monkeypatch.setattr(lvs, "download_video_source", lambda *a, **k: _saved_video(downloaded))
    monkeypatch.setattr(lvs, "get_preference", lambda name: None)

    async def scenario():
        task_id = (await lvs.submit_video_source_job(
            input_text="https://example.com/v/3", title="", raw_options={}, client_id="local-single-user"
        ))["task_id"]
        await _settle()
        await _settle()
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
    monkeypatch.setattr(lp, "_run_pipeline", _gated_pipeline(log, gates))
    files = []
    for name in ("a", "b", "c"):
        path = tmp_path / f"{name}.mp4"
        path.write_bytes(b"x")
        files.append(path)

    async def scenario():
        answers = []
        for path in files:
            # Submitted one at a time, each as its own "batch of one".
            answers.append(await lp.queue_local_media_file(
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
    monkeypatch.setattr(lp, "_run_pipeline", _gated_pipeline(log, gates))

    def ctx(task_id):
        return SimpleNamespace(task_id_value=task_id, client_id=None, event_route=None)

    async def start(task_id):
        return await lp._start_behind_queue(
            task_id=task_id, client_id=None, route="/test", stage="processing",
            chained_worker=lambda previous, done: lp._run_serially(previous, done, ctx(task_id)),
        )

    async def scenario():
        assert await start("running-a") is True
        await _settle()
        assert await start("running-a") is False, "the same id is not started twice"
        assert await start("waiting-b") is True
        await _settle()
        assert log == ["start:running-a"], "b started beside a"
        gates["running-a"].set()
        await _settle()
        assert log[-1] == "start:waiting-b"
        gates["waiting-b"].set()
        await _settle()

    asyncio.run(scenario())
