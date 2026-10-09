"""Chinese subtitles for an English recording when only Claude is set up.

Requirements (2026-10-09, stability run):

1. With no DeepSeek/OpenAI/Qwen key but a working Claude channel, an English
   recording still gets its Chinese subtitles, written by Claude.
2. With a text-model key, the text model translates, as before; Claude is not
   spent on it.
3. With neither, the text model is kept so the failure names the key to add.
4. Translating through the subscription gives the CLI no tools.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from backend.core import claude_text, media_job
from backend.core.ai_summarizer import generate_bilingual_segments_zh

SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": "Hello everyone,"},
    {"start": 2.0, "end": 4.0, "text": "welcome to the class."},
]


@pytest.fixture(autouse=True)
def _no_text_keys(monkeypatch):
    for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "")
    monkeypatch.setattr("backend.core.ai_summarizer.load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr("backend.core.ai_client.load_dotenv", lambda *a, **k: None)


def _claude_answers(calls):
    def chat(system, user):
        calls.append((system, user))
        return json.dumps([
            {"start_index": 0, "end_index": 1, "text_en": "Hello everyone, welcome to the class.", "text_zh": "大家好，欢迎来上课。"}
        ])
    return chat


def test_no_text_key_but_claude_translates(monkeypatch):
    calls = []
    monkeypatch.setattr(media_job, "claude_chat", lambda key: (_claude_answers(calls), "anthropic_api_key"))
    chat, channel = media_job._translation_writer({"provider": "deepseek"}, lambda value, name: None)
    assert channel == "anthropic_api_key"

    result = generate_bilingual_segments_zh(SEGMENTS, provider="deepseek", chat=chat)

    assert [s["text_zh"] for s in result.segments] == ["大家好，欢迎来上课。"]
    assert calls and "welcome to the class" in calls[0][1]


def test_text_key_keeps_the_text_model(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(media_job, "claude_chat", lambda key: pytest.fail("Claude must not be used"))
    chat, channel = media_job._translation_writer({"provider": "deepseek"}, lambda value, name: None)
    assert chat is None
    assert channel == "deepseek"


def test_neither_names_the_missing_key(monkeypatch):
    monkeypatch.setattr(media_job, "claude_chat", lambda key: None)
    chat, _ = media_job._translation_writer({"provider": "deepseek"}, lambda value, name: None)
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        generate_bilingual_segments_zh(SEGMENTS, provider="deepseek", chat=chat)


def test_subscription_translation_runs_without_tools(monkeypatch):
    monkeypatch.setattr(claude_text.claude_code_note, "cli_path", lambda: "/usr/local/bin/claude")
    seen = {}

    def runner(command, **kwargs):
        seen["command"] = command
        seen["input"] = kwargs.get("input")
        lines = [
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "[]"}),
        ]
        return subprocess.CompletedProcess(command, 0, "\n".join(lines), "")

    chat = claude_text._subscription_chat("claude-opus-5", runner)
    assert chat("SYSTEM", "the segments") == "[]"
    command = seen["command"]
    assert command[command.index("--allowed-tools") + 1] == ""
    assert "Read" in command[command.index("--disallowed-tools") + 1].split(",")
    assert command[command.index("--system-prompt") + 1] == "SYSTEM"
    assert seen["input"] == "the segments"
