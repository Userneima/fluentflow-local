from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.core.local_entry_guards as guards
import backend.routers.local_note_regen as local_note_regen
from backend.core import transcript_note
from backend.core.local_request_scope import LOCAL_OWNER_ID
from backend.routers.local_note_regen import router

_HEADERS = {"x-fluentflow-client-id": "desktop-a"}

_SRT = (
    "1\n00:00:00,000 --> 00:00:02,000\n大家好\n\n"
    "2\n00:00:02,000 --> 00:00:05,000\n今天讲本地版\n"
)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _summary_result(markdown: str = "# 笔记\n\n要点") -> SimpleNamespace:
    return SimpleNamespace(
        markdown=markdown,
        requested_mode="auto",
        resolved_mode="direct",
        chunk_count=1,
        segment_count=3,
        chapter_coverage={"chapters": []},
    )


def _install_store(monkeypatch):
    """In-memory job store + silenced telemetry."""
    jobs: dict[str, dict] = {}
    events: list[dict] = []

    def fake_upsert(task_id, **values):
        jobs[task_id] = {**jobs.get(task_id, {}), "task_id": task_id, **values}
        return jobs[task_id]

    def scoped_get(task_id, client_id=None, **kwargs):
        job = jobs.get(task_id)
        if job is None:
            return None
        if client_id is not None and job.get("client_id", LOCAL_OWNER_ID) != client_id:
            return None
        return job

    def fake_create(task_id, **values):
        if task_id in jobs:
            return False
        jobs[task_id] = {"task_id": task_id, **values}
        return True

    def fake_finalize_note(task_id, before, after, *, client_id=None, **values):
        """Like job_store.finalize_note_if_unchanged: refused when the note or
        transcript moved since ``before``; otherwise writes the changed fields
        onto the task as it is now."""
        job = scoped_get(task_id, client_id=client_id)
        if job is None or job.get("status") == "cancelled":
            return None
        current = dict(job.get("result") or {})
        if any(current.get(k) != (before or {}).get(k) for k in ("summary_markdown", "transcript_text")):
            return None
        for key in set(before or {}) | set(after):
            if (before or {}).get(key) != after.get(key):
                current[key] = after.get(key)
        jobs[task_id] = {**job, "client_id": client_id or job.get("client_id"), "result": current, "status": values["status"], "stage": values["stage"],
                         "progress": values["progress"], "summary_status": values["summary_status"]}
        return jobs[task_id]

    # The rewrite route's own module, and the transcript-file note it calls.
    for module in (local_note_regen, transcript_note):
        monkeypatch.setattr(module, "upsert_job", fake_upsert, raising=False)
        monkeypatch.setattr(module, "get_job", lambda task_id, client_id=None: jobs.get(task_id), raising=False)
        monkeypatch.setattr(module, "finalize_note_if_unchanged", fake_finalize_note, raising=False)
        monkeypatch.setattr(module, "log_event", lambda **values: events.append(values), raising=False)
        monkeypatch.setattr(module, "_attach_result_artifacts", lambda task_id, result: result, raising=False)
    monkeypatch.setattr(guards, "get_job", scoped_get)
    monkeypatch.setattr(guards, "create_job_if_absent", fake_create)
    return jobs, events


# ---- /regenerate-summary -----------------------------------------------------

def test_regenerate_merges_into_existing_job(monkeypatch):
    jobs, _ = _install_store(monkeypatch)
    jobs["t1"] = {
        "task_id": "t1",
        "result": {"task_id": "t1", "filename": "a.mp4", "transcript_text": "旧文本"},
    }
    captured: dict = {}

    def fake_summarize(transcript, **kwargs):
        captured.update({"transcript": transcript, **kwargs})
        return _summary_result()

    monkeypatch.setattr(local_note_regen, "summarize_transcript_with_metadata", fake_summarize)

    r = _client().post(
        "/regenerate-summary",
        headers=_HEADERS,
        data={"transcript": "新文本", "task_id": "t1", "note_mode": "direct"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == "t1"
    assert body["summary_markdown"].startswith("# 笔记")
    assert body["resolved_note_mode"] == "direct"
    assert body["regenerated_from_task_id"] is None
    assert captured["transcript"] == "新文本"
    assert captured["note_mode"] == "direct"
    job = jobs["t1"]
    assert job["status"] == "completed"
    assert job["client_id"] == LOCAL_OWNER_ID
    assert job["result"]["filename"] == "a.mp4"  # existing result preserved
    assert job["result"]["summary_markdown"].startswith("# 笔记")
    # A text rewrite is the text model's note; the page must stop describing it
    # as Claude reading the frames.
    assert job["result"]["summary_written_from"] == "text_regeneration"
    # The version it replaced records a rewrite (owner, 2026-10-22).
    from backend.core import note_versions

    assert note_versions.changed_by(job["result"]) == note_versions.REWRITE


def test_regenerate_does_not_overwrite_note_edited_while_ai_runs(monkeypatch):
    jobs, events = _install_store(monkeypatch)
    jobs["t1"] = {
        "task_id": "t1",
        "client_id": LOCAL_OWNER_ID,
        "status": "completed",
        "result": {
            "task_id": "t1",
            "transcript_text": "文本",
            "summary_markdown": "# 原笔记",
        },
    }

    def fake_summarize(transcript, **kwargs):
        jobs["t1"]["result"] = {
            **jobs["t1"]["result"],
            "summary_markdown": "# 用户编辑",
        }
        return _summary_result("# AI 新笔记")

    monkeypatch.setattr(local_note_regen, "summarize_transcript_with_metadata", fake_summarize)

    response = _client().post(
        "/regenerate-summary",
        headers=_HEADERS,
        data={"transcript": "文本", "task_id": "t1"},
    )

    assert response.status_code == 409
    assert jobs["t1"]["status"] == "completed"
    assert jobs["t1"]["result"]["summary_markdown"] == "# 用户编辑"
    assert not any(event.get("success") is True for event in events)


def test_regenerate_does_not_overwrite_edit_saved_after_conflict_check(monkeypatch):
    jobs, events = _install_store(monkeypatch)
    jobs["t-late"] = {
        "task_id": "t-late",
        "client_id": LOCAL_OWNER_ID,
        "status": "completed",
        "result": {
            "task_id": "t-late",
            "transcript_text": "文本",
            "summary_markdown": "# 原笔记",
        },
    }
    monkeypatch.setattr(
        local_note_regen,
        "summarize_transcript_with_metadata",
        lambda transcript, **kwargs: _summary_result("# AI 新笔记"),
    )

    original_bind = local_note_regen.bind_chapter_coverage_time_ranges

    def edit_after_check(result):
        jobs["t-late"]["result"] = {
            **jobs["t-late"]["result"],
            "summary_markdown": "# 用户在检查后保存的编辑",
        }
        return original_bind(result)

    monkeypatch.setattr(
        local_note_regen, "bind_chapter_coverage_time_ranges", edit_after_check
    )
    response = _client().post(
        "/regenerate-summary",
        headers=_HEADERS,
        data={"transcript": "文本", "task_id": "t-late"},
    )

    assert response.status_code == 409
    assert jobs["t-late"]["result"]["summary_markdown"] == "# 用户在检查后保存的编辑"


def test_regenerate_unknown_task_gets_new_id(monkeypatch):
    jobs, _ = _install_store(monkeypatch)
    monkeypatch.setattr(
        local_note_regen,
        "summarize_transcript_with_metadata",
        lambda transcript, **kwargs: _summary_result(),
    )

    r = _client().post(
        "/regenerate-summary",
        headers=_HEADERS,
        data={"transcript": "文本", "task_id": "gone-task"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["regenerated_from_task_id"] == "gone-task"
    assert body["task_id"] != "gone-task"
    assert body["task_id"] in jobs


def test_regenerate_uses_local_credential_store(monkeypatch):
    _install_store(monkeypatch)
    monkeypatch.setattr(
        guards, "resolve_secret",
        lambda form_value, name: (form_value or "").strip() or f"stored-{name}",
    )
    captured: dict = {}
    monkeypatch.setattr(
        local_note_regen, "summarize_transcript_with_metadata",
        lambda transcript, **kwargs: captured.update(kwargs) or _summary_result(),
    )

    r = _client().post(
        "/regenerate-summary",
        headers=_HEADERS,
        data={"transcript": "文本", "ai_provider": "openai"},
    )

    assert r.status_code == 200
    assert captured["provider"] == "openai"
    assert captured["api_key"] == "stored-openai_api_key"


def test_regenerate_failure_returns_friendly_500(monkeypatch):
    jobs, events = _install_store(monkeypatch)

    def boom(transcript, **kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(local_note_regen, "summarize_transcript_with_metadata", boom)

    r = _client().post(
        "/regenerate-summary",
        headers=_HEADERS,
        data={"transcript": "文本", "task_id": "t9"},
    )

    assert r.status_code == 500
    assert r.json()["detail"].strip()
    failed = [job for job in jobs.values() if job.get("status") == "failed"]
    assert failed and failed[0]["summary_status"] == "failed"
    assert any(event["event_name"] == "task_failed" for event in events)


# ---- /summarize-transcript-file ----------------------------------------------

def test_transcript_file_rejects_unknown_suffix(monkeypatch):
    jobs, _ = _install_store(monkeypatch)
    r = _client().post(
        "/summarize-transcript-file",
        headers=_HEADERS,
        files={"file": ("notes.pdf", b"x", "application/pdf")},
    )
    assert r.status_code == 400
    assert jobs == {}


def test_transcript_file_rejects_empty_transcript(monkeypatch):
    jobs, _ = _install_store(monkeypatch)
    r = _client().post(
        "/summarize-transcript-file",
        headers=_HEADERS,
        files={"file": ("notes.txt", b"   \n  ", "text/plain")},
        data={"task_id": "empty-1"},
    )
    assert r.status_code == 400
    assert jobs["empty-1"]["status"] == "failed"


def test_transcript_file_size_limit(monkeypatch):
    _install_store(monkeypatch)
    monkeypatch.setattr(local_note_regen, "max_transcript_upload_mb", lambda: 1.0)
    r = _client().post(
        "/summarize-transcript-file",
        headers=_HEADERS,
        files={"file": ("notes.txt", b"x" * (2 * 1024 * 1024), "text/plain")},
    )
    assert r.status_code == 413


def test_transcript_file_duration_limit(monkeypatch):
    jobs, _ = _install_store(monkeypatch)
    monkeypatch.setattr(transcript_note, "max_media_duration_seconds", lambda: 3.0)
    r = _client().post(
        "/summarize-transcript-file",
        headers=_HEADERS,
        files={"file": ("talk.srt", _SRT.encode("utf-8"), "text/plain")},
        data={"task_id": "long-1"},
    )
    assert r.status_code == 413
    assert jobs["long-1"]["status"] == "failed"


def test_transcript_only_mode_skips_summary(monkeypatch):
    jobs, _ = _install_store(monkeypatch)

    def unexpected(*args, **kwargs):
        raise AssertionError("summarizer must not run in transcript-only mode")

    monkeypatch.setattr(transcript_note, "summarize_transcript_with_metadata", unexpected)

    r = _client().post(
        "/summarize-transcript-file",
        headers=_HEADERS,
        files={"file": ("talk.srt", _SRT.encode("utf-8"), "text/plain")},
        data={"skip_summary": "1", "task_id": "only-1"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["summary_skipped"] is True
    assert body["summary_status"] == "skipped"
    assert "本地版" in body["transcript_text"]
    assert jobs["only-1"]["status"] == "completed"
    assert jobs["only-1"]["summary_status"] == "skipped"


def test_transcript_file_summary_success(monkeypatch):
    jobs, events = _install_store(monkeypatch)
    monkeypatch.setattr(
        transcript_note, "summarize_transcript_with_metadata",
        lambda transcript, **kwargs: _summary_result("# 本地笔记"),
    )

    r = _client().post(
        "/summarize-transcript-file",
        headers=_HEADERS,
        files={"file": ("talk.srt", _SRT.encode("utf-8"), "text/plain")},
        data={"task_id": "sum-1", "prompt_preset": "lecture"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["summary_markdown"] == "# 本地笔记"
    assert body["summary_status"] == "completed"
    assert body["resolved_note_mode"] == "direct"
    assert body["prompt_preset"] == "lecture"
    assert body["display_segments"]
    assert jobs["sum-1"]["status"] == "completed"
    names = [event["event_name"] for event in events]
    assert "source_imported" in names
    assert "transcript_ready" in names
    assert "summary_completed" in names


def test_transcript_file_summary_failure_returns_partial_result(monkeypatch):
    jobs, _ = _install_store(monkeypatch)

    def boom(transcript, **kwargs):
        raise RuntimeError("AI provider down")

    monkeypatch.setattr(transcript_note, "summarize_transcript_with_metadata", boom)

    r = _client().post(
        "/summarize-transcript-file",
        headers=_HEADERS,
        files={"file": ("talk.srt", _SRT.encode("utf-8"), "text/plain")},
        data={"task_id": "fail-1"},
    )

    assert r.status_code == 200  # transcript survives, summary marked failed
    body = r.json()
    assert body["summary_status"] == "failed"
    assert body["summary_error"].strip()
    assert "本地版" in body["transcript_text"]
    assert jobs["fail-1"]["summary_status"] == "failed"
    assert jobs["fail-1"]["status"] == "completed"


def test_regenerate_keeps_the_old_note_when_the_model_returns_nothing(monkeypatch):
    """An empty answer is a failed regeneration, not a new empty note: the note
    the user had stays, the task stays finished, and the reason says so in
    Chinese."""
    jobs, events = _install_store(monkeypatch)
    jobs["t-keep"] = {
        "task_id": "t-keep", "status": "completed", "client_id": LOCAL_OWNER_ID,
        "summary_status": "completed",
        "result": {"task_id": "t-keep", "transcript_text": "文本", "summary_markdown": "# 原来的笔记"},
    }
    monkeypatch.setattr(
        local_note_regen, "summarize_transcript_with_metadata",
        lambda transcript, **kwargs: _summary_result(markdown="   \n"),
    )

    r = _client().post(
        "/regenerate-summary",
        headers=_HEADERS,
        data={"transcript": "文本", "task_id": "t-keep"},
    )

    assert r.status_code >= 500
    assert "没有返回" in r.json()["detail"] and "保留" in r.json()["detail"]
    job = jobs["t-keep"]
    assert job["result"]["summary_markdown"] == "# 原来的笔记"
    assert job["status"] == "completed", "the task itself did not fail, only this regeneration"
    assert job["summary_status"] == "failed"
    assert "没有返回" in job["error_reason"]
    assert any(e["event_name"] == "summary_regenerated" and e["success"] is False for e in events)


def test_regenerate_without_a_previous_note_reports_nothing_was_written(monkeypatch):
    jobs, events = _install_store(monkeypatch)
    monkeypatch.setattr(
        local_note_regen, "summarize_transcript_with_metadata",
        lambda transcript, **kwargs: _summary_result(markdown=""),
    )

    r = _client().post("/regenerate-summary", headers=_HEADERS, data={"transcript": "文本"})

    assert r.status_code >= 500
    assert "没有返回" in r.json()["detail"]
    failed = [job for job in jobs.values() if job.get("summary_status") == "failed"]
    assert failed and failed[0]["status"] == "failed"
    assert not any(job.get("result", {}).get("summary_markdown") == "" for job in jobs.values())


def test_a_submitted_transcript_is_recorded_as_fluentflows_own_text(monkeypatch):
    """The glossary step, run later on this task, leaves alone the sentences
    the user edits; it can only tell them apart when the submitted text was
    recorded when the task was made."""
    from backend.core import glossary_correction as gc

    jobs, _ = _install_store(monkeypatch)
    r = _client().post(
        "/summarize-transcript-file",
        headers=_HEADERS,
        files={"file": ("talk.srt", _SRT.encode("utf-8"), "text/plain")},
        data={"skip_summary": "1", "task_id": "marks-1"},
    )

    assert r.status_code == 200
    result = jobs["marks-1"]["result"]
    assert gc.edited_by_user(result) == set()
    result["raw_segments"][1]["text"] = "今天讲本地版，我改过"
    assert gc.edited_by_user(result) == {1}
