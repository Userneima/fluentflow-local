"""The user's own agent decides what to cut (owner, 2026-10-16/17).

A user who works with FluentFlow from their agent (Claude Code, Codex, ...)
may have no model key in FluentFlow at all; their agent's subscription can
make the same judgement FluentFlow's model makes. So after transcription the
task can stop and wait for the agent: it reads the same material the model
would read (``survey_cut.prepare_material``: the timeline, the long gaps,
screenshots of the ones whose picture moved), hands back a decision in the
model's own answer format, and FluentFlow cuts with it under the same rules
(``survey_cut.apply_answer``). Whoever judged writes the processing report:
the agent organises the facts the program lists (``cut_report``).

Who judges (``choice``, set when the agent submits):

- ``agent``: the agent, always;
- ``fluentflow``: FluentFlow's own judge (``cut_judge``), as from the page;
- ``auto`` (default for an agent's submission): the agent only when FluentFlow
  has no judge of its own at the moment of judging.

The task does not wait forever. Every agent conversation has its own MCP
process, which reports the tasks it is responsible for every half minute
(``beat``); once those reports stop the conversation is gone. A conversation
that stays open but has moved on is caught by two time limits: the material
not fetched within ``FETCH_LIMIT_SECONDS``, or no decision within
``decision_limit`` of fetching it. Either way, and when the user presses
"让 FluentFlow 自己判断" on the page, FluentFlow judges as it would have
(its model, or the loudness plan alone without one) and the report says why.
The limits are estimates, not measurements: every agent's actual time is
recorded (``decided_after_seconds``) so they can be set from history later.
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.core import cut_cases, cut_judge, cut_report, debreath_job, job_store, local_intake_flow, silence_cuts, survey_cut
from backend.core.silence_cuts import CutPlan, TimeRange
from backend.core.storage_paths import _artifact_storage_dir

logger = logging.getLogger(__name__)

KEY = "agent_cut"
CHOICES = ("auto", "agent", "fluentflow")

WAITING = "waiting"
DECIDED = "decided"
FALLBACK = "fallback"
DONE = "done"

HEARTBEAT_STALE_SECONDS = 120.0
FETCH_LIMIT_SECONDS = 600.0
# Per hour of recording, and never less than the minimum. An estimate: the
# model judged a 58-minute recording in 47 s (decision log 2.10); an agent
# makes several tool calls and opens each screenshot.
DECIDE_SECONDS_PER_HOUR = 900.0
DECIDE_MIN_SECONDS = 900.0
SHOTS_DIR = "agent_cut_shots"

_lock = threading.Lock()
# Last report from an agent's MCP process, per task, in this process only: a
# restart forgets them, and the next report (every half minute) restores them.
_beats: dict[str, float] = {}


class AgentCutError(RuntimeError):
    """Refused, in the agent's words."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _age(stamp: Any) -> float | None:
    try:
        moment = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - moment).total_seconds()


def state_of(result: Any) -> dict[str, Any]:
    state = result.get(KEY) if isinstance(result, dict) else None
    return state if isinstance(state, dict) else {}


def is_waiting(result: Any) -> bool:
    return state_of(result).get("status") == WAITING


def choice_of(job: dict[str, Any]) -> str | None:
    """What the agent asked for when it submitted; None for a task not
    submitted by an agent."""
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    value = metadata.get("cut_judge") or (metadata.get("folder_intake") or {}).get("cut_judge")
    return str(value) if value in CHOICES else None


def normalized_choice(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text not in CHOICES:
        raise AgentCutError(f"cut_judge 只能是 {' / '.join(CHOICES)}")
    return text


def should_wait(task_id: str, client_id: str | None) -> bool:
    """Whether this task stops after transcription for the agent to judge."""
    if not local_intake_flow.survey_first_enabled():
        return False
    job = job_store.get_job(task_id, client_id=client_id) or {}
    if str(job.get("status") or "") != "completed":
        return False
    choice = choice_of(job)
    if choice is None or choice == "fluentflow":
        return False
    if choice == "agent":
        return True
    try:
        return cut_judge.choose() is None
    except Exception as exc:  # noqa: BLE001 - no judge found is the same answer
        logger.warning("could not tell whether FluentFlow can judge %s: %s", task_id, exc)
        return True


def decision_limit(duration_seconds: float) -> float:
    return max(DECIDE_MIN_SECONDS, DECIDE_SECONDS_PER_HOUR * duration_seconds / 3600.0)


def _save(task_id: str, client_id: str | None, change) -> dict[str, Any]:
    """Change this task's agent-cut state under the lock; returns the new state."""
    with _lock:
        job = job_store.get_job(task_id, client_id=client_id)
        if not job:
            raise AgentCutError("任务不存在")
        before = copy.deepcopy(dict(job.get("result") or {}))
        result = copy.deepcopy(before)
        state = dict(state_of(result))
        change(state, result)
        result[KEY] = state
        job_store.save_result_changes(task_id, before, result, client_id=client_id)
        return state


# --- preparing the material (in the queue) --------------------------------

def _stored_plan(state: dict[str, Any]) -> CutPlan:
    duration = float(state["duration"])
    cuts = [TimeRange(float(a), float(b)) for a, b in state.get("plan_cuts") or []]
    return CutPlan(
        source_duration_seconds=duration, cuts=cuts,
        keeps=silence_cuts._keeps_between(duration, cuts, silence_cuts.MIN_KEEP_SECONDS),
        silences_found=len(cuts), noise_db=0.0, min_silence_seconds=0.0, padding_seconds=0.0,
    )


def _stored_material(state: dict[str, Any], plan: CutPlan) -> survey_cut.Material:
    gaps = list(plan.cuts)
    by_time = {(round(g.start, 3), round(g.end, 3)): index for index, g in enumerate(gaps)}
    ids: dict[int, int] = {}
    measured: dict[int, float | None] = {}
    for row in state.get("gaps") or []:
        index = by_time.get((round(float(row["start"]), 3), round(float(row["end"]), 3)))
        if index is None:
            continue
        ids[int(row["id"])] = index
    for index, share in (state.get("measured") or {}).items():
        measured[int(index)] = share
    return survey_cut.Material(str(state.get("timeline") or ""), gaps, ids, measured)


def prepare(task_id: str, client_id: str | None) -> bool:
    """Measure what the judge reads, store it on the task and start waiting.
    Blocking (a decode of the recording); runs in the queue. False when there
    is nothing to judge, and the task is cut as usual."""
    job = job_store.get_job(task_id, client_id=client_id) or {}
    result = dict(job.get("result") or {})
    segments = survey_cut._texted(result.get("raw_segments"))
    if not segments:
        return False
    try:
        source = debreath_job.resolve_source(task_id)
    except debreath_job.DebreathError:
        return False
    plan = debreath_job.volume_plan(source)
    spoken, _ = survey_cut.spoken_words(source, plan, survey_cut.speech_spans(result))
    plan, _ = survey_cut.protect_words(plan, spoken)
    folder = _artifact_storage_dir() / task_id / SHOTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    material = survey_cut.prepare_material(source, plan, segments, shots_folder=folder)

    def start(state: dict[str, Any], _result: dict[str, Any]) -> None:
        state.clear()
        state.update({
            "status": WAITING, "since": _now(), "choice": choice_of(job),
            "duration": plan.source_duration_seconds,
            "plan_cuts": [[c.start, c.end] for c in plan.cuts],
            "timeline": material.timeline,
            "gaps": [
                {"id": gap_id, "start": material.gaps[index].start, "end": material.gaps[index].end}
                for gap_id, index in sorted(material.ids.items())
            ],
            "measured": {str(index): share for index, share in material.measured.items()},
            "shots": [{"label": label, "path": str(path)} for label, path in material.images],
            "decision_limit_seconds": round(decision_limit(plan.source_duration_seconds)),
        })

    _save(task_id, client_id, start)
    return True


# --- what the agent reads and sends ------------------------------------------

AGENT_NOTE = """

截图不在这条消息里：下面 shots 列出每张截图在本机的路径和它属于哪段空白，打开你需要看的。"""

DECISION_HOW = (
    "Judge as the instructions say, then call save_agent_cut with keep_gaps and remove exactly as the "
    "JSON the instructions ask for, and model (which model you are). FluentFlow cuts with it under the "
    "same rules as its own judge: spoken words found in the gaps are never cut, and removals that "
    "together take more than a quarter of what was said are not applied but put to the user one by one. "
    "The answer to save_agent_cut lists what was done as numbered facts; organise them into the "
    "processing report and send it with save_agent_cut_report."
)


def material(task_id: str, client_id: str | None) -> dict[str, Any]:
    job = job_store.get_job(task_id, client_id=client_id)
    if not job:
        raise AgentCutError("任务不存在")
    state = state_of(job.get("result"))
    if state.get("status") != WAITING:
        raise AgentCutError(_not_waiting(state))

    def fetched(state: dict[str, Any], _result: dict[str, Any]) -> None:
        state.setdefault("material_at", _now())

    state = _save(task_id, client_id, fetched)
    shots = state.get("shots") or []
    return {
        "task_id": task_id,
        "instructions": survey_cut.SYSTEM + (survey_cut.SHOTS_NOTE + AGENT_NOTE if shots else ""),
        "cases": cut_cases.as_text(cut_cases.collect()),
        "timeline": state.get("timeline") or "",
        "shots": shots,
        "how_to_answer": DECISION_HOW,
        "decide_within_seconds": state.get("decision_limit_seconds"),
    }


def _not_waiting(state: dict[str, Any]) -> str:
    status = state.get("status")
    if status == DECIDED or status == DONE:
        return "这个任务的剪辑已经判断过了。"
    if status == FALLBACK:
        return f"这个任务已经改由 FluentFlow 自己判断：{state.get('fallback_reason') or ''}".strip()
    return "这个任务没有在等你判断剪辑（提交时 cut_judge 设为 agent，或 FluentFlow 没有判断模型时才会等）。"


def decide(task_id: str, client_id: str | None, answer: Any, model: str = "") -> dict[str, Any]:
    """Take the agent's decision. Applied at once to the plan the agent was
    shown, so a mistake comes back now, while the agent is there to fix it;
    the cut itself is rendered by the waiting task, in the queue."""
    if isinstance(answer, str):
        answer = survey_cut.parse_answer(answer)
    if not isinstance(answer, dict):
        raise AgentCutError("决定要是一个 JSON 对象：{\"keep_gaps\": [...], \"remove\": [...]}")
    job = job_store.get_job(task_id, client_id=client_id) or {}
    result = dict(job.get("result") or {})
    segments = survey_cut._texted(result.get("raw_segments"))
    state = state_of(result)
    if state.get("status") != WAITING:
        raise AgentCutError(_not_waiting(state))
    plan = _stored_plan(state)
    stuff = _stored_material(state, plan)
    unknown = [
        item.get("id") for item in answer.get("keep_gaps") or []
        if isinstance(item, dict) and _int(item.get("id")) not in stuff.ids
    ]
    if unknown:
        raise AgentCutError(f"keep_gaps 里的编号 {unknown} 不在时间线里，用时间线里「空白 #编号」的编号。")
    _, record = survey_cut.apply_answer(answer, plan, segments, stuff)
    record.update({"long_gaps": len(stuff.ids), "screenshots_sent": len(state.get("shots") or [])})
    judged_by = f"agent:{model.strip()}" if model.strip() else "agent"

    def decided(state: dict[str, Any], _result: dict[str, Any]) -> None:
        if state.get("status") != WAITING:
            raise AgentCutError(_not_waiting(state))
        since = _age(state.get("since"))
        state.update({
            "status": DECIDED, "decided_at": _now(), "judged_by": judged_by,
            "decision": record, "answer": answer,
            "decided_after_seconds": round(since) if since is not None else None,
        })

    _save(task_id, client_id, decided)
    facts = cut_report.facts({**record, "judged_by": judged_by}, segments)
    return {
        "task_id": task_id,
        "kept_gaps": len(record.get("kept_gaps") or []),
        "removed": len(record.get("removed") or []),
        "removal_refused": record.get("removal_refused"),
        "facts": cut_report.fact_lines(facts),
        "report_instructions": cut_report.SYSTEM,
        "next": (
            "FluentFlow is cutting with your decision now (it waits for its queue). Organise the facts "
            "above into the processing report as report_instructions say and call save_agent_cut_report "
            "with that JSON. Its answer says how many places the user is asked to judge (to_judge, "
            "including the ones FluentFlow always asks about). Then tell the user the cut is done and that "
            "number, and point them to the task's page in FluentFlow to judge them there; do not ask them "
            "in chat, they need the video to judge."
        ),
    }


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def save_report(task_id: str, client_id: str | None, answer: Any) -> dict[str, Any]:
    """The agent's organisation of the report, kept by item key: the report
    is written from it now if the cut is done, or when it is."""
    if isinstance(answer, str):
        answer = survey_cut.parse_answer(answer)
    if not isinstance(answer, dict) or not isinstance(answer.get("sections"), list):
        raise AgentCutError("说明要是一个 JSON 对象：{\"summary\": \"…\", \"sections\": [...]}")
    job = job_store.get_job(task_id, client_id=client_id) or {}
    result = dict(job.get("result") or {})
    state = state_of(result)
    if state.get("status") not in (DECIDED, DONE):
        raise AgentCutError("先用 save_agent_cut 交回剪辑决定，再交处理说明。")
    segments = survey_cut._texted(result.get("raw_segments"))
    facts = cut_report.facts({**state.get("decision", {}), "judged_by": state.get("judged_by")}, segments)
    keyed = cut_report.keyed(answer, facts)

    def saved(state: dict[str, Any], result: dict[str, Any]) -> None:
        state["report"] = keyed
        debreath = result.get("debreath") if isinstance(result.get("debreath"), dict) else None
        survey = (debreath or {}).get("survey")
        if isinstance(survey, dict) and survey.get("report") and survey_cut.judged_by_agent(survey):
            survey = {**survey, "agent_report": keyed}
            source = survey_cut._texted(result.get("source_raw_segments") or result.get("raw_segments"))
            report = survey_cut.build_report(survey, source, None)
            report["after_seconds"] = survey["report"].get("after_seconds")
            report["before_seconds"] = survey["report"].get("before_seconds")
            survey["report"] = report
            result["debreath"] = {**debreath, "survey": survey}

    _save(task_id, client_id, saved)
    # What the page will ask the user: the agent's doubts and what the rules
    # always ask (removed talk, long cuts). The agent tells the user this
    # number; it cannot count the rules' items itself.
    shown = cut_report.from_keyed(facts, keyed, str(state.get("judged_by") or "agent"))
    judge = next((section["items"] for section in shown["sections"] if section.get("judge")), [])
    return {
        "task_id": task_id, "saved": True, "to_judge": len(judge),
        "to_judge_items": [f"{item.get('text')}（{item.get('doubt')}）" for item in judge],
    }


# --- who is still there ------------------------------------------------------

def beat(task_ids: list[str], client_id: str | None) -> list[str]:
    """An agent's MCP process says it is still here for these tasks. Returns
    the ones it should keep reporting: tasks waiting for the agent, and tasks
    with a step still to come. A task reads completed once its transcript is
    stored, while the material is still being measured: dropping it then
    stopped the reports before the waiting began, and the waiting task was
    given away as closed two minutes later (2026-10-28)."""
    from backend.core import task_activity  # it reads this module's state

    now = time.time()
    keep = []
    for task_id in task_ids[:50]:
        job = job_store.get_job(str(task_id), client_id=client_id)
        if not job:
            continue
        _beats[str(task_id)] = now
        current = task_activity.activity(job)
        if current.busy or current.waiting_for_agent:
            keep.append(str(task_id))
    return keep


def overdue(task_id: str, state: dict[str, Any]) -> str | None:
    """Why the agent is no longer expected, or None while it still is."""
    last = _beats.get(task_id)
    if last is not None and time.time() - last > HEARTBEAT_STALE_SECONDS:
        return "agent 的对话已经关掉了"
    fetched = _age(state.get("material_at"))
    if fetched is None:
        waited = _age(state.get("since"))
        if waited is not None and waited > FETCH_LIMIT_SECONDS:
            return f"agent {round(FETCH_LIMIT_SECONDS / 60)} 分钟内没来取判断材料"
        return None
    limit = float(state.get("decision_limit_seconds") or DECIDE_MIN_SECONDS)
    if fetched > limit:
        return f"agent 取了材料，{round(limit / 60)} 分钟内没交回决定"
    return None


def give_up(task_id: str, client_id: str | None, reason: str) -> bool:
    """Stop waiting: FluentFlow judges instead. False when it was no longer
    waiting (the agent's decision came first)."""
    def fall_back(state: dict[str, Any], _result: dict[str, Any]) -> None:
        if state.get("status") != WAITING:
            raise AgentCutError(_not_waiting(state))
        state.update({"status": FALLBACK, "fallback_reason": reason, "fallback_at": _now()})

    try:
        _save(task_id, client_id, fall_back)
    except AgentCutError:
        return False
    return True


def check(task_id: str, client_id: str | None) -> dict[str, Any] | None:
    """The state once waiting is over (decided, or given up because the agent
    is overdue), or None while it goes on."""
    job = job_store.get_job(task_id, client_id=client_id)
    if not job:
        return {"status": FALLBACK, "fallback_reason": "任务不存在"}
    state = state_of(job.get("result"))
    if state.get("status") in (DECIDED, FALLBACK):
        return state
    if state.get("status") != WAITING:
        return {"status": FALLBACK, "fallback_reason": "没有在等 agent"}
    reason = overdue(task_id, state)
    if reason and give_up(task_id, client_id, reason):
        return state_of((job_store.get_job(task_id, client_id=client_id) or {}).get("result"))
    return None


def decider(state: dict[str, Any]) -> survey_cut.Decide:
    """The agent's decision, applied to the plan the render starts from, by
    time, as a re-render applies the user's (``survey_cut._rerender``)."""
    decision = dict(state.get("decision") or {})

    def apply(_media: Path, plan: CutPlan, _segments: list[dict[str, Any]]) -> tuple[CutPlan, dict[str, Any]]:
        revised = survey_cut.apply_decisions(plan, decision.get("kept_gaps") or [], decision.get("removed") or [])
        return revised, {**decision, "removed_seconds": revised.removed_seconds}

    return apply


def finished(task_id: str, client_id: str | None) -> None:
    def done(state: dict[str, Any], _result: dict[str, Any]) -> None:
        if state.get("status") == DECIDED:
            state["status"] = DONE
        state["finished_at"] = _now()
        state.pop("timeline", None)

    try:
        _save(task_id, client_id, done)
    except AgentCutError:
        pass


def summary(result: Any) -> dict[str, Any] | None:
    """What the task page and ``get_task`` say about it."""
    state = state_of(result)
    if not state:
        return None
    out = {key: state.get(key) for key in ("status", "since", "material_at", "decided_at", "judged_by",
                                           "fallback_reason", "decision_limit_seconds") if state.get(key) is not None}
    out["report_sent"] = bool(state.get("report"))
    return out


__all__ = [
    "AgentCutError", "CHOICES", "beat", "check", "decide", "decider", "finished", "give_up", "is_waiting",
    "material", "normalized_choice", "prepare", "save_report", "should_wait", "summary",
]
