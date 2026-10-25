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

class Channel:
    """A chat that answers with its name, or fails, and counts its calls."""

    def __init__(self, name: str, fails: bool = False):
        self.name, self.fails, self.calls = name, fails, 0

    def __call__(self, system: str, user: str) -> str:
        self.calls += 1
        if self.fails:
            raise RuntimeError(f"{self.name} hit its usage limit")
        return self.name


@pytest.fixture
def subscription(monkeypatch):
    claude = Channel("claude")
    monkeypatch.setenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", "subscription")
    monkeypatch.setattr(claude_text, "claude_chat", lambda *_a, **_k: (claude, "claude_subscription"))
    return claude


@pytest.fixture
def deepseek(monkeypatch):
    key = Channel("deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(media_job, "_key_chat", lambda kwargs: key)
    return key


def test_translation_uses_the_subscription_before_a_deepseek_key(subscription, deepseek):
    chat, channel = media_job._translation_writer({"provider": "deepseek"}, lambda value, name: None)

    assert channel == "claude_subscription" and chat("s", "u") == "claude"
    assert deepseek.calls == 0


def test_the_subtitle_check_uses_the_subscription_before_a_deepseek_key(subscription, deepseek):
    chat, channel = media_job._subtitle_chat({"provider": "deepseek"}, lambda value, name: None)

    assert channel == "claude_subscription" and chat("s", "u") == "claude"
    assert deepseek.calls == 0


def test_a_subscription_that_fails_while_running_hands_over_to_the_key(subscription, deepseek):
    # Choosing the channel only knows the CLI is logged in; the usage limit
    # shows up on a call (review, 2026-10-25).
    subscription.fails = True
    chat, _ = media_job._subtitle_chat({"provider": "deepseek"}, lambda value, name: None)

    assert [chat("s", "u") for _ in range(3)] == ["deepseek"] * 3
    assert subscription.calls == 1 and deepseek.calls == 3


@pytest.mark.real_coverage_checker
def test_the_coverage_check_uses_the_subscription_first_and_the_key_when_it_fails(monkeypatch, subscription):
    from backend.core import ai_summarizer

    key = Channel("deepseek")
    monkeypatch.setattr(ai_summarizer, "text_model_chat", lambda **_k: key)
    chat, name = vn.coverage_checker()
    assert name == "claude_subscription" and chat("s", "u") == "claude" and key.calls == 0

    subscription.fails = True
    chat, _ = vn.coverage_checker()
    assert chat("s", "u") == "deepseek"


def test_without_opting_in_the_subscription_is_never_reached(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", "api_key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(claude_text, "claude_chat", lambda *_a, **_k: pytest.fail("not opted in"))

    assert claude_text.subscription_chat() is None
    assert media_job._translation_writer({"provider": "deepseek"}, lambda value, name: None) == (None, "deepseek")


def test_opted_in_but_logged_out_falls_back_to_the_key(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", "subscription")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(claude_text, "claude_chat", lambda *_a, **_k: None)

    assert media_job._translation_writer({"provider": "deepseek"}, lambda value, name: None) == (None, "deepseek")
