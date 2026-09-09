"""Which Claude the visual note goes to, and what the user is told about it.

There are two ways to reach Claude from this machine and they differ in exactly
one thing that matters to a user: who pays, and therefore what they have to set
up. The Claude Code login already on this Mac costs nothing to configure and
spends the subscription's allowance. An Anthropic API key has to be created,
pasted, and kept, and spends per request. Both send the same transcript and the
same frames to the same company, and neither is a fallback for a *different*
provider — a run that cannot reach Claude fails and says so, rather than
quietly producing a note from DeepSeek or Qwen and calling it the same thing.

This module picks between them and hands back a small object describing the
choice, so the page can say "this will use the Claude subscription on this Mac"
*before* the button rather than after the bill. The choice is never silent: the
channel's name goes into the preview, into the job result, and into the note's
recorded provenance.

Scope: the subscription channel exists because FluentFlow Local is a private
tool on its maintainer's own machine. See ``claude_code_note`` for the boundary
and ``docs/claude_agent_sdk_deferred_plan.md`` for the evidence behind it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from backend.core import claude_code_note, claude_vision

CHANNEL_SUBSCRIPTION = claude_code_note.CHANNEL_NAME
CHANNEL_ANTHROPIC_KEY = "anthropic_api_key"

# What the page says out loud. Short, and specific about the thing a user would
# want to know before clicking: whose allowance this spends.
CHANNEL_LABELS = {
    CHANNEL_SUBSCRIPTION: "本机已登录的 Claude 订阅",
    CHANNEL_ANTHROPIC_KEY: "你自己的 Anthropic API Key",
}

_PREFERENCE_ENV = "FLUENTFLOW_VISUAL_NOTE_CHANNEL"
_PREFERENCE_ALIASES = {
    "subscription": CHANNEL_SUBSCRIPTION,
    "claude_code": CHANNEL_SUBSCRIPTION,
    CHANNEL_SUBSCRIPTION: CHANNEL_SUBSCRIPTION,
    "api_key": CHANNEL_ANTHROPIC_KEY,
    "anthropic": CHANNEL_ANTHROPIC_KEY,
    CHANNEL_ANTHROPIC_KEY: CHANNEL_ANTHROPIC_KEY,
}


@dataclass(frozen=True)
class Channel:
    """One way to reach Claude, and everything the page needs to describe it."""

    name: str
    label: str
    model: str
    unavailable_reason: str | None
    write: Callable[..., Any]

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None


def preferred_channel() -> str | None:
    """A forced choice from the environment, or ``None`` to decide by what works."""
    raw = (os.environ.get(_PREFERENCE_ENV) or "").strip().lower()
    return _PREFERENCE_ALIASES.get(raw)


def _subscription_channel() -> Channel:
    return Channel(
        name=CHANNEL_SUBSCRIPTION,
        label=CHANNEL_LABELS[CHANNEL_SUBSCRIPTION],
        model=claude_vision.configured_model(),
        unavailable_reason=claude_code_note.unavailable_reason(),
        write=claude_code_note.write_visual_note,
    )


def _api_key_channel(api_key: str | None) -> Channel:
    return Channel(
        name=CHANNEL_ANTHROPIC_KEY,
        label=CHANNEL_LABELS[CHANNEL_ANTHROPIC_KEY],
        model=claude_vision.configured_model(),
        unavailable_reason=claude_vision.unavailable_reason(api_key),
        write=claude_vision.write_visual_note,
    )


def resolve_channel(api_key: str | None = None) -> Channel:
    """Pick the channel this machine will actually use.

    The subscription comes first when nothing is forced, because it is the one
    that asks the user for nothing. The API-key path is not deleted and not
    deprecated: it is what a machine without Claude Code falls back to, and it
    is what this feature has to return to if FluentFlow Local ever stops being
    one person's private tool.

    When neither works, the refusal is the subscription's — "log in once in the
    terminal" is a smaller thing to ask than "go create an API key", and the key
    remains available to anyone who prefers it via ``FLUENTFLOW_VISUAL_NOTE_CHANNEL``.
    """
    forced = preferred_channel()
    if forced == CHANNEL_SUBSCRIPTION:
        return _subscription_channel()
    if forced == CHANNEL_ANTHROPIC_KEY:
        return _api_key_channel(api_key)

    subscription = _subscription_channel()
    if subscription.available:
        return subscription
    api = _api_key_channel(api_key)
    if api.available:
        return api
    return subscription


__all__ = [
    "CHANNEL_ANTHROPIC_KEY",
    "CHANNEL_LABELS",
    "CHANNEL_SUBSCRIPTION",
    "Channel",
    "preferred_channel",
    "resolve_channel",
]
