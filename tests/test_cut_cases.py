"""The user's past judgements on cuts become cases for the next judgement.

Requirements (owner, 2026-10-15):
1. Every agreement ("剪得对" / "确实该保留") and every change the user made is a
   case, with its situation, Claude's decision and the user's, newest first.
2. A taken-back agreement or an undone change is no case; an unanswered item
   is none either.
3. The cases are put in front of the model that judges the next recording.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from backend.core import cut_cases, job_store, survey_cut
from backend.core.silence_cuts import CutPlan, TimeRange


def _task(survey):
    task_id = f"cc-{uuid.uuid4().hex[:8]}"
    job_store.upsert_job(task_id=task_id, status="completed", result={"debreath": {"survey": survey}})
    return task_id


def _mine(cases, marker):
    return [case for case in cases if marker in case["situation"]]


def test_agreements_and_changes_are_cases_and_withdrawn_ones_are_not():
    marker = uuid.uuid4().hex[:6]
    _task({"verdicts": {"k": {
        "verdict": "agree", "kind": "long_gap", "model_said": "removed", "start": 0, "end": 40,
        "text": "等人入会", "picture": "画面静止", "before": f"大家稍等{marker}", "at": "2026-10-15T01:00:00",
    }}})
    _task({"user_changes": [
        {"kind": "long_gap", "model_said": "removed", "user_said": "kept", "start": 10, "end": 30,
         "model_reason": "没人说话", "picture": "画面一直在变", "before": f"我演示一下{marker}", "at": "2026-10-15T02:00:00"},
        {"kind": "long_gap", "model_said": "removed", "user_said": "kept", "start": 50, "end": 60,
         "before": f"撤销过的{marker}", "at": "2026-10-15T03:00:00", "undone_at": "2026-10-15T04:00:00"},
    ]})

    cases = _mine(cut_cases.collect(limit=1000), marker)

    assert [case["situation"].split("前一句")[1][:5] for case in cases] == ["「我演示一", "「大家稍等"]
    text = cut_cases.as_text(cases)
    assert "长空白，20 秒，画面一直在变" in text and "Claude 当时剪掉（Claude 当时的理由：没人说话），用户改成了保留" in text
    assert "用户确认剪掉得对" in text
    assert "撤销过的" not in text


def test_without_cases_nothing_is_added():
    assert cut_cases.as_text([]) == ""


def test_the_cases_come_before_the_recording():
    seen = {}

    def model(system, user):
        seen["user"] = user
        return '{"keep_gaps": [], "remove": []}'

    cuts = [TimeRange(10.1, 13.9)]
    plan = CutPlan(source_duration_seconds=60.0, cuts=cuts, keeps=[TimeRange(0, 10.1), TimeRange(13.9, 60.0)],
                   silences_found=1, noise_db=-30, min_silence_seconds=0.25, padding_seconds=0.1)
    survey_cut.revise_plan(Path("x.mp4"), plan, [{"start": 0.5, "end": 9.5, "text": "开场"}], model,
                           pictures=lambda s, g: {}, cases="这位用户以前对剪辑的判断：\n- 案例\n\n")

    assert seen["user"].startswith("这位用户以前对剪辑的判断") and "录像总长 60 秒" in seen["user"]
