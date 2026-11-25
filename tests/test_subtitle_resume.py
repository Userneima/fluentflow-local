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
