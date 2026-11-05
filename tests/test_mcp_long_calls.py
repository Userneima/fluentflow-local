"""A slow tool answers before the host stops waiting for it.

Requirement (2026-10-27 measurement, decision 5.40; done 2026-11-05): Codex
stops waiting on one tool call after 300 seconds unless its config is raised,
while writing a review note takes up to 45 minutes and FluentFlow goes on
working. Most of the product's users drive it from Codex. So:

- a slow call answers "still working" before that, not an error;
- calling the same tool again with the same arguments waits on the same run:
  the backend is asked once, not once per call (each call costs model time);
- the answer, when it comes, is the backend's answer, given once;
- different arguments are a different run;
- a quick call (saving a note without a model check) is answered directly.
"""

from __future__ import annotations

import threading

import pytest

from scripts import fluentflow_mcp_server as server


@pytest.fixture
def backend(monkeypatch):
    """A stand-in FluentFlow that counts how often it is asked and finishes
    when the test lets it."""
    calls: list[tuple[str, dict]] = []
    release = threading.Event()

    def request(method, path, *, payload=None, api_base=None, client_id=None, timeout=60):
        calls.append((path, payload))
        release.wait(5)
        return {"ok": True, "path": path, "written": True}

    monkeypatch.setattr(server, "_agent_request", request)
    monkeypatch.setattr(server, "_require_local_edition", lambda base: None)
    monkeypatch.setattr(server, "HOLD_SECONDS", 0.2)
    monkeypatch.setattr(server, "_runs", {})
    return calls, release


SLOW_CALLS = [
    ("write_review_note", {"task_id": "t1"}),
    ("revise_review_note", {"task_id": "t1", "instruction": "术语表加上 RAG"}),
    ("run_glossary_correction", {"task_id": "t1"}),
    ("draft_note_part", {"task_id": "t1", "rules": "规则", "part": 2}),
    ("save_agent_note", {"task_id": "t1", "note_markdown": "# 笔记", "model_check": True}),
]


@pytest.mark.parametrize("name,arguments", SLOW_CALLS)
def test_a_slow_call_says_still_working_and_the_repeat_waits_on_the_same_run(backend, name, arguments):
    calls, release = backend

    first = server._call_tool(name, arguments)
    assert "isError" not in first
    assert '"still_working"' in first["content"][0]["text"]

    second = server._call_tool(name, arguments)
    assert '"still_working"' in second["content"][0]["text"]
    assert len(calls) == 1

    release.set()
    third = server._call_tool(name, arguments)
    assert '"written": true' in third["content"][0]["text"]
    assert len(calls) == 1

    # Asked again after the answer was given: a new run, not the old answer.
    server._call_tool(name, arguments)
    assert len(calls) == 2


def test_different_arguments_are_a_different_run(backend):
    calls, release = backend
    server._call_tool("revise_review_note", {"task_id": "t1", "instruction": "改 A"})
    server._call_tool("revise_review_note", {"task_id": "t1", "instruction": "改 B"})
    assert [payload["instruction"] for _path, payload in calls] == ["改 A", "改 B"]
    release.set()


def test_a_call_that_finishes_in_time_is_answered_directly(backend):
    calls, release = backend
    release.set()
    answer = server._call_tool("write_review_note", {"task_id": "t1"})
    assert '"written": true' in answer["content"][0]["text"]
    assert len(calls) == 1


def test_saving_a_note_without_a_model_check_is_not_held(backend):
    calls, release = backend
    release.set()
    answer = server._call_tool("save_agent_note", {"task_id": "t1", "note_markdown": "# 笔记"})
    assert '"written": true' in answer["content"][0]["text"]
    assert not server._runs


def test_the_hold_is_inside_codexs_wait():
    # Codex gives up on a tool call at 300 seconds; the answer has to be back first.
    assert server.HOLD_SECONDS <= 270
