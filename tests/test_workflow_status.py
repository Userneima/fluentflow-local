"""The per-step status Settings opens with, the start page and MCP read too.

Requirements (decision 6.24, owner 2026-10-22):
- each step says who does it now, from the same choice the pipeline makes, so
  the page can never disagree with what a job will do;
- something the person has to fix is listed once at the top and its step is
  marked: an expired Claude login, nothing able to write a note, speaker
  labels switched on without the component;
- an agent asking gets the steps for a task it submits: the cut falls to that
  agent when FluentFlow has no model of its own;
- an environment override of the preset is what is reported, since it wins.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.core import claude_code_note, cut_judge, local_intake_flow, visual_note_channel, workflow_status as ws
from backend.local_main import create_local_app


def _channel(name: str, available: bool) -> visual_note_channel.Channel:
    return visual_note_channel.Channel(
        name=name, label=name, model="", unavailable_reason=None if available else "no", write=lambda *a, **k: None,
    )


@pytest.fixture
def no_models(monkeypatch):
    """Nothing configured: no Claude, no picture model, no text key."""
    monkeypatch.setattr(cut_judge, "choose", lambda *a, **k: None)
    monkeypatch.setattr(visual_note_channel, "resolve_channel", lambda *a, **k: _channel(visual_note_channel.CHANNEL_ANTHROPIC_KEY, False))
    monkeypatch.setattr(ws, "local_ai_kwargs", lambda **k: {})
    monkeypatch.setattr(ws, "diarization_status", lambda: {"available": True})
    monkeypatch.setattr(ws, "load_project_env", lambda: None)


def _step(overview, step_id):
    return next(step for step in overview["steps"] if step["id"] == step_id)


def test_lists_every_step_in_the_order_a_recording_goes(no_models):
    ids = [step["id"] for step in ws.workflow_status()["steps"]]
    assert ids == ["intake", "cutting", "transcription", "notes", "outputs", "agent"]


def test_says_claude_decides_the_cut_when_the_pipeline_picks_it(no_models, monkeypatch):
    monkeypatch.setattr(cut_judge, "choose", lambda *a, **k: cut_judge.Judge(visual_note_channel.CHANNEL_SUBSCRIPTION, True, lambda *a: ""))
    summary = _step(ws.workflow_status(), "cutting")["summary"]["zh"]
    assert summary.startswith("Claude（本机已登录的订阅）判断剪哪里，会看截图")


def test_names_the_picture_model_when_that_decides(no_models, monkeypatch):
    monkeypatch.setattr(cut_judge, "choose", lambda *a, **k: cut_judge.Judge("vision:kimi-k2.6", True, lambda *a: ""))
    assert "看图模型 kimi-k2.6" in _step(ws.workflow_status(), "cutting")["summary"]["zh"]


def test_without_a_model_the_page_says_loudness_and_an_agent_hears_it_decides(no_models):
    assert "只按音量剪" in _step(ws.workflow_status(), "cutting")["summary"]["zh"]
    assert "由提交任务的 agent 判断" in _step(ws.workflow_status(for_agent=True), "cutting")["summary"]["zh"]


def test_reports_the_preset_an_environment_override_sets(no_models, monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_PROCESSING_PRESET", "speed")
    assert "速度优先" in _step(ws.workflow_status(), "cutting")["summary"]["zh"]


def test_claude_writes_the_note_when_its_channel_works(no_models, monkeypatch):
    monkeypatch.setattr(visual_note_channel, "resolve_channel", lambda *a, **k: _channel(visual_note_channel.CHANNEL_SUBSCRIPTION, True))
    monkeypatch.setattr(local_intake_flow, "auto_note_enabled", lambda: True)
    monkeypatch.setattr(claude_code_note, "cli_available", lambda: True)
    monkeypatch.setattr(claude_code_note, "login_state", lambda **k: True)
    overview = ws.workflow_status({"ai_provider": "deepseek"})
    assert _step(overview, "notes")["summary"]["zh"] == "Claude（本机已登录的订阅）结合画面写，照你的笔记风格。"
    assert overview["attention"] == []


def test_an_expired_claude_login_is_put_first(no_models, monkeypatch):
    monkeypatch.setattr(visual_note_channel, "resolve_channel", lambda *a, **k: _channel(visual_note_channel.CHANNEL_SUBSCRIPTION, False))
    monkeypatch.setattr(claude_code_note, "cli_available", lambda: True)
    monkeypatch.setattr(claude_code_note, "login_state", lambda **k: False)
    overview = ws.workflow_status()
    assert [item["id"] for item in overview["attention"]] == ["claude-login", "no-writer"]
    assert _step(overview, "notes")["level"] == "attention"


def test_a_text_model_writes_from_the_transcript_only(no_models, monkeypatch):
    monkeypatch.setattr(ws, "local_ai_kwargs", lambda **k: {"provider": k.get("ai_provider") or "deepseek", "api_key": "sk"})
    summary = _step(ws.workflow_status({"ai_provider": "kimi"}), "notes")["summary"]["zh"]
    assert summary == "Kimi（月之暗面）只按转录稿写，不看画面。"


def test_nothing_to_write_a_note_needs_the_person(no_models):
    overview = ws.workflow_status()
    assert _step(overview, "notes")["level"] == "attention"
    assert overview["attention"][0]["target"] == "accounts"


def test_transcript_only_is_a_choice_not_a_problem(no_models):
    overview = ws.workflow_status({"skip_ai_summary": "1"})
    assert _step(overview, "notes")["summary"]["zh"] == "只转录，不写笔记。"
    assert overview["attention"] == []


def test_speaker_labels_on_without_the_component_needs_the_person(no_models, monkeypatch):
    monkeypatch.setattr(ws, "diarization_status", lambda: {"available": False})
    overview = ws.workflow_status({"speaker_diarization": "1", "skip_ai_summary": "1"})
    assert [item["id"] for item in overview["attention"]] == ["speakers"]
    assert "不区分讲话人" in _step(overview, "transcription")["summary"]["zh"]
    assert ws.workflow_status({"skip_ai_summary": "1"})["attention"] == []


def test_the_page_and_an_agent_read_it_over_http(no_models, monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "secret")
    client = TestClient(create_local_app())
    page = client.get("/workflow-status", params={"skip_ai_summary": "1", "default_source": "upload"}).json()
    assert "首页默认停在「本机文件」" in _step(page, "intake")["summary"]["zh"]
    assert client.get("/agent/v1/status").status_code == 401
    agent = client.get("/agent/v1/status", headers={"Authorization": "Bearer secret"}).json()
    assert [row["step"] for row in agent["steps"]][:2] == ["拿到素材", "剪辑"]
    assert "首页默认" not in agent["steps"][0]["now"]
