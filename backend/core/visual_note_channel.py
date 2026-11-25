"""Which Claude the visual note goes to, and what the user is told about it.

There are two ways to reach Claude from this machine and they differ in exactly
one thing that matters to a user: who pays, and therefore what they have to set
up. The Claude Code login already on this Mac costs nothing to configure and
spends the subscription's allowance. An Anthropic API key has to be created,
pasted, and kept, and spends per request. Both send the same transcript and the
same frames to the same company. When neither reaches Claude, the note is
written by the text model instead (decisions 5.3, 6.64) and never passed off as
Claude's: the result records ``note_written_by="text_fallback"`` and the
reason (``media_job``), and the note's copies say it did not see the picture.

This module picks between them and hands back a small object describing the
choice, so the page can say which one is about to be spent *before* the button
rather than after the bill. The choice is never silent: the channel's name goes
into the preview, into the job result, and into the note's recorded provenance.

The key is the default and the subscription is opt-in, which is the opposite of
what this file did while FluentFlow Local was one person's private tool. The
reason is distribution: Anthropic does not allow a third-party product to offer
claude.ai login or subscription rate limits to its users, and a release is a
third-party product. Someone running this from source on their own machine
against their own login is not, which is why the channel stays and is reached
through ``FLUENTFLOW_VISUAL_NOTE_CHANNEL`` instead of being deleted. See
``claude_code_note`` for the rest of that boundary.
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
        model=claude_code_note.subscription_model(),
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

    The user's own key comes first, and the subscription is only reached when
    it is asked for by name. A build that goes out to other people must not
    spend a claude.ai login on its own initiative; a source checkout on the
    maintainer's machine still can, by setting the preference.

    The subscription is never mentioned to a user who has not opted in: what
    the product offers publicly is the user's own API key, and the refusal says
    only that.
    """
    forced = preferred_channel()
    if forced == CHANNEL_SUBSCRIPTION:
        return _subscription_channel()
    return _api_key_channel(api_key)


def unavailable_outcome(channel: Any, text_model_ready: bool) -> dict[str, Any]:
    """How ``visual_note_unavailable`` records a frame note that cannot run,
    decided here once for the submission and the note step.

    A notice (success) only when the text model writes the note instead and
    nothing is the user's to fix: no Anthropic Key is the ordinary setup
    without Claude (decision 6.86). An expired Claude login is theirs to fix
    and stays a failure even though the text note still comes; with no text
    model either there is no note, a failure whatever the reason (review,
    2026-11-26: both had been recorded as a successful fallback)."""
    login_expired = (
        getattr(channel, "name", None) == CHANNEL_SUBSCRIPTION
        and claude_code_note.login_state() is False
    )
    return {
        "success": text_model_ready and not login_expired,
        "reason": "login_expired" if login_expired else "channel_unavailable",
        "fallback": "text_note" if text_model_ready else "none",
    }


__all__ = [
    "CHANNEL_ANTHROPIC_KEY",
    "CHANNEL_LABELS",
    "CHANNEL_SUBSCRIPTION",
    "Channel",
    "preferred_channel",
    "resolve_channel",
    "unavailable_outcome",
]
