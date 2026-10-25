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
stay "in progress" after its runner is gone; a restart closes what the previous
process left open (``recover_after_restart``).

Writes are best-effort: a step record that cannot be written is logged and the
task goes on. The task's results are the user's; the steps only describe them.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

from backend.core import job_store

logger = logging.getLogger(__name__)

TRANSCRIBE = "transcribe"
CUT = "cut"
NOTE = "note"
COMPRESS = "compress"
FEISHU = "feishu"
ORDER = (TRANSCRIBE, CUT, NOTE, COMPRESS, FEISHU)
LABELS = {
    TRANSCRIBE: "转录",
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


def _write(task_id: str, name: str, status: str, **kwargs: Any) -> None:
    try:
        job_store.write_task_step(task_id, name, status, order=ORDER.index(name), **kwargs)
    except Exception:  # noqa: BLE001 - the task goes on without its step record
        logger.warning("could not record step %s of %s as %s", name, task_id, status, exc_info=True)


def plan(task_id: str) -> None:
    """Every step of a task about to run, none started yet."""
    for name in ORDER:
        _write(task_id, name, PENDING, reset=True)


def start(task_id: str, name: str) -> None:
    _write(task_id, name, RUNNING, started=True)


def finish(task_id: str, name: str, status: str, error: str | None = None) -> None:
    _write(task_id, name, status, error=error or None, finished=True)


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


def recover_after_restart(keep: Callable[[str], bool] = lambda _task_id: False) -> int:
    """Close the steps a stopped service left open; ``keep`` names the tasks
    that go on by themselves (a task waiting for the user's agent)."""
    closed = 0
    try:
        task_ids = job_store.task_ids_with_open_steps()
    except Exception:  # noqa: BLE001 - nothing to close
        logger.warning("could not list open task steps", exc_info=True)
        return 0
    for task_id in task_ids:
        if keep(task_id):
            continue
        close_open(task_id, error=RESTART_ERROR)
        closed += 1
    return closed


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


__all__ = [
    "CUT", "COMPRESS", "DONE", "ENDS", "FAILED", "FEISHU", "LABELS", "NOTE", "OPEN", "ORDER",
    "PENDING", "RUNNING", "SKIPPED", "STOPPED", "TRANSCRIBE",
    "close_open", "compress_outcome", "cut_outcome", "feishu_outcome", "finish", "note_outcome",
    "plan", "recover_after_restart", "settle", "start", "steps_of", "transcribe_outcome",
]
