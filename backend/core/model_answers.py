"""A step's model answers kept until the step is done, so a run that carries
the step on after an interruption reads them instead of paying again.

A step cut off halfway (the service stopped, the machine crashed, the user
pressed 停下这一步) used to lose every answer it had already paid for: the
corrections of the transcript asked all their batches again, a key-written note
wrote again the chapters it had (decision 6.41, 2026-10-26 measured). Every exit
that asks a model with text it can name (``ai_client``, ``claude_text``,
``claude_vision``, ``cut_judge``) goes through ``remembered``: the answer to a
request of a step that ``task_steps`` has open is stored under that task and
step, keyed by what was asked. When the same request is asked again for the
same step, the stored answer is given back and nothing is sent or recorded as
a call.

Answers are replayed in the order they were given: the n-th time a run asks a
request, it gets the n-th answer stored for it, and a model asked again after
an unreadable reply gets its second answer back, not the first one twice. Past
the stored ones, the model is asked.

Kept only while the step is open and dropped when it ends (``step_ended``):
done or skipped, it will not be asked again; failed for a reason of its own (an
answer that could not be used, a missing file), carrying it on must ask again
rather than replay the same failure. Kept when the step was stopped, cut off by
a restart, or failed because the model could not be reached (network, quota):
those answers were good, and carrying the step on after a fix uses them.

The subscription's command line (``claude_code_note``) is not covered: it is a
whole agent run, not one request and its answer, and the subscription does not
bill per call.

Never costs the task: an answer that cannot be stored or read is logged and
the model is asked as if nothing were kept.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Iterable

from backend.core import job_store, model_usage

logger = logging.getLogger(__name__)

_lock = threading.Lock()
# Steps a task's run has open (``task_steps.start`` .. ``finish``): only their
# answers are kept. A step's work asked for on request (cut again, rewrite the
# note) runs outside the steps and keeps nothing.
_open: set[tuple[str, str]] = set()
# How many times this run asked each request of each open step.
_asked: dict[tuple[str, str, str], int] = {}
# Steps whose last request raised (the model unreachable), not answered.
_unreachable: set[tuple[str, str]] = set()


def request_key(*parts: Any, images: Iterable[str | Path] = ()) -> str:
    """What was asked, as one key: the request's text parts and the content
    of its pictures (their paths change from run to run, their content does
    not)."""
    digest = hashlib.sha256()
    digest.update(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))
    for image in images:
        try:
            digest.update(Path(image).read_bytes())
        except OSError:
            digest.update(str(image).encode("utf-8"))
    return digest.hexdigest()


def step_started(task_id: str, step: str) -> None:
    with _lock:
        _open.add((task_id, step))
        _unreachable.discard((task_id, step))
        for key in [key for key in _asked if key[:2] == (task_id, step)]:
            del _asked[key]


def step_ended(task_id: str, step: str, status: str, *, keep: bool) -> None:
    """The step reached ``status``; ``keep`` says its answers may be read by
    a run that carries it on (stopped, cut off, the model unreachable)."""
    with _lock:
        _open.discard((task_id, step))
        unreachable = (task_id, step) in _unreachable
        _unreachable.discard((task_id, step))
    if keep or unreachable:
        return
    try:
        job_store.delete_model_answers(task_id, step)
    except Exception:  # noqa: BLE001 - leftover answers are dropped with the task
        logger.warning("could not drop the kept answers of %s %s", task_id, step, exc_info=True)


def forget_task(task_id: str) -> None:
    try:
        job_store.delete_model_answers(task_id)
    except Exception:  # noqa: BLE001
        logger.warning("could not drop the kept answers of %s", task_id, exc_info=True)


def remembered(key: str, ask: Callable[[], str]) -> str:
    """The answer to the request ``key``: the one kept for this step when a
    run before this one already had it, else ``ask()``'s, kept."""
    where = model_usage.current()
    task_id, step = where.task_id, where.step
    with _lock:
        open_step = bool(task_id and step and (task_id, step) in _open)
        if open_step:
            counted = (task_id, step, key)
            nth = _asked.get(counted, 0)
            _asked[counted] = nth + 1
    if not open_step:
        return ask()
    try:
        kept = job_store.list_model_answers(task_id, step, key)
    except Exception:  # noqa: BLE001 - asked as if nothing were kept
        logger.warning("could not read the kept answers of %s %s", task_id, step, exc_info=True)
        kept = []
    if nth < len(kept):
        logger.info("step %s of %s: answer %d to this request read from the earlier run", step, task_id, nth + 1)
        return kept[nth]
    try:
        answer = ask()
    except BaseException:
        with _lock:
            _unreachable.add((task_id, step))
        raise
    with _lock:
        _unreachable.discard((task_id, step))
    if isinstance(answer, str):
        try:
            job_store.insert_model_answer(task_id, step, key, answer)
        except Exception:  # noqa: BLE001 - the answer goes back all the same
            logger.warning("could not keep an answer of %s %s", task_id, step, exc_info=True)
    return answer


__all__ = ["forget_task", "remembered", "request_key", "step_ended", "step_started"]
