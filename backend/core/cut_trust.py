"""Whether the user is still asked about a judge's cuts.

Requirement (owner, 2026-11-19, decisions 2.18 and 2.19). The processing
report asks the user about what the judge was unsure of and what the rules
always ask (removed talk, long cuts). On this machine 158 such places were
asked, 105 agreed with, none turned around. A judge whose decisions the user
keeps agreeing with may stop being asked about, but only when the user says
so; a judge without that record (another model, a weaker one, one that does
not say which model it is) is always asked.

So the record is kept per judge (``judged_by``, the exact name: an agent's
Claude Opus and FluentFlow's own Claude subscription are two judges):
- a judge qualifies once the user answered its places in at least
  ``RELEASE_TASKS`` tasks, at least ``RELEASE_ANSWERS`` places, and none of
  the latest ``RELEASE_ANSWERS`` answers turned a decision around;
- a qualified judge is offered: the report asks whether to close its
  reminders. Only the user's 关闭提醒 closes them; 继续问我 is not offered
  again until ``RELEASE_ANSWERS`` more places are agreed with;
- a turnaround after closing takes the closing back, and the user is asked
  place by place until the judge qualifies and is offered again; reopening
  counts as 继续问我;
- a judge whose name says no model (``text_model``, whatever text model is
  set; an agent that names its session, not its model) is never offered.

Closed places are not answered for the user: nothing is written to the task,
so they never become cases (``cut_cases`` counts only what the user clicked).
They stop counting as waiting, and the report says why it did not ask. The
answers are read from the tasks themselves; the user's choice per judge is
kept in the local config (``cut_judge_reminders``).
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

from backend.core import job_store, local_config

RELEASE_TASKS = 5
RELEASE_ANSWERS = 30

# Names that say which model judged. FluentFlow's own Claude channels add the
# model after the channel (``claude_code_subscription:opus``; before
# 2026-11-19 they named only the channel, and those records count for no
# judge); an agent and the vision model name it themselves.
_OWN_CLAUDE = {"claude_code_subscription", "anthropic_api_key"}
_MODEL = re.compile(r"claude|gpt|gemini|deepseek|kimi|moonshot|qwen|glm|mimo|doubao|llama|mistral|grok", re.I)

# The tally reads every answered task (about 0.3 s on the owner's 78 tasks), so
# it is kept until an answer, change or undo is saved (``forget``), and read
# again after a few minutes in case a task was deleted.
FRESH_SECONDS = 300
_lock = threading.Lock()
_cache: dict[str, Any] = {"db": None, "at": 0.0, "record": {}, "generation": 0}


def judge_of(judged_by: Any) -> str | None:
    """The judge a record can be kept for, or None when the name does not say
    which model judged."""
    name = str(judged_by or "").strip()
    channel, _, model = name.partition(":")
    if channel in _OWN_CLAUDE:
        return name if model.strip() else None
    for prefix in ("agent:", "vision:"):
        if name.startswith(prefix) and _MODEL.search(name[len(prefix):]):
            return name
    return None


def _judge_keys(survey: dict[str, Any]) -> set[str]:
    sections = (survey.get("report") or {}).get("sections") or []
    return {
        str(item.get("key")) for section in sections if isinstance(section, dict) and section.get("judge")
        for item in section.get("items") or [] if isinstance(item, dict) and item.get("key")
    }


def _answers(survey: dict[str, Any]) -> list[tuple[str, bool]]:
    """(when, turned around) for every place of this report the user answered."""
    asked = _judge_keys(survey)
    verdicts = survey.get("verdicts") if isinstance(survey.get("verdicts"), dict) else {}
    answers = [
        (str(verdict.get("at") or ""), False) for key, verdict in verdicts.items()
        if key in asked and isinstance(verdict, dict) and verdict.get("verdict") == "agree"
    ]
    answers += [
        (str(change.get("at") or ""), True) for change in survey.get("user_changes") or []
        if isinstance(change, dict) and not change.get("undone_at")
    ]
    return answers


def _tally(surveys: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    record: dict[str, dict[str, Any]] = {}
    for survey in surveys:
        judge = judge_of(survey.get("judged_by"))
        answers = _answers(survey)
        if judge is None or not answers:
            continue
        entry = record.setdefault(judge, {"tasks": 0, "answers": []})
        entry["tasks"] += 1
        entry["answers"].extend(answers)
    return record


def forget() -> None:
    """An answer, change or undo was saved: tally again on the next read."""
    with _lock:
        _cache.update(db=None, generation=_cache["generation"] + 1)


def _record() -> dict[str, dict[str, Any]]:
    db = str(job_store.DEFAULT_DB_PATH)
    with _lock:
        if _cache["db"] == db and time.monotonic() - _cache["at"] < FRESH_SECONDS:
            return _cache["record"]
        generation = _cache["generation"]
    record = _tally(job_store.cut_surveys_with_answers())
    with _lock:
        # An answer saved while this read ran made it stale: use it once,
        # keep nothing.
        if _cache["generation"] == generation:
            _cache.update(db=db, at=time.monotonic(), record=record)
    return record


CHOICES_KEY = "cut_judge_reminders"
OFFER = "这个 AI 的剪辑判断已多次确认，要关闭它的存疑提醒吗？"
ASKED_AGAIN = "你最近改过这个 AI 的判断，这次逐处请你看。"
CHOICES = {"close", "keep", "reopen"}


class ChoiceError(ValueError):
    """A choice about a judge's reminders that cannot be taken, in the user's words."""


def _choice(judge: str | None) -> dict[str, Any]:
    if not judge:
        return {}
    saved = local_config.load_config().get(CHOICES_KEY)
    found = saved.get(judge) if isinstance(saved, dict) else None
    return found if isinstance(found, dict) else {}


def standing(judged_by: Any) -> dict[str, Any]:
    """Where this judge stands with the user: whether its reminders are closed
    (``released``: its places are not asked), whether closing them is offered,
    on how many answers, and whether a recent turnaround is why it is asked."""
    judge = judge_of(judged_by)
    entry: dict[str, Any] = (_record().get(judge) if judge else None) or {"tasks": 0, "answers": []}
    answers = sorted(entry["answers"])
    latest = answers[-RELEASE_ANSWERS:]
    turned = any(flipped for _, flipped in latest)
    qualified = entry["tasks"] >= RELEASE_TASKS and len(latest) >= RELEASE_ANSWERS and not turned
    choice = _choice(judge)
    closed_at = str(choice.get("closed_at") or "")
    closed = bool(closed_at) and not any(flipped and at > closed_at for at, flipped in answers)
    kept_at = str(choice.get("kept_at") or "")
    agreed_since = sum(1 for at, flipped in answers if not flipped and at > kept_at)
    offered = qualified and not closed and (not kept_at or agreed_since >= RELEASE_ANSWERS)
    agreed = sum(1 for _, flipped in answers if not flipped)
    return {
        "judge": judge,
        "released": closed,
        "offer": offered,
        "agreed": agreed,
        "tasks": entry["tasks"],
        "turned_recently": turned,
        "sentence": closed_sentence(agreed) if closed else OFFER if offered else ASKED_AGAIN if turned else None,
    }


def closed_sentence(agreed: int) -> str:
    """Why the report did not ask, in the page's words."""
    return f"你已关闭这个 AI 的存疑提醒（你确认过它 {agreed} 处判断、没改过），这些地方默认照它的判断，有不对的可以直接改。"


def of_job(job: dict[str, Any]) -> dict[str, Any] | None:
    """``standing`` of the judge of this task's cut report; None without one."""
    survey = _survey_of(job)
    return standing(survey.get("judged_by")) if survey.get("report") else None


def _survey_of(job: dict[str, Any]) -> dict[str, Any]:
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    state = result.get("debreath") if isinstance(result.get("debreath"), dict) else {}
    survey = state.get("survey")
    return survey if isinstance(survey, dict) else {}


def choose(job: dict[str, Any], choice: str) -> dict[str, Any]:
    """The user's answer about the reminders of this task's judge: ``close``
    them (only when offered), ``keep`` asking, or ``reopen`` them. Returns the
    judge's standing after it."""
    if choice not in CHOICES:
        raise ChoiceError("只能关闭提醒、继续问我或重新打开提醒")
    survey = _survey_of(job)
    judge = judge_of(survey.get("judged_by"))
    if not survey.get("report") or judge is None:
        raise ChoiceError("看不出这条视频是哪个 AI 判断的，不能关它的提醒")
    now = _now()
    if choice == "close":
        if not standing(judge)["offer"]:
            raise ChoiceError("这个 AI 现在还不能关闭提醒：要先在 5 条视频里确认它 30 处判断、最近没改过")
        saved = {"closed_at": now}
    else:
        saved = {"kept_at": now}
    config = local_config.load_config()
    choices = config.get(CHOICES_KEY) if isinstance(config.get(CHOICES_KEY), dict) else {}
    local_config.save_section(CHOICES_KEY, {**choices, judge: saved})
    return standing(judge)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def waiting(survey: dict[str, Any]) -> int:
    """Places of this report still waiting for the user: none once the user
    closed its judge's reminders."""
    if not isinstance(survey, dict) or not survey.get("report"):
        return 0
    verdicts = survey.get("verdicts") if isinstance(survey.get("verdicts"), dict) else {}
    left = len([key for key in _judge_keys(survey) if key not in verdicts])
    return 0 if left and standing(survey.get("judged_by"))["released"] else left


__all__ = ["CHOICES_KEY", "ChoiceError", "OFFER", "RELEASE_ANSWERS", "RELEASE_TASKS", "choose", "closed_sentence", "forget", "judge_of", "of_job", "standing", "waiting"]
