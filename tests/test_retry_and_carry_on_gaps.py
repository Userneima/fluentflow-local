"""The gaps in submitting again and carrying on (10-18 review, items 16 and
18; owner 2026-11-06, docs/方案/2026-11-06-中断后接着做.md), in the user's
terms:

- A task is finished only when every step was done or not needed. A step the
  run never reached, with nothing before it failed or stopped, is not done:
  carrying the task on starts there.
- A task submitted for the user's agent to judge its cut is still judged by
  the agent after it is submitted again.
- A link task cut off while it downloaded is offered again (the link is
  fetched again), not told its file is gone.
- An agent carrying a note on in a new conversation, fetching without saying
  how the note is handed over, gets the parts it was being handed over in.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.core import agent_note, job_store, task_steps, task_submission
from backend.core.local_task_detail import LocalTaskDetailPolicy as POLICY

_counter = iter(range(10_000))


@pytest.fixture()
def task_id():
    found = f"gaps-{next(_counter)}"
    yield found
    job_store.delete_jobs([found])


def test_a_step_the_run_never_reached_is_not_counted_done(task_id):
    job_store.upsert_job(task_id=task_id, status="completed")
    task_steps.plan(task_id)
    for name in (task_steps.FETCH, task_steps.TRANSCRIBE, task_steps.CORRECT):
        task_steps.start(task_id, name)
        task_steps.finish(task_id, name, task_steps.DONE)

    task_steps.close_open(task_id)

    named = task_steps.statuses(task_steps.steps_of([task_id])[task_id])
    assert named[task_steps.CUT] == task_steps.FAILED
    assert task_steps.resume_point(task_steps.steps_of([task_id])[task_id]) == task_steps.CUT


def test_steps_after_a_failed_one_are_skipped_and_carrying_on_starts_at_the_failure(task_id):
    job_store.upsert_job(task_id=task_id, status="completed")
    task_steps.plan(task_id)
    task_steps.start(task_id, task_steps.FETCH)
    task_steps.finish(task_id, task_steps.FETCH, task_steps.FAILED, "下载失败")

    task_steps.close_open(task_id)

    named = task_steps.statuses(task_steps.steps_of([task_id])[task_id])
    assert named[task_steps.TRANSCRIBE] == task_steps.SKIPPED
    assert task_steps.resume_point(task_steps.steps_of([task_id])[task_id]) == task_steps.FETCH


def test_submitting_again_keeps_who_judges_the_cut(monkeypatch):
    job = {"task_id": "t-judge", "status": "failed", "source_type": "video_link",
           "metadata": {"cut_judge": "agent", "video_source_url": "https://example.com/v/1"}}
    monkeypatch.setattr(task_submission, "get_job", lambda *_a, **_k: job)
    monkeypatch.setattr(task_submission, "_in_place_origin", lambda _job: None)
    monkeypatch.setattr(task_submission, "find_source_file", lambda _task_id: None)
    seen = {}

    async def link_retry(**kwargs):
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(task_submission, "_retry_link_task", link_retry)
    asyncio.run(task_submission.retry_task("t-judge", client_id=None, local_caller=True))
    assert seen["carry"]["cut_judge"] == "agent"


def test_a_link_cut_off_while_downloading_is_offered_again(monkeypatch):
    import backend.core.local_task_detail as detail

    monkeypatch.setattr(detail, "find_source_file", lambda _task_id: None)
    job = {"task_id": "t-link", "status": "failed", "source_type": "video_link",
           "metadata": {"video_source_url": "https://example.com/v/1"}}
    assert POLICY.is_source_retryable(job)
    assert not POLICY.is_source_retryable({"task_id": "t-file", "status": "failed", "source_type": "video_file"})


def test_fetching_without_saying_keeps_the_parts(task_id, monkeypatch):
    class Stop(Exception):
        pass

    monkeypatch.setattr(agent_note, "_job", lambda *_a: {})

    def stop_here(*_a):
        raise Stop()

    monkeypatch.setattr(agent_note, "_eligible", stop_here)
    agent_note._set_small_parts(task_id, True)
    with pytest.raises(Stop):
        agent_note.material(task_id, keyframe_extractor=lambda *_a, **_k: None)
    assert agent_note._small_parts(task_id)
    with pytest.raises(Stop):
        agent_note.material(task_id, small_parts=False, keyframe_extractor=lambda *_a, **_k: None)
    assert not agent_note._small_parts(task_id)


def test_a_note_left_unfinished_is_written_again_when_carried_on(task_id):
    """(2026-11-06, measured) The note's body is saved before its check runs;
    a restart then found "a note" and recorded the step failed instead of
    finishing it. A note the user edited is never written over."""
    from backend.core import task_flow

    base = {"transcript_text": "讲了一些东西", "summary_markdown": "# 笔记\n\n正文", "note_deferred_to_visual_note": True}
    job_store.upsert_job(task_id=task_id, status="completed", result={**base, "summary_status": "running"})
    assert task_flow._note_left_unfinished(task_id, None)

    job_store.upsert_job(task_id=task_id, status="completed", result={
        **base, "summary_status": "failed", "summary_error": task_steps.STOPPED_NOTE})
    assert task_flow._note_left_unfinished(task_id, None)

    job_store.upsert_job(task_id=task_id, status="completed", result={
        **base, "summary_status": "running", "summary_edited": True})
    assert not task_flow._note_left_unfinished(task_id, None)

    job_store.upsert_job(task_id=task_id, status="completed", result={**base, "summary_status": "completed"})
    assert not task_flow._note_left_unfinished(task_id, None)
