"""The cut report stops asking about a judge the user keeps agreeing with.

Requirements (owner, 2026-11-19, decision 2.18):
1. A judge whose places the user answered in at least 5 tasks, at least 30
   places, none of the latest 30 turned around, is released: its places no
   longer wait for the user, on the records list and in the report.
2. Below either count the user is still asked.
3. One turnaround among the latest 30 takes the release back; an undone
   turnaround does not count.
4. A judge whose name does not say which model judged (the text model, an
   agent naming its session, FluentFlow's Claude channel without its model)
   is never released; a judge is one exact model, so a record is not lent
   to another model.
5. Nothing is answered for the user: a released place is no agreement and no case.
6. An agreement saved through the report counts at once.
"""

from __future__ import annotations

import uuid

from backend.core import cut_cases, cut_trust, job_store, survey_cut


def _judge() -> str:
    return f"agent:claude-test-{uuid.uuid4().hex[:8]}"


def _report(keys):
    return {"sections": [{"title": "需要你判断", "judge": True, "items": [
        {"ref": key, "key": key, "kind": "removed_talk", "action": "removed", "text": "去掉试音", "start": 1, "end": 5}
        for key in keys
    ]}]}


def _task(judge, answered=0, asked=None, changes=None, at="2026-11-01T00:00:00"):
    keys = [f"k{i}" for i in range(asked if asked is not None else answered)]
    survey = {
        "judged_by": judge, "report": _report(keys),
        "verdicts": {key: {"verdict": "agree", "at": at} for key in keys[:answered]},
        "user_changes": changes or [],
    }
    task_id = f"ct-{uuid.uuid4().hex[:8]}"
    job_store.upsert_job(task_id=task_id, status="completed", result={"debreath": {"survey": survey}})
    cut_trust.forget()
    return task_id


def _waiting(task_id):
    return job_store.get_job(task_id)["result"]["debreath"]["survey"]


def _row_to_judge(task_id):
    row = next(job for job in job_store.list_jobs(limit=None, include_result=False) if job["task_id"] == task_id)
    return row["result"]["debreath"]["survey"]["to_judge"]


def test_a_judge_with_a_clean_record_is_not_asked_again():
    judge = _judge()
    for _ in range(5):
        _task(judge, answered=6)
    new = _task(judge, asked=3)

    assert cut_trust.standing(judge)["released"] is True
    assert cut_trust.waiting(_waiting(new)) == 0
    assert _row_to_judge(new) == 0


def test_too_few_tasks_or_answers_are_still_asked():
    few_tasks = _judge()
    for _ in range(4):
        _task(few_tasks, answered=10)
    few_answers = _judge()
    for _ in range(5):
        _task(few_answers, answered=5)

    for judge in (few_tasks, few_answers):
        assert cut_trust.standing(judge)["released"] is False
        assert cut_trust.waiting(_waiting(_task(judge, asked=2))) == 2


def test_a_recent_turnaround_takes_the_release_back_and_an_undone_one_does_not():
    judge = _judge()
    for _ in range(5):
        _task(judge, answered=6)
    _task(judge, changes=[{"kind": "removed_talk", "at": "2026-11-02T00:00:00", "undone_at": "2026-11-02T00:05:00"}])
    assert cut_trust.standing(judge)["released"] is True

    _task(judge, changes=[{"kind": "removed_talk", "at": "2026-11-02T00:00:00"}])
    standing = cut_trust.standing(judge)
    assert standing["released"] is False and standing["turned_recently"] is True
    assert cut_trust.waiting(_waiting(_task(judge, asked=1))) == 1


def test_judges_that_do_not_name_a_model_are_never_released():
    for judge in ("text_model", "agent:codex-current-session-" + uuid.uuid4().hex[:6], "agent", "claude_code_subscription"):
        assert cut_trust.judge_of(judge) is None
        assert cut_trust.standing(judge)["released"] is False
    # FluentFlow's own Claude names its model, so changing it starts a new record.
    assert cut_trust.judge_of("claude_code_subscription:opus") == "claude_code_subscription:opus"
    assert cut_trust.judge_of("anthropic_api_key:claude-opus-5-5") == "anthropic_api_key:claude-opus-5-5"
    assert cut_trust.judge_of("agent:gpt-6.1-sol") == "agent:gpt-6.1-sol"


def test_a_released_place_is_not_answered_for_the_user():
    judge = _judge()
    for _ in range(5):
        _task(judge, answered=6)
    new = _task(judge, asked=2)
    before = _waiting(new)

    cut_trust.waiting(before)

    assert _waiting(new)["verdicts"] == {}
    assert not [case for case in cut_cases.collect(limit=1000) if case["at"] == "2026-11-19T00:00:00"]


def test_an_agreement_saved_through_the_report_counts_at_once():
    judge = _judge()
    for _ in range(4):
        _task(judge, answered=7)
    last = _task(judge, asked=2)
    pending = _task(judge, asked=1)
    assert cut_trust.waiting(_waiting(pending)) == 1

    survey_cut.verdict(last, ["k0", "k1"], "agree")

    assert cut_trust.standing(judge)["released"] is True
    assert cut_trust.waiting(_waiting(pending)) == 0
