"""What the Agent access panel is told about connecting an agent.

Requirements (GUI checklist 2026-10-22, lane 3 item 9): the panel says whether
an agent can connect at all (the access token is set) and whether one has
connected since FluentFlow started. Only an agent counts: a request with a
wrong token did not connect, and the browser page reading the same API is not
an agent.
"""

from fastapi.testclient import TestClient

import backend.core.local_request_scope as scope
from backend.local_main import create_local_app


def _status(client):
    r = client.get("/agent-access/status")
    assert r.status_code == 200
    return r.json()


def test_says_no_agent_can_connect_without_a_token(monkeypatch):
    monkeypatch.delenv("FLUENTFLOW_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(scope, "_last_agent_request_at", None)
    status = _status(TestClient(create_local_app()))
    assert status == {"token_configured": False, "last_agent_request_at": None}


def test_records_an_agent_that_got_through(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "secret")
    monkeypatch.setattr(scope, "_last_agent_request_at", None)
    client = TestClient(create_local_app())
    assert _status(client)["last_agent_request_at"] is None

    client.get("/agent/v1/tasks", headers={"Authorization": "Bearer secret"})

    status = _status(client)
    assert status["token_configured"] is True
    assert isinstance(status["last_agent_request_at"], float)


def test_a_wrong_token_or_the_browser_page_is_not_an_agent(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "secret")
    monkeypatch.setattr(scope, "_last_agent_request_at", None)
    client = TestClient(create_local_app())

    client.get("/agent/v1/tasks", headers={"Authorization": "Bearer wrong"})
    client.get("/agent/v1/tasks", headers={
        "Authorization": "Bearer secret", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin",
    })

    assert _status(client)["last_agent_request_at"] is None
