"""The doors a task comes in through, as the page and the Agent API use them.

Through the real local app over HTTP: one upload, a batch of uploads, a folder
on this machine, and a retry. What the user must be able to count on:

- Submitting creates a task they can find at once, under the name of their
  file (the whole name, without the extension), and the answer tells them
  where it stands in the queue.
- A file that cannot be processed is refused at the door with a reason they
  can read, and leaves no half-made task behind.
- Two submissions never run at the same time: the second starts only after
  the first has finished.

The pipeline itself is faked (it is ffmpeg plus a model); everything from the
HTTP request to the job row is real. ``tests/test_serial_queue_entries.py``
proves the chain at the function level and ``tests/test_local_folder_intake.py``
covers the system-dialog entry and the in-place retry; neither is repeated.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.local_main as local_main
from backend.core import job_store, media_preflight
from backend.core.job_event_hub import JobEventHub
from backend.core.local_error_diagnostics import diagnose_error
from backend.core.local_request_scope import LOCAL_OWNER_ID
from backend.core.storage_paths import _source_storage_dir
from backend.routers import local_processing as lp
from backend.routers import local_video_sources as lvs

CANNED_NOTE = "# 笔记\n\n正文"


class _Passed:
    duration_seconds = 12.0

    def as_metadata(self):
        return {"duration_seconds": 12.0, "format_name": "mov,mp4", "audio_stream_count": 1}


def _has_chinese(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


def _user_reads(detail: str) -> dict:
    """The page and the Agent API both pass the HTTP detail through the same
    diagnosis; this is the sentence the user ends up with."""
    return diagnose_error(detail)


@pytest.fixture()
def pipeline(monkeypatch):
    """A fake pipeline: records every run, takes a moment, finishes with a
    canned result. Overlap would show up as interleaved start/finish entries."""
    log: list[str] = []
    contexts: list = []

    async def fake_run_job_then_note(ctx):
        log.append(f"start:{ctx.task_id_value}")
        contexts.append(ctx)
        await asyncio.sleep(0.05)
        result = {
            "task_id": ctx.task_id_value,
            "display_title": ctx.display_title_value,
            "summary_markdown": CANNED_NOTE,
            "transcript_text": "你好",
        }
        job_store.upsert_job(
            task_id=ctx.task_id_value, status="completed", client_id=ctx.client_id,
            stage="done", progress=100, result=result,
        )
        await lp.JOB_EVENTS.publish(ctx.task_id_value, {"stage": "done", "progress": 100, "result": result})
        log.append(f"finish:{ctx.task_id_value}")

    monkeypatch.setattr(lp, "_run_job_then_note", fake_run_job_then_note)
    return {"log": log, "contexts": contexts}


@pytest.fixture()
def client(monkeypatch, pipeline):
    """The real local app with a fresh hub and an empty chain.

    ffprobe is the one expensive edge faked here; the uploads are a few bytes.
    Startup's own re-queue of restart-interrupted tasks is stubbed so leftovers
    from other test files cannot join this test's queue.
    """
    hub = JobEventHub()
    monkeypatch.setattr(lp, "JOB_EVENTS", hub)
    monkeypatch.setattr(lvs, "JOB_EVENTS", hub)
    lp._QUEUE_RECENT.clear()
    monkeypatch.setattr(lp, "preflight_media_file", lambda _path: _Passed())

    async def no_resume(_task_ids):
        return 0

    monkeypatch.setattr(local_main, "resume_interrupted_jobs", no_resume)
    with TestClient(local_main.create_local_app()) as test_client:
        yield test_client
    lp._QUEUE_RECENT.clear()


def _wait_until_done(client: TestClient, task_id: str, seconds: float = 5.0) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        job = client.get(f"/jobs/{task_id}").json()
        if job.get("status") in {"completed", "failed", "cancelled"}:
            return job
        time.sleep(0.02)
    raise AssertionError(f"{task_id} never finished: {client.get(f'/jobs/{task_id}').json()}")


def _upload(name: str, content: bytes = b"not really a video") -> tuple[str, bytes, str]:
    return (name, content, "video/mp4")


# ── one upload ──────────────────────────────────────────────────────────────

def test_a_single_upload_creates_a_task_under_the_files_full_name_and_streams_to_done(client, pipeline):
    response = client.post(
        "/process",
        files={"file": _upload("5.投资人视角下的AI浪潮.mp4")},
        data={"task_id": "http-single", "note_mode": "auto"},
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"stage": "done"' in response.text, "the stream ends with the finished task"

    job = _wait_until_done(client, "http-single")
    assert job["status"] == "completed"
    assert job["source_filename"] == "5.投资人视角下的AI浪潮.mp4"
    assert job["source_type"] == "video"
    assert job["metadata"]["display_title"] == "5.投资人视角下的AI浪潮", "not cut at the first dot"
    assert job["metadata"]["raw_title"] == "5.投资人视角下的AI浪潮"
    assert job["metadata"]["route"] == "/process"
    assert job["metadata"]["queue_options"]["note_mode"] == "auto"
    assert job["result"]["summary_markdown"] == CANNED_NOTE
    assert job["task_snapshot"]
    assert pipeline["log"] == ["start:http-single", "finish:http-single"]
    ctx = pipeline["contexts"][0]
    assert ctx.display_title_value == "5.投资人视角下的AI浪潮"
    assert Path(ctx.in_path).parent == _source_storage_dir() / "http-single", "the upload was stored under its task"
    assert Path(ctx.in_path).read_bytes() == b"not really a video"


def test_a_title_the_user_typed_wins_over_the_file_name(client, pipeline):
    client.post("/process", files={"file": _upload("rec-0811.mp4")}, data={"task_id": "http-titled", "title": "周一例会"})

    job = _wait_until_done(client, "http-titled")
    assert job["metadata"]["display_title"] == "周一例会"
    assert job["metadata"]["queue_options"]["title"] == "周一例会"


def test_a_task_id_already_in_use_is_refused(client, pipeline):
    client.post("/process", files={"file": _upload("a.mp4")}, data={"task_id": "http-dup"})
    _wait_until_done(client, "http-dup")

    response = client.post("/process", files={"file": _upload("b.mp4")}, data={"task_id": "http-dup"})

    assert response.status_code == 409
    assert pipeline["log"].count("start:http-dup") == 1


# ── what is refused at the door ─────────────────────────────────────────────

def _no_task_was_left_behind(filename: str) -> None:
    rows = job_store.list_jobs(client_id=LOCAL_OWNER_ID, limit=200)
    assert not [row for row in rows if row.get("source_filename") == filename]


def test_an_unsupported_file_type_is_refused_with_a_readable_reason_and_no_task(client, pipeline):
    response = client.post("/process", files={"file": _upload("slides.pptx")}, data={"task_id": "http-pptx"})

    assert response.status_code == 400
    shown = _user_reads(response.json()["detail"])
    assert shown["code"] == "unsupported_file_type" and _has_chinese(shown["detail"])
    assert job_store.get_job("http-pptx") is None
    assert pipeline["log"] == []


def test_an_upload_over_the_size_limit_is_refused_with_a_readable_reason_and_no_task(client, pipeline, monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_MAX_UPLOAD_MB", "1")

    response = client.post(
        "/process",
        files={"file": _upload("huge.mp4", b"x" * (1024 * 1024 + 1))},
        data={"task_id": "http-huge"},
    )

    assert response.status_code == 413
    shown = _user_reads(response.json()["detail"])
    assert shown["code"] == "file_too_large" and _has_chinese(shown["detail"])
    assert job_store.get_job("http-huge") is None, "the reserved task id is released"
    assert not (_source_storage_dir() / "http-huge").exists(), "no partial upload is kept"
    assert pipeline["log"] == []


def test_an_empty_file_is_refused_with_a_readable_reason_and_a_failed_task_that_explains(client, pipeline, monkeypatch):
    """An empty upload passes the suffix and size checks; the media check
    refuses it before anything runs. The real check runs here — its empty-file
    rule needs no ffprobe."""
    monkeypatch.setattr(lp, "preflight_media_file", media_preflight.preflight_media_file)

    response = client.post("/process", files={"file": _upload("empty.mp4", b"")}, data={"task_id": "http-empty"})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert _has_chinese(detail) and "为空" in detail
    assert _user_reads(detail)["code"] == "media_file_empty"
    job = job_store.get_job("http-empty")
    assert job["status"] == "failed" and job["error_reason"] == detail
    assert not (_source_storage_dir() / "http-empty").exists()
    assert pipeline["log"] == []


def test_a_batch_with_one_bad_file_is_refused_whole_and_queues_nothing(client, pipeline):
    response = client.post(
        "/queue/process",
        files=[("files", _upload("good.mp4")), ("files", _upload("bad.exe"))],
    )

    assert response.status_code == 400
    assert _user_reads(response.json()["detail"])["code"] == "unsupported_file_type"
    _no_task_was_left_behind("good.mp4")
    assert pipeline["log"] == []


def test_a_batch_over_the_size_limit_is_refused_whole_and_queues_nothing(client, pipeline, monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_MAX_UPLOAD_MB", "1")

    response = client.post(
        "/queue/process",
        files=[("files", _upload("small.mp4")), ("files", _upload("big.mp4", b"x" * (1024 * 1024 + 1)))],
    )

    assert response.status_code == 413
    assert _user_reads(response.json()["detail"])["code"] == "file_too_large"
    _no_task_was_left_behind("small.mp4")
    _no_task_was_left_behind("big.mp4")
    assert pipeline["log"] == []


def test_too_many_files_at_once_is_refused_with_a_readable_reason(client, pipeline, monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_MAX_QUEUE_FILES", "2")

    response = client.post(
        "/queue/process",
        files=[("files", _upload(f"part-{i}.mp4")) for i in range(3)],
    )

    assert response.status_code == 413
    detail = response.json()["detail"]
    shown = _user_reads(detail)
    assert _has_chinese(shown["detail"]) and shown["code"] != "unknown_error", detail
    assert pipeline["log"] == []


# ── a batch, and the order it runs in ───────────────────────────────────────

def test_a_batch_answers_each_files_place_in_the_queue_and_runs_them_one_after_another(client, pipeline):
    response = client.post(
        "/queue/process",
        files=[("files", _upload("1.2-1.3 批判性思维_合并.mp4")), ("files", _upload("lecture.mp4"))],
        data={"note_mode": "auto"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["count"] == 2
    first, second = body["queued"]
    assert first["queue_position"] == 1 and first["queue_total"] == 2
    assert second["queue_position"] == 2 and second["queue_total"] == 2
    assert first["filename"] == "1.2-1.3 批判性思维_合并.mp4" and second["filename"] == "lecture.mp4"
    assert first["task_id"] != second["task_id"]
    assert {first["status"], second["status"]} == {"queued"}

    done_first = _wait_until_done(client, first["task_id"])
    done_second = _wait_until_done(client, second["task_id"])
    assert done_first["metadata"]["display_title"] == "1.2-1.3 批判性思维_合并"
    assert done_second["metadata"]["display_title"] == "lecture"
    assert done_first["metadata"]["queue_position"] == 1 and done_second["metadata"]["queue_position"] == 2
    assert done_first["metadata"]["route"] == "/queue/process"
    assert pipeline["log"] == [
        f"start:{first['task_id']}", f"finish:{first['task_id']}",
        f"start:{second['task_id']}", f"finish:{second['task_id']}",
    ], "strictly one after the other"


def test_a_second_upload_waits_for_the_first_whichever_door_it_used(client, pipeline):
    """A single upload and a batch share one queue: the batch's first file
    starts only after the single upload has finished."""
    client.post("/process", files={"file": _upload("first.mp4")}, data={"task_id": "http-first"})
    batch = client.post("/queue/process", files=[("files", _upload("second.mp4"))]).json()
    second = batch["queued"][0]["task_id"]

    _wait_until_done(client, "http-first")
    _wait_until_done(client, second)

    assert pipeline["log"] == ["start:http-first", "finish:http-first", f"start:{second}", f"finish:{second}"]


# ── a folder on this machine ────────────────────────────────────────────────

@pytest.fixture()
def folder(tmp_path):
    recordings = tmp_path / "讲座"
    recordings.mkdir()
    (recordings / "5.投资人视角下的AI浪潮.mp4").write_bytes(b"one")
    (recordings / "第二讲.m4a").write_bytes(b"two")
    (recordings / "notes.txt").write_text("not media", encoding="utf-8")
    return recordings


def test_a_folder_preview_says_what_would_be_queued_and_queues_nothing(client, pipeline, folder):
    response = client.post("/queue/process-folder", json={"path": str(folder), "preview": True})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["preview"] is True and body["count"] == 2
    assert sorted(body["files"] if isinstance(body["files"][0], str) else [f["name"] for f in body["files"]]) == [
        "5.投资人视角下的AI浪潮.mp4", "第二讲.m4a",
    ]
    assert pipeline["log"] == []
    rows = job_store.list_jobs(client_id=LOCAL_OWNER_ID, limit=200)
    assert not [
        row for row in rows
        if ((row.get("metadata") or {}).get("folder_intake") or {}).get("folder") == str(folder)
    ], "a preview creates no task"


def test_a_folder_is_queued_in_place_with_positions_and_full_titles(client, pipeline, folder):
    response = client.post("/queue/process-folder", json={"path": str(folder)})

    assert response.status_code == 200, response.text
    queued = response.json()["queued"]
    assert [q["queue_position"] for q in queued] == [1, 2]
    assert all(q["queue_total"] == 2 and q["status"] == "queued" for q in queued)

    jobs = [_wait_until_done(client, q["task_id"]) for q in queued]
    titles = {job["metadata"]["display_title"] for job in jobs}
    assert titles == {"5.投资人视角下的AI浪潮", "第二讲"}
    for job in jobs:
        assert job["metadata"]["route"] == "/queue/process-folder"
        assert job["metadata"]["folder_intake"]["folder"] == str(folder)
        assert Path(job["metadata"]["folder_intake"]["original_path"]).parent == folder
    assert {Path(ctx.in_path).parent for ctx in pipeline["contexts"]} == {folder}, "read where they are, not copied"
    ids = [q["task_id"] for q in queued]
    assert pipeline["log"] == [f"start:{ids[0]}", f"finish:{ids[0]}", f"start:{ids[1]}", f"finish:{ids[1]}"]


def test_a_folder_that_does_not_exist_is_refused_by_name(client, pipeline, tmp_path):
    missing = tmp_path / "nowhere"

    response = client.post("/queue/process-folder", json={"path": str(missing)})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert _has_chinese(detail) and str(missing) in detail
    assert pipeline["log"] == []


# ── running one again ───────────────────────────────────────────────────────

def test_a_retry_queues_a_new_task_from_the_stored_upload_with_the_same_title_and_options(client, pipeline):
    client.post(
        "/process",
        files={"file": _upload("5.投资人视角下的AI浪潮.mp4")},
        data={"task_id": "http-orig", "note_mode": "high_fidelity", "title": "投资人讲座"},
    )
    _wait_until_done(client, "http-orig")
    job_store.upsert_job(task_id="http-orig", status="failed", client_id=LOCAL_OWNER_ID, error_reason="模型没有回应")

    response = client.post("/jobs/http-orig/retry")

    assert response.status_code == 200, response.text
    body = response.json()
    retry_id = body["task_id"]
    assert body["ok"] is True and body["source_task_id"] == "http-orig" and retry_id != "http-orig"
    assert body["job"]["status"] == "queued" and body["job"]["task_snapshot"]

    retried = _wait_until_done(client, retry_id)
    assert retried["status"] == "completed"
    assert retried["metadata"]["display_title"] == "投资人讲座"
    assert retried["metadata"]["queue_options"]["note_mode"] == "high_fidelity"
    assert retried["metadata"]["retry_source_task_id"] == "http-orig"
    assert retried["metadata"]["route"] == "/jobs/{task_id}/retry"
    assert retried["source_filename"] == "5.投资人视角下的AI浪潮.mp4"
    assert job_store.get_job("http-orig")["status"] == "failed", "the old record is left as it was"
    assert pipeline["log"][-2:] == [f"start:{retry_id}", f"finish:{retry_id}"]
    assert Path(pipeline["contexts"][-1].in_path).parent == _source_storage_dir() / retry_id, "its own copy"


def test_a_retry_of_a_task_still_running_is_refused(client, pipeline, monkeypatch):
    async def never_finishes(ctx):
        await asyncio.sleep(3600)

    monkeypatch.setattr(lp, "_run_job_then_note", never_finishes)
    client.post("/queue/process", files=[("files", _upload("slow.mp4"))])
    task_id = job_store.list_jobs(client_id=LOCAL_OWNER_ID, limit=1)[0]["task_id"]

    response = client.post(f"/jobs/{task_id}/retry")

    assert response.status_code == 409
    assert "Cancel" in response.json()["detail"] or "取消" in response.json()["detail"]


def test_a_retry_of_a_task_nobody_has_heard_of_is_a_404(client, pipeline):
    assert client.post("/jobs/never-existed/retry").status_code == 404


def test_a_retry_whose_recording_has_been_cleaned_up_says_so(client, pipeline):
    client.post("/process", files={"file": _upload("gone.mp4")}, data={"task_id": "http-gone"})
    _wait_until_done(client, "http-gone")
    job_store.upsert_job(task_id="http-gone", status="failed", client_id=LOCAL_OWNER_ID)
    for path in sorted((_source_storage_dir() / "http-gone").glob("*")):
        path.unlink()

    response = client.post("/jobs/http-gone/retry")

    assert response.status_code == 404
    assert _user_reads(response.json()["detail"])["code"] == "source_file_missing"
