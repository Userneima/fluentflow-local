"""The one answer to "is this task still being worked on, and on what".

A local task reads ``completed`` as soon as its transcript is stored; the cut,
the note, the compressed copy and the Feishu export come after
(``task_steps``). Every caller that must not act on a task still at work asks
here: deleting a task (whose note step would otherwise write it back
afterwards), re-running a step on it, an agent waiting for it, the launcher's
restart guard (through ``/jobs``) and the task list.

A task waiting for the user's agent to judge its cut is not "busy": nothing on
this machine is working on it, a restart resumes it, and the agent waiting on
it would wait on itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.core import agent_cut, debreath_job, task_steps, visual_note_job

_LIVE_STATUSES = frozenset({"queued", "running", "processing"})


@dataclass(frozen=True)
class Activity:
    busy: bool
    # What the task is doing, in the user's words ("正在写笔记"), and the
    # step that is (``current_step``).
    label: str | None = None
    current_step: str | None = None
    # Open steps by name: ``note`` and ``cut`` are what agents ask about.
    open_steps: tuple[str, ...] = ()
    waiting_for_agent: bool = False
    failed_steps: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "busy": self.busy,
            "label": self.label,
            "current_step": self.current_step,
            "open_steps": list(self.open_steps),
            "waiting_for_agent": self.waiting_for_agent,
            "failed_steps": [
                {"step": name, "label": task_steps.LABELS.get(name, name)} for name in self.failed_steps
            ],
        }


def _step_name(step: dict[str, Any]) -> str:
    return str(step.get("step_type") or "").split(":", 1)[-1]


def activity(job: dict[str, Any] | None, steps: list[dict[str, Any]] | None = None) -> Activity:
    """``steps`` are the task's recorded steps when the caller already has
    them (a list reads them for every task at once); otherwise they are read."""
    job = job or {}
    task_id = str(job.get("task_id") or "")
    if steps is None:
        steps = task_steps.steps_of([task_id]).get(task_id, []) if task_id else []
    status = str(job.get("status") or "")
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    named = {_step_name(step): step for step in steps}
    open_steps = tuple(
        name for name in task_steps.ORDER if str((named.get(name) or {}).get("status")) in task_steps.OPEN
    )
    failed = tuple(
        name for name in task_steps.ORDER if str((named.get(name) or {}).get("status")) == task_steps.FAILED
    )
    if status in {"failed", "cancelled"}:
        return Activity(busy=False, failed_steps=failed)
    if agent_cut.is_waiting(result):
        return Activity(busy=False, label="等 agent 判断剪辑", open_steps=open_steps,
                        waiting_for_agent=True, failed_steps=failed)
    if status in _LIVE_STATUSES:
        label = "在排队" if status == "queued" else "正在处理"
        return Activity(busy=True, label=label, current_step=task_steps.TRANSCRIBE,
                        open_steps=open_steps or (task_steps.TRANSCRIBE,), failed_steps=failed)
    # Re-runs started on a finished task (a new cut, a rewritten note) keep
    # their state in the result, outside the steps.
    rerun: list[str] = []
    if debreath_job.is_running(result):
        rerun.append(task_steps.CUT)
    summary_status = str(result.get("summary_status") or job.get("summary_status") or "").lower()
    if visual_note_job.is_running(result) or summary_status == "pending":
        rerun.append(task_steps.NOTE)
    working = tuple(name for name in task_steps.ORDER if name in open_steps or name in rerun)
    if not working:
        return Activity(busy=False, failed_steps=failed)
    running = [name for name in working if str((named.get(name) or {}).get("status")) == task_steps.RUNNING
               or name in rerun]
    label_step = (running or list(working))[0]
    return Activity(busy=True, label="正在" + task_steps.LABELS[label_step], current_step=label_step,
                    open_steps=working,
                    failed_steps=tuple(name for name in failed if name not in working))


_DELETE_WORDS = {
    task_steps.TRANSCRIBE: "这个任务还在处理，等它结束再删。",
    task_steps.CUT: "这个任务还在剪辑或导出视频，等它结束再删。",
    task_steps.NOTE: "这个任务的笔记还在写，等写完再删。",
    task_steps.COMPRESS: "这个任务还在压缩副本，等它结束再删。",
    task_steps.FEISHU: "这个任务还在发飞书，等它结束再删。",
}


def busy_reason(job: dict[str, Any]) -> str | None:
    """What a task is still doing, in the user's words, or None."""
    current = activity(job)
    if not current.busy:
        return None
    for name in task_steps.ORDER:
        if name in current.open_steps:
            return _DELETE_WORDS[name]
    return _DELETE_WORDS[task_steps.TRANSCRIBE]


__all__ = ["Activity", "activity", "busy_reason"]
