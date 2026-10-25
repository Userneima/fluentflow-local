"""Requirement (2026-10-15): an agent that says nothing about speaker
separation gets it off, whether it hands over a file path or a link, and
whether or not the component is installed. It runs only when asked for; an
explicit choice always wins.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routers.local_agent as local_agent
from backend.core import speaker_diarization
from backend.core.task_submission import local_path_options

_TOKEN = "speaker-default-token"


def _link_options(monkeypatch, options: dict) -> dict:
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", _TOKEN)
    seen: dict = {}

    async def submit(**kwargs):
        seen.update(kwargs["raw_options"])
        return {"task_id": "link-1", "status": "queued"}

    monkeypatch.setattr(local_agent, "submit_video_source_job", submit)
    app = FastAPI()
    app.include_router(local_agent.router)
    response = TestClient(app).post(
        "/agent/v1/tasks",
        headers={"x-fluentflow-access-token": _TOKEN},
        json={"input": "https://www.bilibili.com/video/BV1xx411c7mD", "options": options},
    )
    assert response.status_code == 200, response.text
    return seen


@pytest.mark.parametrize("dependency", [True, False])
def test_path_and_link_leave_it_off_when_not_asked(monkeypatch, dependency):
    monkeypatch.setattr(speaker_diarization, "diarization_status", lambda: {"dependency_installed": dependency, "available": dependency})

    path_options, _ = local_path_options({})
    link_options = _link_options(monkeypatch, {})

    assert path_options.get("speaker_diarization") is None
    assert link_options.get("speaker_diarization") is None


def test_an_explicit_choice_wins_either_way(monkeypatch):
    # An explicit "off" is kept as "false" so the saved choice cannot override it.
    assert local_path_options({"speaker_diarization": False})[0]["speaker_diarization"] == "false"
    assert _link_options(monkeypatch, {"speaker_diarization": False})["speaker_diarization"] is False
    assert local_path_options({"speaker_diarization": True})[0]["speaker_diarization"] == "true"
    assert _link_options(monkeypatch, {"speaker_diarization": True})["speaker_diarization"] is True
