"""A Claude subscription the user opted into comes before a key billed per token.

Requirement (owner, 2026-10-25): the errands that went to a text-model key
first (subtitle check, English subtitle translation, the note's coverage
check, where a recording ends) spent his DeepSeek balance down to nothing
while the subscription he had already paid for sat idle. When the machine
chose the subscription, these use it first; a user who never opted in is
never sent to it and keeps the old order.
"""

from __future__ import annotations

import pytest

from backend.core import claude_text, media_job
from backend.core import visual_note_job as vn

SUBSCRIBED = (lambda system, user: "claude", "claude_subscription")


@pytest.fixture
def opted_in(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", "subscription")
    monkeypatch.setattr(claude_text, "claude_chat", lambda *_a, **_k: SUBSCRIBED)


def test_translation_uses_the_subscription_before_a_deepseek_key(monkeypatch, opted_in):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")

    assert media_job._translation_writer({"provider": "deepseek"}, lambda value, name: None) == SUBSCRIBED


def test_the_subtitle_check_uses_the_subscription_before_a_deepseek_key(monkeypatch, opted_in):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")

    chat, channel = media_job._subtitle_chat({"provider": "deepseek"}, lambda value, name: None)

    assert channel == "claude_subscription" and chat("s", "u") == "claude"


@pytest.mark.real_coverage_checker
def test_the_coverage_check_uses_the_subscription_before_a_text_model(monkeypatch, opted_in):
    from backend.core import ai_summarizer

    monkeypatch.setattr(ai_summarizer, "text_model_chat", lambda **_k: pytest.fail("the paid key must wait"))

    assert vn.coverage_checker() == SUBSCRIBED


def test_without_opting_in_the_subscription_is_never_reached(monkeypatch):
    monkeypatch.delenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(claude_text, "claude_chat", lambda *_a, **_k: pytest.fail("not opted in"))

    assert claude_text.subscription_chat() is None
    assert media_job._translation_writer({"provider": "deepseek"}, lambda value, name: None) == (None, "deepseek")


def test_opted_in_but_logged_out_falls_back_to_the_key(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", "subscription")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(claude_text, "claude_chat", lambda *_a, **_k: None)

    assert media_job._translation_writer({"provider": "deepseek"}, lambda value, name: None) == (None, "deepseek")
