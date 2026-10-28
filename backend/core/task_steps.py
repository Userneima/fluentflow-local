"""Each task's steps after it is queued, one row per step (``job_steps``).

The job's own ``status`` turns ``completed`` as soon as the transcript is
stored, and that stays so: the transcript is readable from then on, and many
routes allow edits and re-runs on exactly that condition. Whether the task as a
whole is finished is a different question, and it is answered here, from the
steps: every step has reached an end (done, failed, skipped or stopped).
``task_activity`` reads this for every "is it still working" question.

A step is opened by the code that runs it and closed with what it produced
(``*_outcome`` read the result each step writes). The flow that runs a task
closes whatever is still open when it ends, however it ends, so no step can
stay "in progress" after its runner is gone. A restart carries a task on from
the first step that did not reach its end (``resume_point``, ``reopen_from``;
``task_flow.resume_after_restart``), and closes the steps of one it does not
carry on (``recover_after_restart``). A step the user stops ends ``stopped``,
and carrying the task on starts there.

Writes are best-effort: a step record that cannot be written is logged and the
task goes on. The task's results are the user's; the steps only describe them.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from backend.core import job_store, model_usage, task_stop

logger = logging.getLogger(__name__)

FETCH = "fetch"
TRANSCRIBE = "transcribe"
CORRECT = "correct"
CUT = "cut"
NOTE = "note"
COMPRESS = "compress"
FEISHU = "feishu"
ORDER = (FETCH, TRANSCRIBE, CORRECT, CUT, NOTE, COMPRESS, FEISHU)
# The steps the pipeline runs before the transcript is stored.
BEFORE_TRANSCRIPT = (FETCH, TRANSCRIBE, CORRECT)
LABELS = {
    FETCH: "取素材",
    TRANSCRIBE: "转录",
    CORRECT: "修正转录稿",
    CUT: "剪辑",
    NOTE: "写笔记",
    COMPRESS: "压缩副本",
    FEISHU: "发飞书",
}

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"
STOPPED = "stopped"
OPEN = frozenset({PENDING, RUNNING})
ENDS = frozenset({DONE, FAILED, SKIPPED, STOPPED})

RESTART_ERROR = "服务重启中断了这一步。"
UNFINISHED_ERROR = "这一步没有正常结束。"
# What a step the user stopped says until it is carried on.
STOPPED_NOTE = "已停下，点「接着做」从这一步开始。"


def _write(task_id: str, name: str, status: str, **kwargs: Any) -> None:
    if task_stop.shutting_down():
        # The service is stopping and stopped this step's work under it: the
        # step was cut off, not stopped or failed, and a restart carries it
        # on from here (``task_stop.shutdown``).
        logger.info("service stopping: step %s of %s left as it was (not %s)", name, task_id, status)
        return
    try:
        job_store.write_task_step(task_id, name, status, order=ORDER.index(name), **kwargs)
    except Exception:  # noqa: BLE001 - the task goes on without its step record
        logger.warning("could not record step %s of %s as %s", name, task_id, status, exc_info=True)


def plan(task_id: str) -> None:
    """Every step of a task about to run, none started yet."""
    for name in ORDER:
        _write(task_id, name, PENDING, reset=True)


def start(task_id: str, name: str) -> None:
    """The step is under way. The model calls made from here on, in this
    context and the work it hands to other threads, are this step's
    (``model_usage``)."""
    _write(task_id, name, RUNNING, started=True)
    model_usage.enter_step(task_id, name)


def finish(task_id: str, name: str, status: str, error: str | None = None) -> None:
    # A call of this step refused for the test budget fails the step, whatever
    # the step made of the refusal (many steps go on without their model).
    if model_usage.budget_refused(task_id, name) and status != STOPPED:
        status, error = FAILED, model_usage.BUDGET_EXCEEDED
    _write(task_id, name, status, error=error or None, finished=True)
    model_usage.leave_step(task_id, name)


def steps_of(task_ids: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    try:
        return job_store.list_task_steps(list(task_ids))
    except Exception:  # noqa: BLE001 - read as "no steps recorded"
        logger.warning("could not read task steps", exc_info=True)
        return {}


def close_open(task_id: str, *, stopped: bool = False, error: str = UNFINISHED_ERROR) -> None:
    """End every step still open once its runner is gone.

    A step that never started did not happen (skipped); one that was running
    did not finish (failed). When the task was stopped, both are stopped: they
    did not happen because the user stopped them.
    """
    for step in steps_of([task_id]).get(task_id, []):
        name = str(step.get("step_type") or "")[len(job_store.TASK_STEP_PREFIX):]
        if name not in ORDER or step.get("status") not in OPEN:
            continue
        if stopped:
            finish(task_id, name, STOPPED)
        elif step.get("status") == RUNNING:
            finish(task_id, name, FAILED, error)
        else:
            finish(task_id, name, SKIPPED)


def interrupted() -> list[str]:
    """Tasks a stopped service left with a step not yet at an end."""
    try:
        return job_store.task_ids_with_open_steps()
    except Exception:  # noqa: BLE001 - nothing to carry on
        logger.warning("could not list open task steps", exc_info=True)
        return []


def recover_after_restart(keep: Callable[[str], bool] = lambda _task_id: False) -> int:
    """Close the steps a stopped service left open, for the tasks not carried
    on (``keep`` names the ones that are)."""
    closed = 0
    for task_id in interrupted():
        if keep(task_id):
            continue
        close_open(task_id, error=RESTART_ERROR)
        closed += 1
    return closed


def statuses(steps: list[dict[str, Any]] | None) -> dict[str, str]:
    """Each recorded step's status by name."""
    found: dict[str, str] = {}
    for step in steps or []:
        name = str(step.get("step_type") or "")[len(job_store.TASK_STEP_PREFIX):]
        if name in ORDER:
            found[name] = str(step.get("status") or "")
    return found


def resume_point(steps: list[dict[str, Any]] | None) -> str | None:
    """The step a task carries on from: the first that did not end done or
    skipped (stopped, failed, or never reached its end). None when there is
    nothing to carry on: every step done or skipped, or no steps recorded."""
    named = statuses(steps)
    if not named:
        return None
    for name in ORDER:
        if named.get(name, PENDING) not in (DONE, SKIPPED):
            return name
    return None


def reopen_from(task_id: str, name: str, steps: list[dict[str, Any]] | None = None) -> None:
    """Make ``name`` and every later step not already done open again (not
    started), for a run that carries the task on from ``name``."""
    named = statuses(steps if steps is not None else steps_of([task_id]).get(task_id, []))
    for later in ORDER[ORDER.index(name):]:
        if named.get(later) != DONE:
            _write(task_id, later, PENDING, reset=True)


def status_of(task_id: str, name: str) -> str | None:
    return statuses(steps_of([task_id]).get(task_id, [])).get(name)


# ── what each step produced, read from the task's result ───────────────────

def _result(job: Any) -> dict[str, Any]:
    result = job.get("result") if isinstance(job, dict) else None
    return result if isinstance(result, dict) else {}


def transcribe_outcome(job: Any) -> tuple[str, str | None]:
    status = str((job or {}).get("status") or "")
    if status == "completed":
        return DONE, None
    if status == "cancelled":
        return STOPPED, None
    return FAILED, str((job or {}).get("error_reason") or "") or UNFINISHED_ERROR


def cut_outcome(job: Any) -> tuple[str, str | None]:
    state = _result(job).get("debreath")
    status = str(state.get("status") or "") if isinstance(state, dict) else ""
    if status == "completed":
        return DONE, None
    if status == "failed":
        return FAILED, str(state.get("error") or "") or UNFINISHED_ERROR
    if status == "running":
        return FAILED, UNFINISHED_ERROR
    return SKIPPED, None


def note_outcome(job: Any) -> tuple[str, str | None]:
    result = _result(job)
    status = str(result.get("summary_status") or (job or {}).get("summary_status") or "").lower()
    if str(result.get("summary_markdown") or "").strip() and status != "failed":
        return DONE, None
    if status == "failed":
        return FAILED, str(result.get("summary_error") or "") or UNFINISHED_ERROR
    if status in {"pending", "running"}:
        return FAILED, UNFINISHED_ERROR
    return SKIPPED, None


def compress_outcome(state: Any) -> tuple[str, str | None]:
    """``compress_original``'s record: made, declined (``compress_skipped``,
    e.g. a bitrate too low to gain anything) or failed (``compress_error``)."""
    if not isinstance(state, dict):
        return SKIPPED, None
    if state.get("compress_error"):
        return FAILED, str(state["compress_error"])
    if state.get("compressed") is False:
        return SKIPPED, None
    return DONE, None


def feishu_outcome(job: Any) -> tuple[str, str | None]:
    result = _result(job)
    if result.get("lark_response"):
        return DONE, None
    if result.get("lark_error"):
        return FAILED, str(result.get("lark_error"))
    return SKIPPED, None


def settle(task_id: str, name: str, outcome: tuple[str, str | None]) -> None:
    status, error = outcome
    finish(task_id, name, status, error)


# ── a step the user's agent does ─────────────────────────────────────────────
#
# The note an agent writes is not run by the flow: with the note left to the
# agent the flow records the note step skipped, and the agent writes it later
# (or while the cut renders). Its time is kept on the step's own record, from
# the agent first reading the material to its save, so the task page can say
# how long the note took. Only the task's first note is timed: an edit or
# rewrite days later is not part of processing the recording.

AGENT_STARTED = "agent_started_at"
AGENT_FINISHED = "agent_finished_at"


def _row(task_id: str, name: str) -> dict[str, Any] | None:
    for step in steps_of([task_id]).get(task_id, []):
        if str(step.get("step_type") or "") == job_store.TASK_STEP_PREFIX + name:
            return step
    return None


def agent_began(task_id: str, name: str) -> None:
    """The agent read the material for this step; its clock starts now,
    unless it already started or FluentFlow did the step itself."""
    row = _row(task_id, name)
    if row is None:
        return
    detail = row.get("result") if isinstance(row.get("result"), dict) else {}
    if detail.get(AGENT_STARTED) or row.get("status") == DONE:
        return
    _write(task_id, name, str(row.get("status") or PENDING),
           detail={**detail, AGENT_STARTED: job_store.now_iso()})


def agent_finished(task_id: str, name: str) -> None:
    """The agent saved this step's work: its clock stops, and a step the flow
    had ended without it (skipped, failed) reads done. A step still open is
    left for the flow to close."""
    row = _row(task_id, name)
    if row is None:
        return
    detail = row.get("result") if isinstance(row.get("result"), dict) else {}
    if not detail.get(AGENT_STARTED) or detail.get(AGENT_FINISHED):
        return
    status = str(row.get("status") or PENDING)
    _write(task_id, name, status if status in OPEN else DONE,
           detail={**detail, AGENT_FINISHED: job_store.now_iso()})


# ── how long each step took ──────────────────────────────────────────────────

def _moment(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None
    if moment is not None and moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round(max(0.0, (end - start).total_seconds()), 1)


def timings(job: Any, steps: list[dict[str, Any]] | None, *, now: datetime | None = None) -> dict[str, Any]:
    """How long the task spent on each step, for the task page and agents.

    One row per step that ran (a step that never ran or was skipped is left
    out), in the order the steps run, plus the wait in the queue before the
    first. The cut is split where its render began and ended: before it,
    deciding what to cut (the user's agent, when it judges); then finding the
    gaps and rendering; then writing the cut's report.
    A step still running counts up to ``now``. ``total_seconds`` is the whole
    run, from submission to the last step's end, so it is less than the rows
    added up when steps ran side by side (``overlapping``)."""
    now = now or datetime.now(timezone.utc)
    by_name: dict[str, dict[str, Any]] = {}
    for step in steps or []:
        name = str(step.get("step_type") or "")[len(job_store.TASK_STEP_PREFIX):]
        if name in ORDER:
            by_name[name] = step
    rows: list[dict[str, Any]] = []
    for name in ORDER:
        step = by_name.get(name)
        if step is None:
            continue
        detail = step.get("result") if isinstance(step.get("result"), dict) else {}
        status = str(step.get("status") or "")
        start, end = _moment(step.get("started_at")), _moment(step.get("finished_at"))
        by_agent = bool(detail.get(AGENT_STARTED))
        if by_agent:
            start, end = _moment(detail.get(AGENT_STARTED)), _moment(detail.get(AGENT_FINISHED))
            if end is None:
                status = RUNNING
        if start is None or status == SKIPPED:
            continue
        running = status == RUNNING and end is None
        row: dict[str, Any] = {
            "step": name,
            "label": LABELS[name],
            "status": status,
            "by_agent": by_agent,
            "started_at": start.isoformat(),
            "finished_at": end.isoformat() if end else None,
            "seconds": _seconds(start, now if running else end),
            "running": running,
            "parts": [],
        }
        if name == CUT:
            row["parts"] = _cut_parts(job, start, end, now if running else end)
        rows.append(row)
    created = _moment((job or {}).get("created_at"))
    first = min((_moment(row["started_at"]) for row in rows), default=None)
    waited = _seconds(created, first)
    if waited is not None and waited >= 1:
        rows.insert(0, {
            "step": "queue", "label": "排队", "status": DONE, "by_agent": False,
            "started_at": created.isoformat(), "finished_at": first.isoformat(),
            "seconds": waited, "running": False, "parts": [],
        })
    ends = [now if row["running"] else _moment(row["finished_at"]) for row in rows]
    last = max((end for end in ends if end is not None), default=None)
    begin = created or first
    overlapping = any(
        _moment(later["started_at"]) < earlier_end
        for index, later in enumerate(rows)
        for earlier_end in ends[:index]
        if earlier_end is not None and (earlier_end - _moment(later["started_at"])).total_seconds() >= 1
    )
    return {
        "rows": rows,
        "total_seconds": _seconds(begin, last) if rows else None,
        "running": any(row["running"] for row in rows),
        "overlapping": overlapping,
    }


def _cut_parts(job: Any, start: datetime, end: datetime | None, until: datetime | None) -> list[dict[str, Any]]:
    state = _result(job).get("debreath")
    if not isinstance(state, dict):
        return []
    render_start = _moment(state.get("started_at"))
    if render_start is None or render_start < start or (end is not None and render_start > end):
        return []
    render_end = _moment(state.get("finished_at"))
    parts = [
        {"label": "判断剪哪里", "seconds": _seconds(start, render_start)},
        {"label": "生成剪后视频", "seconds": _seconds(render_start, render_end or until)},
    ]
    # After the render the cut writes its report (``survey_cut._finish``).
    after = _seconds(render_end, end) if render_end is not None else None
    if after is not None and after >= 1:
        parts.append({"label": "整理处理报告", "seconds": after})
    return parts


__all__ = [
    "BEFORE_TRANSCRIPT", "CORRECT", "CUT", "COMPRESS", "DONE", "ENDS", "FAILED", "FEISHU", "FETCH", "LABELS",
    "NOTE", "OPEN", "ORDER", "PENDING", "RUNNING", "SKIPPED", "STOPPED", "TRANSCRIBE",
    "agent_began", "agent_finished", "close_open", "compress_outcome", "cut_outcome", "feishu_outcome", "finish", "interrupted", "note_outcome",
    "plan", "recover_after_restart", "reopen_from", "resume_point", "settle", "start", "status_of",
    "statuses", "steps_of", "timings", "transcribe_outcome",
]
