"""Which Claude writes the note.

Requirements (2026-10-10): notes follow the newest Opus instead of a model id
written into the code.

1. Through the subscription, Claude Code is asked for "opus", its own name for
   the newest Opus, and the note records the model id that actually answered.
2. Through an API key, the newest Opus in Anthropic's model list is used,
   looked up at most once a day.
3. A failed lookup never costs the note: the last answer or a fallback is used.
4. FLUENTFLOW_VISUAL_NOTE_MODEL still names a model outright.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from backend.core import claude_code_note, claude_vision


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.delenv("FLUENTFLOW_VISUAL_NOTE_MODEL", raising=False)
    monkeypatch.setitem(claude_vision._LATEST_MODEL, "id", None)
    monkeypatch.setitem(claude_vision._LATEST_MODEL, "at", 0.0)


def _cli(monkeypatch, seen):
    monkeypatch.setattr(claude_code_note, "cli_path", lambda: "/usr/local/bin/claude")
    monkeypatch.setattr(claude_code_note, "unavailable_reason", lambda: None)

    def runner(command, **kwargs):
        seen.append(command[command.index("--model") + 1])
        line = json.dumps({
            "type": "result", "is_error": False,
            "result": json.dumps({"note_markdown": "# 标题", "basis_note": ""}),
            "modelUsage": {"claude-haiku-5-5": {"outputTokens": 3}, "claude-opus-5-5": {"outputTokens": 900}},
        })
        return subprocess.CompletedProcess(command, 0, line, "")

    return runner


def test_the_subscription_asks_for_opus_and_records_who_answered(monkeypatch):
    seen = []
    draft = claude_code_note.write_visual_note("[00:00] 你好", [], runner=_cli(monkeypatch, seen))
    assert seen == ["opus"]
    assert draft.model == "claude-opus-5-5"


def test_an_override_names_the_model_outright(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_VISUAL_NOTE_MODEL", "claude-opus-6")
    seen = []
    claude_code_note.write_visual_note("[00:00] 你好", [], runner=_cli(monkeypatch, seen))
    assert seen == ["claude-opus-6"]
    assert claude_vision.latest_model("k") == "claude-opus-6"


class _Models:
    def __init__(self, ids):
        self.calls = 0
        self.ids = ids

    def list(self, limit=100):
        self.calls += 1
        if self.ids is None:
            raise RuntimeError("offline")
        return [SimpleNamespace(id=model_id, created_at=created) for model_id, created in self.ids]


def test_the_api_key_uses_the_newest_opus_once_a_day():
    models = _Models([("claude-opus-5", "2026-03-01"), ("claude-opus-5-5", "2026-09-01"),
                      ("claude-sonnet-5-5", "2026-09-20"), ("claude-opus-4-8", "2025-12-01")])
    client = SimpleNamespace(models=models)
    assert claude_vision.latest_model("k", client=client, now=1000.0) == "claude-opus-5-5"
    assert claude_vision.latest_model("k", client=client, now=1000.0 + 3600) == "claude-opus-5-5"
    assert models.calls == 1
    claude_vision.latest_model("k", client=client, now=1000.0 + 2 * 86400)
    assert models.calls == 2


def test_a_failed_lookup_falls_back_without_failing():
    client = SimpleNamespace(models=_Models(None))
    assert claude_vision.latest_model("k", client=client, now=1.0) == claude_vision.DEFAULT_MODEL
