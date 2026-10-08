"""Requirement: an agent that says nothing about speaker separation gets the
same answer whether it hands over a file path or a link — on when pyannote is
installed, and not requested at all when it is not (a request that is always
skipped only plants a failed-looking step). An explicit choice always wins.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routers.local_agent as local_agent
from backend.core import speaker_diarization
from backend.routers.local_processing import local_path_options

_TOKEN = "speaker-default-token"


@pytest.fixture()
def installed(monkeypatch):
    def set_installed(value: bool) -> None:
        monkeypatch.setattr(speaker_diarization, "diarization_status", lambda: {"dependency_installed": value})
    return set_installed


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
def test_path_and_link_get_the_same_default(monkeypatch, installed, dependency):
    installed(dependency)

    path_options, _ = local_path_options({})
    link_options = _link_options(monkeypatch, {})

    expected = "true" if dependency else None
    assert path_options.get("speaker_diarization") == expected
    assert link_options.get("speaker_diarization") == expected


def test_an_explicit_choice_wins_either_way(monkeypatch, installed):
    installed(True)
    assert "speaker_diarization" not in local_path_options({"speaker_diarization": False})[0]
    assert _link_options(monkeypatch, {"speaker_diarization": False})["speaker_diarization"] is False

    installed(False)
    assert local_path_options({"speaker_diarization": True})[0]["speaker_diarization"] == "true"
