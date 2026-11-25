"""A subtitle task a restart or a stop cut off carries on as a subtitle task.

Requirement (independent review of the 2026-11-25 fixes, to-do 「字幕任务被重启
打断后续不上」): a task made from a subtitle file — one on this machine handed
over by path (decision 1.16), or the captions a link came with — was carried on
after a restart as if it were a recording. Cut off while it read the file, it
failed saying the recording was gone; cut off while FluentFlow's model wrote
its note, it was not known to work. Now, either way, it reads its subtitles
again and ends with the transcript and the note, and when the subtitle file is
gone the user is told it is the subtitle file that is missing.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.core.transcript_note as transcript_note
from backend.core import job_store, note_pdf, output_folder, storage_paths, task_actions, task_steps
from backend.core.output_folder import SUBTITLE_INTAKE_KEY

SRT = """1
00:00:01,000 --> 00:00:04,000
今天讲最小二乘法。

2
00:00:05,000 --> 00:00:09,000
先看误差平方和为什么能代表拟合得好不好。
"""

_counter = iter(range(10_000))


@pytest.fixture()
def task_id():
    return f"subtitle-resume-{next(_counter)}-{int(time.time() * 1000) % 100000}"


@pytest.fixture(autouse=True)
def model(monkeypatch):
    calls: list[str] = []

    def write(text, **_kw):
        calls.append(text)
        return SimpleNamespace(markdown="# 最小二乘法\n\n模型写的。", requested_mode="auto",
                               resolved_mode="direct", chunk_count=1)

    monkeypatch.setattr(transcript_note, "summarize_transcript_with_metadata", write)

    def export(task_id, *, client_id=None, output_dir=None, filename=None):
        path = Path(output_dir) / f"{filename}.pdf"
        path.write_bytes(b"%PDF fake")
        return {"task_id": task_id, "pdf_path": str(path), "html_path": None}

    monkeypatch.setattr(note_pdf, "export_pdf", export)
    return calls


_OPTIONS = {"intelligence": "fluentflow", "skip_summary": "false"}


def _in_place_task(task_id: str, tmp_path: Path, *, status: str) -> Path:
    folder = tmp_path / "1.短视频"
    folder.mkdir()
    srt = folder / "最小二乘法.srt"
    srt.write_text(SRT, encoding="utf-8")
    job_store.upsert_job(
        task_id=task_id, status=status, stage="import", progress=10, source_type="transcript_file",
        source_filename=srt.name,
        metadata={"route": "/agent/v1/tasks", "raw_title": "最小二乘法", "display_title": "最小二乘法",
                  "queue_options": _OPTIONS, "agent_input_type": "subtitle_file",
                  SUBTITLE_INTAKE_KEY: {"original_path": str(srt)}},
    )
    return srt


def _captions_task(task_id: str, *, status: str) -> None:
    target = storage_paths._source_storage_dir() / task_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "source.srt").write_text(SRT, encoding="utf-8")
    job_store.upsert_job(
        task_id=task_id, status=status, stage="import", progress=10, source_type="transcript_file",
        source_filename="最小二乘法.srt",
        metadata={"route": "/agent/v1/tasks", "raw_title": "最小二乘法", "display_title": "最小二乘法",
                  "queue_options": _OPTIONS, "video_source_url": "https://www.youtube.com/watch?v=abc",
                  "source_path": str(target / "source.srt")},
    )


def _cut_off_reading(task_id: str) -> None:
    task_steps.plan(task_id)
    task_steps.start(task_id, task_steps.FETCH)


def _cut_off_writing_the_note(task_id: str) -> None:
    task_steps.plan(task_id)
    task_steps.finish(task_id, task_steps.FETCH, task_steps.DONE)
    task_steps.start(task_id, task_steps.NOTE)


def _statuses(task_id):
    return task_steps.statuses(task_steps.steps_of([task_id]).get(task_id, []))


def _ended(task_id):
    return not any(status in task_steps.OPEN for status in _statuses(task_id).values())


async def _until(predicate, tries=500):
    for _ in range(tries):
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


def _restart(task_id) -> int:
    async def scenario():
        assert task_id in task_actions.stepped_tasks_cut_off()
        carried = await task_actions.resume_after_restart([task_id])
        assert await _until(lambda: _ended(task_id) and job_store.get_job(task_id)["status"] != "queued")
        return carried

    carried = asyncio.run(scenario())
    output_folder._WORKER.submit(lambda: None).result(timeout=10)
    return carried


def _assert_finished_with_its_note(task_id, model):
    job = job_store.get_job(task_id)
    assert job["status"] == "completed", job.get("error_reason")
    assert "误差平方和" in job["result"]["transcript_text"]
    assert "模型写的" in job["result"]["summary_markdown"]
    assert _statuses(task_id)[task_steps.FETCH] == task_steps.DONE
    assert _statuses(task_id)[task_steps.NOTE] == task_steps.DONE
    assert _statuses(task_id)[task_steps.TRANSCRIBE] == task_steps.SKIPPED, "nothing was transcribed"
    assert len(model) == 1
    assert not (job.get("metadata") or {}).get("restart_resumed"), "cleared once the run ends"


@pytest.mark.parametrize("cut_off", [_cut_off_reading, _cut_off_writing_the_note],
                         ids=["reading the file", "writing the note"])
def test_a_subtitle_file_by_path_carries_on_after_a_restart(task_id, tmp_path, model, cut_off):
    srt = _in_place_task(task_id, tmp_path, status="running")
    cut_off(task_id)

    assert _restart(task_id) == 1

    _assert_finished_with_its_note(task_id, model)
    folder = srt.parent / "最小二乘法"
    assert "模型写的" in (folder / "最小二乘法_笔记.md").read_text(encoding="utf-8")
    assert srt.read_text(encoding="utf-8") == SRT


@pytest.mark.parametrize("cut_off", [_cut_off_reading, _cut_off_writing_the_note],
                         ids=["reading the file", "writing the note"])
def test_a_links_captions_carry_on_after_a_restart(task_id, model, cut_off, monkeypatch):
    from backend.core import link_submission

    def no_download(*_a, **_kw):
        raise AssertionError("the captions are on disk: nothing is downloaded again")

    monkeypatch.setattr(link_submission, "download_video_source", no_download)
    _captions_task(task_id, status="running")
    cut_off(task_id)

    assert _restart(task_id) == 1

    _assert_finished_with_its_note(task_id, model)


def test_a_subtitle_file_gone_since_is_named_as_the_subtitle_file(task_id, tmp_path):
    srt = _in_place_task(task_id, tmp_path, status="cancelled")
    task_steps.plan(task_id)
    task_steps.start(task_id, task_steps.FETCH)
    task_steps.close_open(task_id, stopped=True)
    srt.unlink()

    with pytest.raises(task_actions.Refused) as refused:
        asyncio.run(task_actions.resume_task(task_id, None))

    assert "字幕文件" in refused.value.message and "录像" not in refused.value.message
    assert str(srt) in refused.value.message


# Review of 643178c: a graceful service stop cancels the worker, which marks
# the task cancelled (worker_cancelled) while its step stays open. When the
# restart cannot carry it on, it reads as failed by the restart, not as stopped
# by the user, and the page names the subtitle file that is missing (the
# generic text pointed to 重试, which a subtitle task cannot use, decision 6.82).

def _stopped_with_the_service(task_id, tmp_path, status):
    srt = _in_place_task(task_id, tmp_path, status="running")
    _cut_off_reading(task_id)
    if status == "cancelled":
        job_store.upsert_job(task_id=task_id, status="cancelled", stage="error", error_reason="worker_cancelled")
    return srt


def _left_for_the_user(task_id):
    assert task_id in task_actions.stepped_tasks_cut_off()
    assert asyncio.run(task_actions.resume_after_restart([task_id])) == 0
    job = job_store.get_job(task_id)
    assert job["status"] == "failed", job.get("error_reason")
    assert (job.get("metadata") or {}).get(job_store.RESTART_INTERRUPTION_KEY), "the page tells the user once"
    assert _statuses(task_id)[task_steps.FETCH] == task_steps.FAILED
    return job


@pytest.mark.parametrize("status", ["running", "cancelled"])
def test_a_subtitle_file_gone_after_a_restart_is_named_on_the_task(task_id, tmp_path, status):
    srt = _stopped_with_the_service(task_id, tmp_path, status)
    srt.unlink()

    job = _left_for_the_user(task_id)

    assert str(srt) in job["error_reason"] and "字幕文件" in job["error_reason"]
    assert "重试" not in job["error_reason"]


@pytest.mark.parametrize("status", ["running", "cancelled"])
def test_a_task_restarts_keep_cutting_off_reads_as_cut_off_by_the_restart(task_id, tmp_path, status):
    _stopped_with_the_service(task_id, tmp_path, status)
    job_store.update_job_metadata(task_id, {task_actions.RESTART_RESUME_COUNT_KEY: task_actions.RESTART_RESUME_LIMIT})

    job = _left_for_the_user(task_id)

    assert "服务重启中断" in job["error_reason"]


def test_a_stopped_subtitle_task_carries_on_when_the_user_asks(task_id, tmp_path, model):
    _in_place_task(task_id, tmp_path, status="cancelled")
    _cut_off_writing_the_note(task_id)
    task_steps.close_open(task_id, stopped=True)

    async def scenario():
        await task_actions.resume_task(task_id, None)
        assert await _until(lambda: _ended(task_id) and job_store.get_job(task_id)["status"] != "queued")

    asyncio.run(scenario())
    output_folder._WORKER.submit(lambda: None).result(timeout=10)
    _assert_finished_with_its_note(task_id, model)


# ── the user's transcript edits survive carrying the note on ────────────────
#
# Requirement (review of 2026-11-25): when only the note failed (the model
# failed) and the user then edited the transcript in FluentFlow, carrying the
# task on writes the note from their edited transcript and keeps it; it does
# not read the subtitles again, so neither the file's text nor a file changed
# on disk since replaces what they edited. A recording carried on at its note
# is not transcribed again either.

EDITED = "今天讲最小二乘法。先看残差平方和为什么能代表拟合得好不好。"
EDITED_SEGMENTS = [
    {"start": 1.0, "end": 4.0, "text": "今天讲最小二乘法。"},
    {"start": 5.0, "end": 9.0, "text": "先看残差平方和为什么能代表拟合得好不好。"},
]


def _first_run_the_model_fails(task_id, monkeypatch):
    def fails(_text, **_kw):
        raise RuntimeError("模型这次没有回答")

    monkeypatch.setattr(transcript_note, "summarize_transcript_with_metadata", fails)
    _cut_off_reading(task_id)
    _restart(task_id)
    job = job_store.get_job(task_id)
    assert job["status"] == "completed" and job["result"]["summary_status"] == "failed"
    assert _statuses(task_id)[task_steps.NOTE] == task_steps.FAILED


def _user_edits_the_transcript(task_id):
    """What the editor's save writes (``local_job_edit.update_job_transcript``)."""
    before = dict(job_store.get_job(task_id)["result"])
    job_store.save_result_changes(task_id, before, {
        **before, "transcript_text": EDITED, "raw_segments": EDITED_SEGMENTS, "display_segments": EDITED_SEGMENTS,
        "transcript_edited": True,
    })
    return before


def _carry_on(task_id):
    async def scenario():
        await task_actions.resume_task(task_id, None)
        assert await _until(lambda: _ended(task_id) and job_store.get_job(task_id)["result"].get(
            "summary_status") not in {"pending", "failed"})

    asyncio.run(scenario())
    output_folder._WORKER.submit(lambda: None).result(timeout=10)


def _assert_note_from_the_edit(task_id, model, before):
    job = job_store.get_job(task_id)
    result = job["result"]
    assert job["status"] == "completed", job.get("error_reason")
    assert result["transcript_text"] == EDITED, "the user's edit was replaced"
    assert result["raw_segments"] == EDITED_SEGMENTS
    assert result.get("transcript_edited") is True
    from backend.core import glossary_correction
    assert result.get(glossary_correction.FINGERPRINTS) == before.get(glossary_correction.FINGERPRINTS)
    assert "模型写的" in result["summary_markdown"]
    assert not result.get("summary_error")
    assert len(model) == 1 and "残差平方和" in model[0], "the note was not written from the edited transcript"
    assert _statuses(task_id)[task_steps.NOTE] == task_steps.DONE


def test_a_subtitle_files_transcript_edits_survive_carrying_its_note_on(task_id, tmp_path, model, monkeypatch):
    srt = _in_place_task(task_id, tmp_path, status="running")
    _first_run_the_model_fails(task_id, monkeypatch)
    before = _user_edits_the_transcript(task_id)
    # The file changed on disk since: its new words are not the note's.
    srt.write_text(SRT.replace("最小二乘法", "傅里叶变换"), encoding="utf-8")
    monkeypatch.setattr(transcript_note, "summarize_transcript_with_metadata",
                        lambda text, **_kw: (model.append(text), SimpleNamespace(
                            markdown="# 最小二乘法\n\n模型写的。", requested_mode="auto",
                            resolved_mode="direct", chunk_count=1))[1])

    _carry_on(task_id)

    _assert_note_from_the_edit(task_id, model, before)
    assert "傅里叶变换" not in model[0]


def test_a_links_captions_transcript_edits_survive_carrying_its_note_on(task_id, model, monkeypatch):
    from backend.core import link_submission

    def no_download(*_a, **_kw):
        raise AssertionError("the captions are on disk: nothing is downloaded again")

    monkeypatch.setattr(link_submission, "download_video_source", no_download)
    _captions_task(task_id, status="running")
    _first_run_the_model_fails(task_id, monkeypatch)
    before = _user_edits_the_transcript(task_id)
    monkeypatch.setattr(transcript_note, "summarize_transcript_with_metadata",
                        lambda text, **_kw: (model.append(text), SimpleNamespace(
                            markdown="# 最小二乘法\n\n模型写的。", requested_mode="auto",
                            resolved_mode="direct", chunk_count=1))[1])

    _carry_on(task_id)

    _assert_note_from_the_edit(task_id, model, before)


# ── carrying on after the note: what the note already is stays ─────────────
#
# Requirement (review of 5d6a580, 2026-11-26): a subtitle task carried on at
# its Feishu step only exports the note it holds: the model is not called and
# the note (which the user may have edited since) is not touched. A
# transcript edit saved while a carried-on note is written stays in the
# database and in the files. Stopping a carried-on note leaves the task as a
# recording stopped at its note: completed, its note step stopped.


def _feishu_failed_after_the_note(task_id, *, edited_note: str) -> None:
    """The note is done, the export to Feishu failed (an expired login); the
    user then edited the note."""
    result = {"transcript_text": "今天讲最小二乘法。先看误差平方和为什么能代表拟合得好不好。",
              "raw_segments": EDITED_SEGMENTS, "summary_markdown": edited_note, "summary_status": "completed",
              "display_title": "最小二乘法", "lark_error": "飞书登录过期"}
    job_store.upsert_job(task_id=task_id, status="completed", stage="done", progress=100,
                         summary_status="completed", result=result)
    task_steps.plan(task_id)
    for name in task_steps.ORDER:
        task_steps.finish(task_id, name, task_steps.SKIPPED)
    task_steps.finish(task_id, task_steps.FETCH, task_steps.DONE)
    task_steps.finish(task_id, task_steps.NOTE, task_steps.DONE)
    task_steps.finish(task_id, task_steps.FEISHU, task_steps.FAILED, "飞书登录过期")


def _exports_succeed(monkeypatch) -> list[str]:
    from backend.core import task_flow

    exports: list[str] = []

    def export(task_id_, client_id, job, options, source_filename, title):
        exports.append(job_store.get_job(task_id_)["result"]["summary_markdown"])
        before = dict(job_store.get_job(task_id_)["result"])
        job_store.save_result_changes(task_id_, before, {**before, "lark_response": {"url": "https://x"},
                                                         "lark_error": None})
        task_steps.settle(task_id_, task_steps.FEISHU, task_steps.feishu_outcome(job_store.get_job(task_id_)))

    monkeypatch.setattr(task_flow, "_export_agent_note", export)
    monkeypatch.setattr(task_flow.test_copy, "enabled", lambda: False)
    return exports


@pytest.mark.parametrize("kind", ["subtitle file by path", "link captions"])
def test_carrying_on_at_feishu_only_exports_the_note_it_holds(task_id, tmp_path, model, monkeypatch, kind):
    from backend.core import link_submission

    monkeypatch.setattr(link_submission, "download_video_source", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("nothing is downloaded again")))
    monkeypatch.setitem(_OPTIONS, "export_to_lark", "true")
    if kind == "subtitle file by path":
        _in_place_task(task_id, tmp_path, status="completed")
    else:
        _captions_task(task_id, status="completed")
    _feishu_failed_after_the_note(task_id, edited_note="# 笔记\n\n我自己改过的。")
    exports = _exports_succeed(monkeypatch)

    async def scenario():
        answer = await task_actions.resume_task(task_id, None)
        assert answer["resume_from"]["step"] == task_steps.FEISHU
        assert await _until(lambda: _ended(task_id) and exports)
        await asyncio.sleep(0.1)

    asyncio.run(scenario())
    output_folder._WORKER.submit(lambda: None).result(timeout=10)

    job = job_store.get_job(task_id)
    assert model == [], "the note's model was called again"
    assert exports == ["# 笔记\n\n我自己改过的。"]
    assert job["result"]["summary_markdown"] == "# 笔记\n\n我自己改过的。"
    assert job["status"] == "completed"
    assert _statuses(task_id)[task_steps.FEISHU] == task_steps.DONE
    assert _statuses(task_id)[task_steps.NOTE] == task_steps.DONE


def _note_held_by_the_model(monkeypatch):
    import threading

    in_model, answer = threading.Event(), threading.Event()

    def slow(text, **_kw):
        in_model.set()
        assert answer.wait(10)
        return SimpleNamespace(markdown="# 最小二乘法\n\n模型写的。", requested_mode="auto",
                               resolved_mode="direct", chunk_count=1)

    monkeypatch.setattr(transcript_note, "summarize_transcript_with_metadata", slow)
    return in_model, answer


def test_a_transcript_edit_saved_while_the_carried_on_note_is_written_stays_in_the_files(
        task_id, tmp_path, monkeypatch):
    from backend.core.result_artifacts import _artifact_storage_dir, _attach_result_artifacts

    srt = _in_place_task(task_id, tmp_path, status="running")
    _first_run_the_model_fails(task_id, monkeypatch)
    in_model, answer = _note_held_by_the_model(monkeypatch)

    async def scenario():
        await task_actions.resume_task(task_id, None)
        assert await _until(in_model.is_set)
        # What the editor's save does (``local_job_edit.update_job_transcript``).
        before = dict(job_store.get_job(task_id)["result"])
        edited = {**before, "transcript_text": EDITED, "raw_segments": EDITED_SEGMENTS,
                  "display_segments": EDITED_SEGMENTS, "transcript_edited": True}
        job_store.save_result_changes(task_id, before, _attach_result_artifacts(task_id, edited, parts=("transcript",)))
        answer.set()
        assert await _until(lambda: _ended(task_id) and "模型写的" in str(
            job_store.get_job(task_id)["result"].get("summary_markdown")))

    asyncio.run(scenario())
    output_folder._WORKER.submit(lambda: None).result(timeout=10)

    result = job_store.get_job(task_id)["result"]
    assert result["transcript_text"] == EDITED
    on_disk = (_artifact_storage_dir() / task_id / result["artifacts"]["transcript_txt"]["filename"]).read_text(
        encoding="utf-8")
    assert "残差平方和" in on_disk, "the transcript file went back to the text before the edit"
    process = srt.parent / "最小二乘法" / output_folder.PROCESS_DIRNAME
    copies = [path.read_text(encoding="utf-8") for path in process.glob("*.txt")]
    assert copies and all("残差平方和" in text for text in copies), copies


def test_stopping_a_carried_on_note_leaves_the_task_as_a_recording_stopped_at_its_note(
        task_id, tmp_path, monkeypatch):
    _in_place_task(task_id, tmp_path, status="running")
    _first_run_the_model_fails(task_id, monkeypatch)
    in_model, answer = _note_held_by_the_model(monkeypatch)

    async def scenario():
        await task_actions.resume_task(task_id, None)
        assert await _until(in_model.is_set)
        assert job_store.get_job(task_id)["status"] == "completed", "a task with its transcript reads completed"
        await task_actions.stop_step(task_id, None)
        assert await _until(lambda: _ended(task_id) and not task_queue.JOB_EVENTS.is_running(task_id))

    from backend.core import task_queue

    try:
        asyncio.run(scenario())
    finally:
        answer.set()
    job = job_store.get_job(task_id)
    assert job["status"] == "completed"
    assert _statuses(task_id)[task_steps.NOTE] == task_steps.STOPPED
    assert job["result"]["summary_error"] == task_steps.STOPPED_NOTE
    assert "误差平方和" in job["result"]["transcript_text"]
