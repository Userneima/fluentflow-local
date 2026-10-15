"""The user's own past judgements on cuts, handed to the model as cases.

Requirement (owner, 2026-10-15). Feedback has to make the next judgement
better, so that fewer things need asking until nothing does. Every "剪得对" /
"确实该保留" and every change the user made on a processing report is a case:
the situation (what kind of stretch, how long, whether the picture moved, what
was said around it), what Claude decided and what the user said. The next cut
and the next report are written with the latest cases in front of the model,
and the model decides which of them resemble what it is looking at.

The cases are read from the tasks themselves, so taking an answer back or
undoing a change removes the case too. Only what the user clicked counts; an
item nobody answered is not a case. They never leave this machine.
"""

from __future__ import annotations

from typing import Any

from backend.core import job_store

MAX_CASES = 40

KIND_NAMES = {
    "long_gap": "长空白",
    "removed_talk": "有人说话的段落",
    "refused_removal": "模型想去掉、被程序拦下的段落",
    "quiet_speech": "说得很轻的话",
    "user_change": "用户改过的段落",
}


def _said(action: Any) -> str:
    return "剪掉" if action == "removed" else "保留"


def _situation(entry: dict[str, Any]) -> str:
    parts = [KIND_NAMES.get(str(entry.get("kind")), "一段")]
    try:
        seconds = float(entry.get("end") or 0) - float(entry.get("start") or 0)
        if seconds > 0:
            parts.append(f"{seconds:.0f} 秒" if seconds >= 1 else f"{seconds:.1f} 秒")
    except (TypeError, ValueError):
        pass
    if entry.get("picture"):
        parts.append(str(entry["picture"]))
    if entry.get("before"):
        parts.append(f"前一句「{entry['before']}」")
    if entry.get("after"):
        parts.append(f"后一句「{entry['after']}」")
    if entry.get("said"):
        parts.append(f"这段说的「{entry['said']}」")
    return "，".join(parts)


def collect(limit: int = MAX_CASES) -> list[dict[str, Any]]:
    """The latest cases across every task on this machine, newest first."""
    cases: list[dict[str, Any]] = []
    for job in job_store.list_jobs(limit=None) or []:
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        state = result.get("debreath") if isinstance(result.get("debreath"), dict) else {}
        survey = state.get("survey") if isinstance(state.get("survey"), dict) else {}
        for verdict in (survey.get("verdicts") or {}).values():
            if not isinstance(verdict, dict) or verdict.get("verdict") != "agree":
                continue
            cases.append({
                "situation": _situation(verdict), "model_said": verdict.get("model_said"),
                "user_said": verdict.get("model_said"), "model_reason": verdict.get("text") or "",
                "at": verdict.get("at") or "",
            })
        for change in survey.get("user_changes") or []:
            if not isinstance(change, dict) or change.get("undone_at"):
                continue
            cases.append({
                "situation": _situation(change), "model_said": change.get("model_said"),
                "user_said": change.get("user_said"), "model_reason": change.get("model_reason") or "",
                "at": change.get("at") or "",
            })
    cases.sort(key=lambda case: str(case["at"]), reverse=True)
    return cases[:limit]


def as_text(cases: list[dict[str, Any]]) -> str:
    """The cases as a block the model reads before the recording. Empty when
    there are none."""
    if not cases:
        return ""
    lines = []
    for case in cases:
        verdict = (
            f"用户确认{_said(case['user_said'])}得对" if case["user_said"] == case["model_said"]
            else f"用户改成了{_said(case['user_said'])}"
        )
        reason = f"（Claude 当时的理由：{case['model_reason']}）" if case["model_reason"] else ""
        lines.append(f"- {case['situation']}。Claude 当时{_said(case['model_said'])}{reason}，{verdict}。")
    return (
        "这位用户以前对剪辑的判断（最近的在前）。眼前的情况和其中某条相似时，照他的判断做；"
        "他确认过的同类情况你可以有把握，他改过的同类情况照他改的做：\n" + "\n".join(lines) + "\n\n"
    )


__all__ = ["collect", "as_text"]
