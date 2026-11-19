"""Whether the user is still asked about a judge's cuts.

Requirement (owner, 2026-11-19, decision 2.18). The processing report asks
the user about what the judge was unsure of and what the rules always ask
(removed talk, long cuts). On this machine 158 such places were asked, 105
agreed with, none turned around. The user should stop being asked about a
judge whose decisions he has kept agreeing with, while a judge without that
record (another model, a weaker one, one that does not say which model it is)
is still asked.

So the record is kept per judge (``judged_by``, the exact name: an agent's
Claude Opus and FluentFlow's own Claude subscription are two judges):
- a judge is released once the user answered its places in at least
  ``RELEASE_TASKS`` tasks, at least ``RELEASE_ANSWERS`` places, and none of
  the latest ``RELEASE_ANSWERS`` answers turned a decision around;
- one turnaround among them takes the release back, and the user is asked
  again until enough clean answers gather;
- a judge whose name says no model (``text_model``, whatever text model is
  set; an agent that names its session, not its model) is never released.

Released places are not answered for the user: nothing is written, so they
never become cases (``cut_cases`` counts only what the user clicked). They
stop counting as waiting, and the report says why it did not ask. The record
is read from the tasks themselves, so it follows every answer, change and
undo without being stored twice.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

from backend.core import job_store

RELEASE_TASKS = 5
RELEASE_ANSWERS = 30

# Names that say which model judged. FluentFlow's own Claude channels name it
# by the channel; an agent and the vision model name it themselves.
_OWN_CLAUDE = {"claude_code_subscription", "anthropic_api_key"}
_MODEL = re.compile(r"claude|gpt|gemini|deepseek|kimi|moonshot|qwen|glm|mimo|doubao|llama|mistral|grok", re.I)

# The tally reads every answered task (about 0.3 s on the owner's 78 tasks), so
# it is kept until an answer, change or undo is saved (``forget``), and read
# again after a few minutes in case a task was deleted.
FRESH_SECONDS = 300
_lock = threading.Lock()
_cache: dict[str, Any] = {"db": None, "at": 0.0, "record": {}}


def judge_of(judged_by: Any) -> str | None:
    """The judge a record can be kept for, or None when the name does not say
    which model judged."""
    name = str(judged_by or "").strip()
    if name in _OWN_CLAUDE:
        return name
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
        _cache.update(db=None)


def _record() -> dict[str, dict[str, Any]]:
    db = str(job_store.DEFAULT_DB_PATH)
    with _lock:
        if _cache["db"] == db and time.monotonic() - _cache["at"] < FRESH_SECONDS:
            return _cache["record"]
    record = _tally(job_store.cut_surveys_with_answers())
    with _lock:
        _cache.update(db=db, at=time.monotonic(), record=record)
    return record


def standing(judged_by: Any) -> dict[str, Any]:
    """Where this judge stands with the user: released or not, on how many
    answers, and whether a recent turnaround is why it is asked again."""
    judge = judge_of(judged_by)
    entry = _record().get(judge) if judge else None
    answers = sorted((entry or {}).get("answers") or [])
    latest = answers[-RELEASE_ANSWERS:]
    turned = any(flipped for _, flipped in latest)
    return {
        "judge": judge,
        "released": bool(entry) and entry["tasks"] >= RELEASE_TASKS and len(latest) >= RELEASE_ANSWERS and not turned,
        "agreed": sum(1 for _, flipped in answers if not flipped),
        "tasks": (entry or {}).get("tasks", 0),
        "turned_recently": turned,
    }


def of_job(job: dict[str, Any]) -> dict[str, Any] | None:
    """``standing`` of the judge of this task's cut report; None without one."""
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    state = result.get("debreath") if isinstance(result.get("debreath"), dict) else {}
    survey = state.get("survey") if isinstance(state.get("survey"), dict) else {}
    if not survey.get("report"):
        return None
    found = standing(survey.get("judged_by"))
    sentence = released_sentence(found) if found["released"] else ASKED_AGAIN if found["turned_recently"] else None
    return {**found, "sentence": sentence}


def released_sentence(standing: dict[str, Any]) -> str:
    """Why the report did not ask, in the page's words."""
    return f"你确认过这个 AI 的 {standing.get('agreed', 0)} 处判断、没改过，这次没再问你，有不对的可以直接改。"


ASKED_AGAIN = "你最近改过这个 AI 的判断，这次逐处请你看。"


def waiting(survey: dict[str, Any]) -> int:
    """Places of this report still waiting for the user: none once its judge
    is released."""
    if not isinstance(survey, dict) or not survey.get("report"):
        return 0
    verdicts = survey.get("verdicts") if isinstance(survey.get("verdicts"), dict) else {}
    left = len([key for key in _judge_keys(survey) if key not in verdicts])
    return 0 if left and standing(survey.get("judged_by"))["released"] else left


__all__ = ["RELEASE_ANSWERS", "RELEASE_TASKS", "forget", "judge_of", "of_job", "released_sentence", "standing", "waiting"]
