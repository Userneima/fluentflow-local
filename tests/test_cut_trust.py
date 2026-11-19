"""The cut report offers to stop asking about a judge the user keeps
agreeing with, and stops only when he says so.

Requirements (owner, 2026-11-19, decisions 2.18 and 2.19):
1. A judge whose places the user answered in at least 5 tasks, at least 30
   places, none of the latest 30 turned around, qualifies. A qualified judge
   is offered (这个 AI 的剪辑判断已多次确认，要关闭它的存疑提醒吗？) and still
   asked: its places keep waiting until the user closes its reminders.
2. Below either count there is no offer, and closing is refused.
3. After 关闭提醒 its places no longer wait, on the records list and in the report.
4. After 继续问我 (or reopening) the offer comes back only once 30 more
   places are agreed with.
5. A turnaround after closing takes the closing back; the offer comes back
   only once the judge qualifies again. An undone turnaround does not count.
6. A judge whose name does not say which model judged (the text model, an
   agent naming its session, FluentFlow's Claude channel without its model)
   is never offered; a judge is one exact model, so a record is not lent to
   another model.
7. Nothing is answered for the user: a closed place is no agreement and no case.
8. An agreement saved through the report counts at once.
"""

from __future__ import annotations

import uuid

from backend.core import cut_cases, cut_trust, job_store, survey_cut

import pytest


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


LATER = "2099-01-01T00:00:00+00:00"


def _qualified():
    judge = _judge()
    for _ in range(5):
        _task(judge, answered=6)
    return judge


def test_a_qualified_judge_is_offered_but_still_asked():
    judge = _qualified()
    new = _task(judge, asked=3)

    standing = cut_trust.standing(judge)
    assert standing["offer"] is True and standing["released"] is False
    assert standing["sentence"] == "这个 AI 的剪辑判断已多次确认，要关闭它的存疑提醒吗？"
    assert cut_trust.waiting(_waiting(new)) == 3
    assert _row_to_judge(new) == 3


def test_too_few_tasks_or_answers_get_no_offer_and_cannot_be_closed():
    few_tasks = _judge()
    for _ in range(4):
        _task(few_tasks, answered=10)
    few_answers = _judge()
    for _ in range(5):
        _task(few_answers, answered=5)

    for judge in (few_tasks, few_answers):
        assert cut_trust.standing(judge)["offer"] is False
        task = _task(judge, asked=2)
        with pytest.raises(cut_trust.ChoiceError):
            cut_trust.choose(job_store.get_job(task), "close")
        assert cut_trust.waiting(_waiting(task)) == 2


def test_closing_stops_the_asking():
    judge = _qualified()
    new = _task(judge, asked=3)

    after = cut_trust.choose(job_store.get_job(new), "close")

    assert after["released"] is True and after["offer"] is False
    assert after["sentence"].startswith("你已关闭这个 AI 的存疑提醒")
    assert cut_trust.waiting(_waiting(new)) == 0
    assert _row_to_judge(new) == 0


@pytest.mark.parametrize("choice", ["keep", "reopen"])
def test_keeping_or_reopening_waits_for_30_more_agreements(choice):
    judge = _qualified()
    new = _task(judge, asked=1)
    if choice == "reopen":
        cut_trust.choose(job_store.get_job(new), "close")

    cut_trust.choose(job_store.get_job(new), choice)
    assert cut_trust.standing(judge)["offer"] is False and cut_trust.waiting(_waiting(new)) == 1

    _task(judge, answered=29, at=LATER)
    assert cut_trust.standing(judge)["offer"] is False
    _task(judge, answered=1, at=LATER)
    assert cut_trust.standing(judge)["offer"] is True


def test_a_turnaround_after_closing_takes_it_back_and_an_undone_one_does_not():
    judge = _qualified()
    new = _task(judge, asked=1)
    cut_trust.choose(job_store.get_job(new), "close")

    _task(judge, changes=[{"kind": "removed_talk", "at": LATER, "undone_at": LATER}])
    assert cut_trust.standing(judge)["released"] is True

    _task(judge, changes=[{"kind": "removed_talk", "at": LATER}])
    standing = cut_trust.standing(judge)
    assert standing["released"] is False and standing["offer"] is False and standing["turned_recently"] is True
    assert standing["sentence"] == "你最近改过这个 AI 的判断，这次逐处请你看。"
    assert cut_trust.waiting(_waiting(new)) == 1

    _task(judge, answered=30, at="2099-01-02T00:00:00+00:00")
    assert cut_trust.standing(judge)["offer"] is True


def test_judges_that_do_not_name_a_model_are_never_offered():
    for judge in ("text_model", "agent:codex-current-session-" + uuid.uuid4().hex[:6], "agent", "claude_code_subscription"):
        assert cut_trust.judge_of(judge) is None
        for _ in range(5):
            _task(judge, answered=6)
        assert cut_trust.standing(judge)["offer"] is False
        with pytest.raises(cut_trust.ChoiceError):
            cut_trust.choose(job_store.get_job(_task(judge, asked=1)), "close")
    # FluentFlow's own Claude names its model, so changing it starts a new record.
    assert cut_trust.judge_of("claude_code_subscription:opus") == "claude_code_subscription:opus"
    assert cut_trust.judge_of("anthropic_api_key:claude-opus-5-5") == "anthropic_api_key:claude-opus-5-5"


def test_a_closed_place_is_not_answered_for_the_user():
    judge = _qualified()
    new = _task(judge, asked=2)
    cut_trust.choose(job_store.get_job(new), "close")

    cut_trust.waiting(_waiting(new))

    assert _waiting(new)["verdicts"] == {}
    assert not [case for case in cut_cases.collect(limit=1000) if "close-marker" in case["situation"]]


def test_an_agreement_saved_through_the_report_counts_at_once():
    judge = _judge()
    for _ in range(4):
        _task(judge, answered=7)
    last = _task(judge, asked=2)
    assert cut_trust.standing(judge)["offer"] is False

    survey_cut.verdict(last, ["k0", "k1"], "agree")

    assert cut_trust.standing(judge)["offer"] is True


def test_the_page_closes_and_reopens_through_the_report(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.core.local_request_scope import LOCAL_OWNER_ID
    from backend.routers.local_job_debreath import router

    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "t")
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    headers = {"x-fluentflow-client-id": "desktop-a", "x-fluentflow-access-token": "t"}
    judge = _qualified()
    task_id = f"ct-{uuid.uuid4().hex[:8]}"
    job_store.upsert_job(task_id=task_id, status="completed", client_id=LOCAL_OWNER_ID,
                         result={"debreath": {"survey": {"judged_by": judge, "report": _report(["k0"])}}})

    refused = client.post(f"/jobs/{task_id}/cut-report/reminders", json={"choice": "forget"}, headers=headers)
    closed = client.post(f"/jobs/{task_id}/cut-report/reminders", json={"choice": "close"}, headers=headers)
    reopened = client.post(f"/jobs/{task_id}/cut-report/reminders", json={"choice": "reopen"}, headers=headers)

    assert refused.status_code == 409
    assert closed.json()["standing"]["released"] is True
    assert reopened.json()["standing"] == {**reopened.json()["standing"], "released": False, "offer": False}
