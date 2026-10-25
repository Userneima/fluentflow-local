"""What can be done to a task, one function per operation.

The page's routes and the Agent API both call these and do nothing else but
turn a request into a call and a ``Refused`` into an answer (409, or 404 for a
task that is not there). An operation only one of them could reach was how
agents were left unable to change a cut (to-do items 7 and 15, 2026-10-18).

Each operation checks what it needs (the task exists, whether it is busy is
asked of ``task_activity``, whether the queue is working is asked of
``task_queue``) and refuses in the user's words, with a code an agent can act
on. Here so far: the processing report's three answers (turn a decision
around, agree with it, undo a change), cancelling a task, stopping the step it
is on, and carrying it on from the step that did not finish. Re-cutting,
rewriting the note and retrying are in ``task_reruns`` and ``task_submission``.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from backend.core import (
    debreath_job,
    local_intake_flow,
    original_locator,
    survey_cut,
    task_activity,
    task_flow,
    task_steps,
    task_stop,
    visual_note_job,
)
from backend.core.event_context import event_metadata
from backend.core.event_logger import log_event
from backend.core.job_store import (
    RESTART_INTERRUPTION_KEY,
    cancel_job_steps,
    get_job,
    reopen_job,
    update_job_metadata,
    upsert_job,
)
from backend.core.local_job_runtime import JOB_EVENTS
from backend.core.task_queue import _start_behind_queue, queue_is_busy

logger = logging.getLogger(__name__)

RunLater = Callable[..., Any]


class Refused(Exception):
    """An operation that cannot be done now: ``message`` in the user's words,
    ``code`` for an agent, ``status`` the HTTP answer (409 unless the task is
    not there)."""

    def __init__(self, message: str, code: str, *, status: int = 409) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}

    def as_http(self, *, agent: bool = False) -> Any:
        """The answer a route gives: the page reads the sentence, an agent
        the code and the sentence."""
        from fastapi import HTTPException

        return HTTPException(status_code=self.status, detail=self.as_dict() if agent else self.message)


def _job(task_id: str, client_id: Optional[str], missing: str = "Job not found") -> dict[str, Any]:
    job = get_job(task_id, client_id=client_id)
    if not job:
        raise Refused(missing, "not_found", status=404)
    return job


def _in_background(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="fluentflow-task-action", daemon=True).start()


# ── the processing report: turn around, agree, undo ─────────────────────────

# Re-renders taken for a task and not finished yet, report and note included.
_accepted: set[str] = set()
_accepted_lock = threading.Lock()


def _accept_rerender(task_id: str, client_id: Optional[str], run_later: RunLater,
                     work: Callable[[bool], Any]) -> dict[str, Any]:
    """Refuse a re-render now for the same reasons as any other render, else
    run ``work`` and then write the note again from the new cut."""
    job = _job(task_id, client_id)
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    if debreath_job.is_running(result):
        raise Refused("这个任务正在重新导出，等这次导完", "rerendering")
    current = task_activity.activity(job)
    if current.busy and task_steps.NOTE in current.open_steps:
        raise Refused("笔记正在写，等写完再改，免得两边的结果互相覆盖。", "note_running")
    if current.busy:
        raise Refused(f"这个任务{current.label or '还在处理'}，等它结束再改。", "busy")
    if queue_is_busy():
        raise Refused("有任务正在处理中，等队列空下来再改。", "queue_busy")
    with _accepted_lock:
        if task_id in _accepted:
            raise Refused("这个任务正在重新导出，等这次导完", "rerendering")
        try:
            original_locator.locate(task_id, client_id)
            debreath_job.claim(task_id)
        except (debreath_job.DebreathError, original_locator.OriginalMissing) as exc:
            raise Refused(str(exc), "cannot_render") from exc
        # Held until the work, the report and the note are done, not only the
        # render: a second click while the report is written would start from
        # decisions this one is about to replace.
        _accepted.add(task_id)

    def _run() -> None:
        try:
            try:
                work(True)
            except Exception:  # noqa: BLE001 - recorded on the survey
                logger.exception("re-render of %s failed", task_id)
                return
            latest = (get_job(task_id, client_id=client_id) or {}).get("result") or {}
            # The note was written from the earlier cut; it is written again from
            # this one so it matches.
            if not latest.get("note_declined") and (latest.get("summary_markdown") or local_intake_flow.note_is_wanted(task_id, client_id)):
                local_intake_flow.mark_note_running(task_id, client_id)
                local_intake_flow.write_note(task_id, client_id)
        finally:
            debreath_job.release(task_id)
            with _accepted_lock:
                _accepted.discard(task_id)

    run_later(_run)
    return {"ok": True, "task_id": task_id, "accepted": True}


def flip_cut_decision(task_id: str, ref: str, client_id: Optional[str] = None,
                      run_later: RunLater = _in_background) -> dict[str, Any]:
    """Turn one decision of the processing report around: what was cut is put
    back, what was kept is cut, and the cut is rendered again
    (``survey_cut.flip``). Answers once the work is accepted."""
    return _accept_rerender(task_id, client_id, run_later,
                            lambda claimed: survey_cut.flip(task_id, ref, client_id=client_id, claimed=claimed))


def undo_cut_change(task_id: str, index: int, client_id: Optional[str] = None,
                    run_later: RunLater = _in_background) -> dict[str, Any]:
    """Undo one of the user's earlier changes from the report's bin
    (``survey_cut.undo_change``); the cut is rendered again."""
    return _accept_rerender(task_id, client_id, run_later,
                            lambda claimed: survey_cut.undo_change(task_id, index, client_id=client_id, claimed=claimed))


def answer_cut_decisions(task_id: str, keys: list[str], verdict: str,
                         client_id: Optional[str] = None) -> dict[str, Any]:
    """Agree with items of the report as they are ("剪得对" / "确实该保留"),
    or take that back (``pending``). Nothing is rendered."""
    try:
        return {"ok": True, **survey_cut.verdict(task_id, keys, verdict, client_id=client_id)}
    except survey_cut.ReviewError as exc:
        raise Refused(str(exc), "cut_report") from exc


def cut_report(task_id: str, client_id: Optional[str] = None) -> dict[str, Any]:
    """The processing report as an agent reads it: every decision with the
    ``ref`` that turns it around and the ``key`` that agrees with it, the
    user's earlier changes (``index`` undoes one) and the answers so far."""
    job = _job(task_id, client_id, "任务不存在")
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    survey = debreath_job.debreath_state(result).get("survey") or {}
    report = survey.get("report") if isinstance(survey, dict) else None
    if not isinstance(report, dict):
        raise Refused("这个任务没有处理说明", "no_report")
    keep = ("ref", "key", "kind", "action", "text", "start", "end", "said", "unsure", "doubt")
    return {
        "ok": True,
        "task_id": task_id,
        "summary": report.get("summary"),
        "sections": [
            {"title": section.get("title"), "to_judge": bool(section.get("judge")),
             "items": [{k: item.get(k) for k in keep if item.get(k) is not None} for item in section.get("items") or []]}
            for section in report.get("sections") or []
        ],
        "user_changes": [
            {"index": index, **{k: change.get(k) for k in ("kind", "start", "end", "model_said", "user_said", "at", "undone_at")}}
            for index, change in enumerate(survey.get("user_changes") or [])
        ],
        "agreed": sorted((survey.get("verdicts") or {}).keys()),
        "rerendering": bool(survey.get("rerendering")),
    }


# ── cancel, stop, carry on ───────────────────────────────────────────────────

_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


async def cancel_task(task_id: str, client_id: Optional[str]) -> dict[str, Any]:
    """Cancel a task that has not finished transcribing (queued or running)."""
    job = _job(task_id, client_id)
    if job.get("status") in _TERMINAL_STATUSES:
        raise Refused(f"Job is already {job.get('status')}", "already_finished")

    cancelled_running_task = await JOB_EVENTS.cancel(task_id)
    cancelled_steps = cancel_job_steps(task_id)
    upsert_job(
        task_id=task_id,
        status="cancelled",
        client_id=job.get("client_id") or client_id,
        stage=job.get("stage") or "cancelled",
        progress=job.get("progress") or 0,
        source_type=job.get("source_type"),
        source_filename=job.get("source_filename"),
        source_file_size_mb=job.get("source_file_size_mb"),
        summary_status=job.get("summary_status"),
        error_reason="user_cancelled",
        metadata=job.get("metadata"),
    )
    await JOB_EVENTS.publish(
        task_id,
        {"stage": "error", "progress": job.get("progress") or 0, "error": "Task cancelled"},
    )
    log_event(
        task_id=task_id,
        event_name="task_cancelled",
        source_type=job.get("source_type"),
        source_filename=job.get("source_filename"),
        source_file_size_mb=job.get("source_file_size_mb"),
        stage=job.get("stage"),
        success=False,
        metadata=event_metadata(trigger="background_tasks"),
    )
    return {
        "ok": True,
        "task_id": task_id,
        "status": "cancelled",
        "cancelled_running_task": cancelled_running_task,
        "cancelled_steps": cancelled_steps,
        "queued_before_cancel": False,
    }


async def stop_step(task_id: str, client_id: Optional[str]) -> dict[str, Any]:
    """Stop the step the task is on: its transcription process, encode,
    Claude command line, or the request it is waiting on. The step is recorded
    as stopped, the task reads 已停下, and ``resume_task`` carries it on from
    there."""
    job = _job(task_id, client_id, "任务不存在")
    current = task_activity.activity(job)
    open_steps = [name for name, status in task_steps.statuses(task_steps.steps_of([task_id]).get(task_id, [])).items()
                  if status in task_steps.OPEN]
    if not open_steps and not JOB_EVENTS.is_running(task_id):
        if current.busy:
            raise Refused("这是单独发起的重新剪辑或重写笔记，不能停下，等它做完。", "rerun_not_stoppable")
        raise Refused("这个任务现在没有在做的步骤。", "not_working")
    step = current.current_step or (open_steps[0] if open_steps else None)
    killed = task_stop.request(task_id)
    cancelled = await JOB_EVENTS.cancel(task_id)
    # Whatever no runner closes (a task still waiting for its turn).
    task_steps.close_open(task_id, stopped=True)
    log_event(
        task_id=task_id,
        event_name="task_step_stopped",
        stage=step,
        success=True,
        metadata=event_metadata(step=step, stopped_processes=killed, cancelled_runner=cancelled),
    )
    return {
        "ok": True,
        "task_id": task_id,
        "stopped_step": None if step is None else {"step": step, "label": task_steps.LABELS.get(step, step)},
    }


RESTART_RESUME_LIMIT = 2
RESTART_RESUME_COUNT_KEY = "restart_resume_count"


async def resume_task(task_id: str, client_id: Optional[str]) -> dict[str, Any]:
    """Carry a task on from the first step that did not finish (stopped,
    failed, or cut off), in its place in the queue. The steps it already did
    are not done again; a cut whose decision was kept is only rendered again."""
    job = _job(task_id, client_id, "任务不存在")
    current = task_activity.activity(job)
    if current.busy or JOB_EVENTS.is_running(task_id):
        raise Refused(f"这个任务{current.label or '还在处理'}，等它停下或做完再接着做。", "busy")
    if task_id in debreath_job.running_task_ids() or visual_note_job.is_claimed(task_id):
        raise Refused("上次停下的那一步还在收尾，过几秒再接着做。", "still_stopping")
    steps = task_steps.steps_of([task_id]).get(task_id, [])
    if not steps:
        raise Refused("这个任务是在记录步骤之前处理的，没法从中断处接着做；可以用「重新处理」。", "no_steps")
    start_at = task_steps.resume_point(steps)
    if start_at is None:
        raise Refused("这个任务的每一步都已经做完了。", "nothing_to_resume")
    update_job_metadata(task_id, {RESTART_RESUME_COUNT_KEY: 0})
    await _carry_on(job, start_at, route="/jobs/{task_id}/resume")
    log_event(task_id=task_id, event_name="task_resumed", stage=start_at, success=True,
              metadata=event_metadata(trigger="user", resume_from=start_at))
    return {"ok": True, "task_id": task_id,
            "resume_from": {"step": start_at, "label": task_steps.LABELS.get(start_at, start_at)}}


async def _carry_on(job: dict[str, Any], start_at: str, *, route: str) -> None:
    """Queue the task from ``start_at``; refuses when what that step needs is
    gone."""
    from backend.core import link_submission, task_submission

    task_id = str(job.get("task_id") or "")
    client_id = job.get("client_id")
    before_transcript = start_at in task_steps.BEFORE_TRANSCRIPT
    if start_at == task_steps.FETCH and link_submission.link_of(job) and task_submission.find_source_file(task_id) is None:
        task_steps.reopen_from(task_id, start_at)
        reopen_job(task_id)
        try:
            started = await link_submission.resume_link_download(job)
        except FileNotFoundError as exc:
            raise Refused(str(exc), "source_missing") from exc
    else:
        try:
            ctx = task_submission.context_for_task(job, need_source=before_transcript or start_at == task_steps.CUT)
        except FileNotFoundError as exc:
            raise Refused(str(exc), "source_missing") from exc
        if before_transcript:
            reopen_job(task_id)
        ctx.event_route = route
        started = await _start_behind_queue(
            task_id=task_id, client_id=client_id, route=route, stage="processing", plan_steps=False,
            chained_worker=lambda previous, done: task_flow._run_serially(previous, done, ctx, start_at),
        )
    if not started:
        raise Refused("这个任务已经在处理了。", "busy")


# ── after a restart ──────────────────────────────────────────────────────────

def stepped_tasks_cut_off() -> list[str]:
    """Tasks a stopped service left with a step not at its end, apart from
    those waiting for the user's agent (``task_flow.resume_agent_cuts``)."""
    return [
        task_id for task_id in task_steps.interrupted()
        if not task_flow.agent_cut_unfinished(get_job(task_id))
    ]


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


async def resume_after_restart(task_ids: list[str]) -> int:
    """Carry each task a restart cut off on in place, from the step it was on
    (decision 6.42). A task restarts have cut off mid-step more than
    ``RESTART_RESUME_LIMIT`` times in a row is left for the user, as before:
    it may be what brings the service down. Returns how many were queued."""
    resumed = 0
    jobs = [job for job in (get_job(task_id) for task_id in task_ids) if job]
    for job in sorted(jobs, key=lambda each: str(each.get("created_at") or "")):
        task_id = str(job.get("task_id"))
        steps = task_steps.steps_of([task_id]).get(task_id, [])
        named = task_steps.statuses(steps)
        was_running = task_steps.RUNNING in named.values()
        metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
        count = int(metadata.get(RESTART_RESUME_COUNT_KEY) or 0) + (1 if was_running else 0)
        start_at = next((name for name in task_steps.ORDER if named.get(name) in task_steps.OPEN), None)
        reason = None
        if start_at is None:
            continue
        if count > RESTART_RESUME_LIMIT:
            reason = "restart_limit"
        else:
            update_job_metadata(task_id, {RESTART_RESUME_COUNT_KEY: count})
            try:
                await _carry_on(job, start_at, route="/startup/resume")
            except Refused as exc:
                reason = exc.message
            except Exception as exc:  # noqa: BLE001 - left for the user, not lost
                logger.exception("could not carry %s on after the restart", task_id)
                reason = f"{type(exc).__name__}: {exc}"
        _log_restart(job, start_at, count=count, left_because=reason)
        if reason is None:
            resumed += 1
            continue
        task_steps.close_open(task_id, error=task_steps.RESTART_ERROR)
        if str(job.get("status") or "") in {"queued", "running"}:
            # Told on the page, as a restart-interrupted task always was.
            upsert_job(
                task_id=task_id, status="failed", client_id=job.get("client_id"), stage="recovery", progress=0,
                error_reason="服务重启中断了这个任务：请重新提交，或对仍保留源文件的任务使用重试。",
                metadata={RESTART_INTERRUPTION_KEY: {
                    "interrupted_at": _now(), "started": was_running, "stage": job.get("stage"),
                    "progress": job.get("progress"),
                }, "queue_wait": None},
            )
    return resumed


def _log_restart(job: dict[str, Any], start_at: str | None, *, count: int, left_because: str | None) -> None:
    log_event(
        task_id=str(job.get("task_id") or ""),
        event_name="task_interrupted_by_restart",
        source_type=str(job.get("source_type") or "") or None,
        stage=start_at,
        success=left_because is None,
        error_reason=left_because,
        metadata=event_metadata(
            outcome="resumed" if left_because is None else "left_for_user",
            resumed_from_step=start_at,
            restart_count=count,
            # Carried on in place: the same task, not a new one.
            resumed_as=str(job.get("task_id") or "") if left_because is None else None,
        ),
    )


__all__ = [
    "Refused", "answer_cut_decisions", "cancel_task", "cut_report", "flip_cut_decision", "resume_after_restart",
    "resume_task", "stepped_tasks_cut_off", "stop_step", "undo_cut_change",
]
