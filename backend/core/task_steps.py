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

Every call that touches the store waits for the disk to commit: on the GitHub
Windows machine that was measured at a median 33 ms and up to 400 (decision
8.27). The flow that runs a task runs on the event loop that also answers the
page and agents, so it awaits the ``*_off_loop`` calls below: the same write on
a worker thread, with the loop free meanwhile (decision 8.30). They are awaited
one after another, so the calls of one task never race and a read after a write
sees it. ``start`` and ``finish`` keep the bookkeeping of which step a model
call belongs to on the calling thread, because that is a context variable of
that thread (``model_usage``).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Iterator

from backend.core import job_store, model_answers, model_usage, task_stop

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
# A step never started because the run ended early with nothing before it
# failed or stopped: it did not happen, and the task is not done without it.
NOT_REACHED_ERROR = "处理意外中断，这一步没做到。"
# What a step the user stopped says until it is carried on.
STOPPED_NOTE = "已停下，点「接着做」从这一步开始。"


def _write(task_id: str, name: str, status: str, **kwargs: Any) -> None:
    _write_steps(task_id, [name], status, **kwargs)


async def _off_loop(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """One of this module's calls on a worker thread (decision 8.30).

    A call already handed to the thread finishes before a cancellation goes
    on, so the code that closes a stopped or failed run's steps never races a
    write that is still on its way. Every cancellation is waited out, not just
    the first (a stop, then the service stopping): each one lands while the
    thread is still writing, and escaping on the second would put the close
    back beside it. Each such wait is one commit: the same wait this call
    would have made on the loop."""
    pending = asyncio.ensure_future(asyncio.to_thread(call, *args, **kwargs))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                continue
            except BaseException:  # noqa: BLE001 - the call's own failure is not the caller's
                break
        raise


def _write_steps(task_id: str, names: list[str], status: str, **kwargs: Any) -> None:
    """Record these steps in one commit: on Windows each commit was measured
    at tens of milliseconds, so the flow makes these calls on a worker thread
    (``_off_loop``)."""
    if not names:
        return
    if task_stop.shutting_down():
        # The service is stopping and stopped this step's work under it: the
        # step was cut off, not stopped or failed, and a restart carries it
        # on from here (``task_stop.shutdown``).
        logger.info("service stopping: steps %s of %s left as they were (not %s)", names, task_id, status)
        return
    try:
        job_store.write_task_steps(task_id, [(name, ORDER.index(name)) for name in names], status, **kwargs)
    except Exception:  # noqa: BLE001 - the task goes on without its step record
        logger.warning("could not record steps %s of %s as %s", names, task_id, status, exc_info=True)


def plan(task_id: str) -> None:
    """Every step of a task about to run, none started yet."""
    _write_steps(task_id, list(ORDER), PENDING, reset=True)


async def plan_off_loop(task_id: str) -> None:
    """``plan`` awaited from the event loop (decision 8.30)."""
    await _off_loop(plan, task_id)


def _step_began(task_id: str, name: str) -> None:
    """The bookkeeping of ``start``, on the thread that will make the calls:
    which step a model call belongs to is a context variable of that thread."""
    model_usage.enter_step(task_id, name)
    model_answers.step_started(task_id, name)


def start(task_id: str, name: str) -> None:
    """The step is under way. The model calls made from here on, in this
    context and the work it hands to other threads, are this step's
    (``model_usage``)."""
    _write(task_id, name, RUNNING, started=True)
    _step_began(task_id, name)


async def start_off_loop(task_id: str, name: str) -> None:
    """``start`` awaited from the event loop (decision 8.30)."""
    await _off_loop(_write, task_id, name, RUNNING, started=True)
    _step_began(task_id, name)


def _ending(task_id: str, name: str, status: str, error: str | None) -> tuple[str, str | None]:
    # A call of this step refused for the test budget fails the step, whatever
    # the step made of the refusal (many steps go on without their model).
    if model_usage.budget_refused(task_id, name) and status != STOPPED:
        return FAILED, model_usage.BUDGET_EXCEEDED
    return status, error


def _answers_ended(task_id: str, name: str, status: str, error: str | None) -> None:
    # The answers it paid for stay for a run that carries it on when it was
    # cut off rather than ended by itself (``model_answers``).
    model_answers.step_ended(task_id, name, status, keep=(
        status == STOPPED or task_stop.shutting_down() or error in (RESTART_ERROR, UNFINISHED_ERROR)
    ))


def finish(task_id: str, name: str, status: str, error: str | None = None) -> None:
    status, error = _ending(task_id, name, status, error)
    _write(task_id, name, status, error=error or None, finished=True)
    model_usage.leave_step(task_id, name)
    _answers_ended(task_id, name, status, error)


def _write_and_end_answers(task_id: str, name: str, status: str, error: str | None) -> None:
    _write(task_id, name, status, error=error or None, finished=True)
    _answers_ended(task_id, name, status, error)


async def finish_off_loop(task_id: str, name: str, status: str, error: str | None = None) -> None:
    """``finish`` awaited from the event loop (decision 8.30)."""
    status, error = _ending(task_id, name, status, error)
    await _off_loop(_write_and_end_answers, task_id, name, status, error)
    model_usage.leave_step(task_id, name)


def steps_of(task_ids: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    try:
        return job_store.list_task_steps(list(task_ids))
    except Exception:  # noqa: BLE001 - read as "no steps recorded"
        logger.warning("could not read task steps", exc_info=True)
        return {}


async def steps_of_off_loop(task_ids: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    """``steps_of`` awaited from the event loop (decision 8.30)."""
    return await _off_loop(steps_of, task_ids)


def _closings(
    steps: list[dict[str, Any]], *, stopped: bool, error: str,
) -> Iterator[tuple[str, str, str | None]]:
    """(name, status, error) for each step ``close_open`` ends, in the order
    they run.

    One that was running did not finish (failed). One that never started is
    skipped when a step before it failed: that failure is what carrying the
    task on starts from. With none before it failed, the run ended early for
    no reason a step recorded, and the step failed as not reached: skipped,
    the task read as finished without it (2026-11-06). When the task was
    stopped, every open step is stopped: they did not happen because the user
    stopped them.
    """
    ended_early = False
    for step in sorted(steps, key=lambda each: ORDER.index(
            str(each.get("step_type") or "")[len(job_store.TASK_STEP_PREFIX):])
            if str(each.get("step_type") or "")[len(job_store.TASK_STEP_PREFIX):] in ORDER else len(ORDER)):
        name = str(step.get("step_type") or "")[len(job_store.TASK_STEP_PREFIX):]
        if name not in ORDER:
            continue
        if step.get("status") in (FAILED, STOPPED):
            ended_early = True
        if step.get("status") not in OPEN:
            continue
        if stopped:
            yield name, STOPPED, None
        elif step.get("status") == RUNNING:
            yield name, FAILED, error
            ended_early = True
        elif ended_early:
            yield name, SKIPPED, None
        else:
            yield name, FAILED, NOT_REACHED_ERROR
            ended_early = True


def close_open(task_id: str, *, stopped: bool = False, error: str = UNFINISHED_ERROR) -> None:
    """End every step still open once its runner is gone (``_closings``)."""
    for name, status, why in _closings(steps_of([task_id]).get(task_id, []), stopped=stopped, error=error):
        finish(task_id, name, status, why)


async def close_open_off_loop(task_id: str, *, stopped: bool = False, error: str = UNFINISHED_ERROR) -> None:
    """``close_open`` awaited from the event loop (decision 8.30)."""
    steps = (await steps_of_off_loop([task_id])).get(task_id, [])
    for name, status, why in _closings(steps, stopped=stopped, error=error):
        await finish_off_loop(task_id, name, status, why)


def _to_skip(named: dict[str, str], names: Iterable[str]) -> list[str]:
    return [name for name in ORDER if name in set(names) and named.get(name) == PENDING]


def skip_pending(task_id: str, names: Iterable[str]) -> None:
    """These steps will not run for this task (a transcript file is not
    transcribed, corrected, cut or compressed): the ones not started are
    skipped now, in one commit, so a stop before the run ends does not read
    them as stopped and carry the task on from them."""
    named = statuses(steps_of([task_id]).get(task_id, []))
    _write_steps(task_id, _to_skip(named, names), SKIPPED, finished=True)


async def skip_pending_off_loop(task_id: str, names: Iterable[str]) -> None:
    """``skip_pending`` awaited from the event loop (decision 8.30)."""
    named = statuses((await steps_of_off_loop([task_id])).get(task_id, []))
    await _off_loop(_write_steps, task_id, _to_skip(named, names), SKIPPED, finished=True)


def _pending(steps: list[dict[str, Any]]) -> list[str]:
    return [name for name, status in statuses(steps).items() if status == PENDING]


def skip_open(task_id: str) -> None:
    """The run is over and the steps not started were not needed (a link that
    gave captions has nothing to transcribe or cut): skipped, unlike the ones
    ``close_open`` finds left over by a run that ended early."""
    for name in _pending(steps_of([task_id]).get(task_id, [])):
        finish(task_id, name, SKIPPED)  # one still running is not over


async def skip_open_off_loop(task_id: str) -> None:
    """``skip_open`` awaited from the event loop (decision 8.30)."""
    for name in _pending((await steps_of_off_loop([task_id])).get(task_id, [])):
        await finish_off_loop(task_id, name, SKIPPED)


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
    _write_steps(task_id, _to_reopen(named, name), PENDING, reset=True)


def _to_reopen(named: dict[str, str], name: str) -> list[str]:
    return [later for later in ORDER[ORDER.index(name):] if named.get(later) != DONE]


async def reopen_from_off_loop(
    task_id: str, name: str, steps: list[dict[str, Any]] | None = None,
) -> None:
    """``reopen_from`` awaited from the event loop (decision 8.30)."""
    if steps is None:
        steps = (await steps_of_off_loop([task_id])).get(task_id, [])
    await _off_loop(_write_steps, task_id, _to_reopen(statuses(steps), name), PENDING, reset=True)


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


async def settle_off_loop(task_id: str, name: str, outcome: tuple[str, str | None]) -> None:
    """``settle`` awaited from the event loop (decision 8.30)."""
    status, error = outcome
    await finish_off_loop(task_id, name, status, error)


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


def agent_began(task_id: str, name: str, *, left_to_agent: bool = False) -> None:
    """The agent read the material for this step; its clock starts now,
    unless it already started or FluentFlow did the step itself.
    ``left_to_agent``: the flow closed the step having left it to the agent
    (the transcript corrections on an agent submission), so its row reads
    done although the work is still the agent's."""
    row = _row(task_id, name)
    if row is None:
        return
    detail = row.get("result") if isinstance(row.get("result"), dict) else {}
    if detail.get(AGENT_STARTED) or (row.get("status") == DONE and not left_to_agent):
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


ALIGN_STARTED = "align_started_at"
ALIGN_FINISHED = "align_finished_at"


def agent_aligning(task_id: str, *, finished: bool = False) -> None:
    """The agent read (or saved) the material for matching the note's sections
    to playback times: kept on the note's row, shown as a row of its own.
    The first read and the first save count; aligning again later is an edit."""
    row = _row(task_id, NOTE)
    if row is None:
        return
    detail = row.get("result") if isinstance(row.get("result"), dict) else {}
    key = ALIGN_FINISHED if finished else ALIGN_STARTED
    if detail.get(key) or (finished and not detail.get(ALIGN_STARTED)):
        return
    _write(task_id, NOTE, str(row.get("status") or PENDING), detail={**detail, key: job_store.now_iso()})


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
            judged = _agent_cut_parts(job, start)
            if judged:
                begin, before = judged
                row["started_at"] = begin.isoformat()
                row["seconds"] = _seconds(begin, now if running else end)
                row["by_agent"] = True
                row["parts"] = before + [
                    {**part, "label": "按决定算剪辑"} if part["label"] == "判断剪哪里" else part
                    for part in row["parts"]
                ]
        rows.append(row)
        if name == NOTE and detail.get(ALIGN_STARTED):
            align_start, align_end = _moment(detail.get(ALIGN_STARTED)), _moment(detail.get(ALIGN_FINISHED))
            rows.append({
                "step": "align", "label": "对齐章节时间", "status": DONE if align_end else RUNNING,
                "by_agent": True, "started_at": align_start.isoformat(),
                "finished_at": align_end.isoformat() if align_end else None,
                "seconds": _seconds(align_start, align_end or now), "running": align_end is None, "parts": [],
            })
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


def _agent_cut_parts(job: Any, cut_start: datetime) -> tuple[datetime, list[dict[str, Any]]] | None:
    """When the user's agent judged the cut: the cut step's row starts once
    the agent decided, so what came before is added in front of it, from
    the cut's own record (2026-11-05: a lesson's 1 m 49 s of preparing the
    material and 88 s of judging were missing, and the 31 s after the
    decision read as the judging)."""
    state = _result(job).get("agent_cut")
    if not isinstance(state, dict):
        return None
    prepared, since = _moment(state.get("prepare_started")), _moment(state.get("since"))
    fetched, decided = _moment(state.get("material_at")), _moment(state.get("decided_at"))
    if since is None or decided is None or decided > cut_start:
        return None
    parts: list[dict[str, Any]] = []
    if prepared is not None and prepared <= since:
        parts.append({"label": "准备判断材料", "seconds": _seconds(prepared, since)})
    if fetched is not None and (fetched - since).total_seconds() >= 1:
        parts.append({"label": "等 agent 取材料", "seconds": _seconds(since, fetched)})
    parts.append({"label": "agent 判断剪哪里", "seconds": _seconds(fetched or since, decided)})
    return (prepared if prepared is not None and prepared <= since else since), parts


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
    "agent_aligning", "agent_began", "agent_finished", "close_open", "close_open_off_loop", "compress_outcome",
    "cut_outcome", "feishu_outcome", "finish", "finish_off_loop", "interrupted", "note_outcome",
    "plan", "plan_off_loop", "recover_after_restart", "reopen_from", "reopen_from_off_loop", "resume_point",
    "settle", "settle_off_loop", "skip_open", "skip_open_off_loop", "skip_pending", "skip_pending_off_loop",
    "start", "start_off_loop", "status_of", "statuses", "steps_of", "steps_of_off_loop", "timings",
    "transcribe_outcome",
]
