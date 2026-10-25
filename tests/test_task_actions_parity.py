"""The page and an agent get the same answer from every task operation.

Requirement (owner, 2026-10-25, docs/方案/2026-10-25-让agent改得对的架构整理.md,
item 2): one table of task states, operations and expected outcomes runs
through the page's routes and through the Agent API; both give the same
status and the same sentence (the agent also gets a code to act on).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import job_store, task_steps
from backend.core.local_request_scope import LOCAL_OWNER_ID
from backend.routers.local_agent import router as agent_router
from backend.routers.local_job_debreath import router as debreath_router
from backend.routers.local_job_mutation import router as mutation_router

_TOKEN = "parity-token"
_HEADERS = {"x-fluentflow-client-id": "desktop-a", "x-fluentflow-access-token": _TOKEN}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", _TOKEN)
    app = FastAPI()
    app.include_router(agent_router)
    app.include_router(debreath_router)
    app.include_router(mutation_router)
    return TestClient(app)


def _finished(task_id):
    job_store.upsert_job(task_id=task_id, status="completed", client_id=LOCAL_OWNER_ID, result={"transcript_text": "x"})
    task_steps.plan(task_id)
    for name in task_steps.ORDER:
        task_steps.finish(task_id, name, task_steps.DONE)


def _old(task_id):
    job_store.upsert_job(task_id=task_id, status="completed", client_id=LOCAL_OWNER_ID, result={"transcript_text": "x"})


def _writing_note(task_id):
    job_store.upsert_job(task_id=task_id, status="completed", client_id=LOCAL_OWNER_ID, result={"transcript_text": "x"})
    task_steps.plan(task_id)
    for name in (*task_steps.BEFORE_TRANSCRIPT, task_steps.CUT):
        task_steps.finish(task_id, name, task_steps.DONE)
    task_steps.start(task_id, task_steps.NOTE)


def _missing(task_id):
    return None


# state, page request, agent request, status, code
CASES = [
    (_finished, ("POST", "/jobs/{id}/stop", None), ("POST", "/agent/v1/tasks/{id}/stop", None), 409, "not_working"),
    (_finished, ("POST", "/jobs/{id}/resume", None), ("POST", "/agent/v1/tasks/{id}/resume", None), 409, "nothing_to_resume"),
    (_old, ("POST", "/jobs/{id}/resume", None), ("POST", "/agent/v1/tasks/{id}/resume", None), 409, "no_steps"),
    (_writing_note, ("POST", "/jobs/{id}/resume", None), ("POST", "/agent/v1/tasks/{id}/resume", None), 409, "busy"),
    (_finished, ("POST", "/jobs/{id}/cancel", None), ("POST", "/agent/v1/tasks/{id}/cancel", None), 409, "already_finished"),
    (_missing, ("POST", "/jobs/{id}/cancel", None), ("POST", "/agent/v1/tasks/{id}/cancel", None), 404, "not_found"),
    (_missing, ("POST", "/jobs/{id}/stop", None), ("POST", "/agent/v1/tasks/{id}/stop", None), 404, "not_found"),
    (_finished, ("POST", "/jobs/{id}/cut-report/verdict", {"keys": ["k"], "verdict": "agree"}),
     ("POST", "/agent/v1/tasks/{id}/cut-report/verdict", {"keys": ["k"], "verdict": "agree"}), 409, "cut_report"),
    (_writing_note, ("POST", "/jobs/{id}/cut-report/g1/flip", None),
     ("POST", "/agent/v1/tasks/{id}/cut-report/flip", {"ref": "g1"}), 409, "note_running"),
    (_writing_note, ("POST", "/jobs/{id}/cut-report/changes/0/undo", None),
     ("POST", "/agent/v1/tasks/{id}/cut-report/undo", {"index": 0}), 409, "note_running"),
]


@pytest.mark.parametrize("state, page, agent, status, code", CASES)
def test_the_page_and_an_agent_get_the_same_answer(client, state, page, agent, status, code):
    task_id = f"parity-{uuid.uuid4().hex[:8]}"
    state(task_id)
    try:
        page_answer = client.request(page[0], page[1].format(id=task_id), json=page[2], headers=_HEADERS)
        agent_answer = client.request(agent[0], agent[1].format(id=task_id), json=agent[2], headers=_HEADERS)
    finally:
        job_store.delete_jobs([task_id])

    assert page_answer.status_code == agent_answer.status_code == status, (page_answer.text, agent_answer.text)
    assert agent_answer.json()["detail"]["code"] == code
    assert page_answer.json()["detail"] == agent_answer.json()["detail"]["message"]
