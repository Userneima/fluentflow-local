"""A plain text request to Claude, through whichever channel the note uses.

For the text jobs that otherwise need a DeepSeek, OpenAI or Qwen key, such as
the Chinese side of an English recording's subtitles. Someone whose notes are
written by Claude often has no other key at all, and every English recording
they processed came back without Chinese subtitles. The channel is the one
``visual_note_channel`` picks, so the same credential pays and the same
opt-in rules apply.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from backend.core import claude_code_note, claude_vision, model_usage
from backend.core.visual_note_channel import CHANNEL_SUBSCRIPTION, preferred_channel, resolve_channel

logger = logging.getLogger(__name__)

TEXT_MAX_TOKENS = 16_000

Chat = Callable[[str, str], str]


def _api_key_chat(api_key: str | None, model: str, max_tokens: int = TEXT_MAX_TOKENS) -> Chat:
    def chat(system: str, user: str) -> str:
        client = claude_vision.anthropic.Anthropic(api_key=claude_vision.resolve_api_key(api_key))
        try:
            with model_usage.call(model_usage.ANTHROPIC, model) as call:
                with client.messages.stream(
                    model=model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                ) as stream:
                    message = stream.get_final_message()
                call.anthropic(message)
        except Exception as exc:  # noqa: BLE001 - mapped to something a user can act on
            raise claude_vision.ClaudeVisionError(
                claude_vision._friendly(exc), actionable=claude_vision._is_actionable(exc),
            ) from exc
        return claude_vision._response_text(message)

    return chat


def _subscription_command(model: str, system: str) -> list[str]:
    binary = claude_code_note.cli_path()
    if not binary:  # pragma: no cover - checked by the channel before this runs
        raise claude_vision.ClaudeVisionError(str(claude_code_note.unavailable_reason()), actionable=True)
    return [
        binary,
        "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--system-prompt", system,
        # A text errand: no tools at all, and nothing from this machine's
        # Claude Code configuration.
        "--allowed-tools", "",
        "--disallowed-tools", claude_code_note._DISALLOWED_TOOLS + ",Read",
        "--safe-mode",
        "--strict-mcp-config",
        "--model", model,
    ]


def _subscription_chat(model: str, runner: Callable | None) -> Chat:
    execute = runner if runner is not None else claude_code_note._run_tracked

    def chat(system: str, user: str) -> str:
        payload, _reads = claude_code_note._run_cli(_subscription_command(model, system), user, execute)
        return claude_code_note._payload_text(payload)

    return chat


def claude_chat(
    api_key: str | None = None, *, runner: Callable | None = None, max_tokens: int = TEXT_MAX_TOKENS,
) -> tuple[Chat, str] | None:
    """A ``(system, user) -> text`` function and the channel's name, or ``None``
    when this machine cannot reach Claude either way. ``max_tokens`` bounds a
    key's answer; the subscription's is the CLI's own."""
    channel = resolve_channel(api_key)
    if not channel.available:
        return None
    if channel.name == CHANNEL_SUBSCRIPTION:
        return _subscription_chat(channel.model, runner), channel.name
    return _api_key_chat(api_key, claude_vision.latest_model(api_key), max_tokens), channel.name


def subscription_chat(*, runner: Callable | None = None) -> tuple[Chat, str] | None:
    """Claude through the subscription, only when this machine opted into it
    and is logged in; ``None`` otherwise.

    For the errands that would otherwise go to a text-model key first
    (subtitle check, translation, coverage check, where a recording ends).
    A subscription that is already paid for costs nothing more per call, so
    when the user chose it, it comes before a key billed per token (owner,
    2026-10-25: his DeepSeek balance ran out on the subtitle check while his
    subscription sat idle). A user who never opted in is never sent to it.
    """
    if preferred_channel() != CHANNEL_SUBSCRIPTION:
        return None
    return claude_chat(None, runner=runner)


def subscription_first(fallback: Chat | None, *, runner: Callable | None = None) -> tuple[Chat, str] | None:
    """``subscription_chat`` that hands over to ``fallback`` (the user's own
    key) the first time a subscription call fails at run time: the usage limit
    reached, the login expired mid-task. Choosing the channel only knows the
    CLI is installed and logged in, which says nothing about the limit
    (review, 2026-10-25). Once failed, the rest of the job stays on the key.
    Without a fallback the failure is raised as before."""
    found = subscription_chat(runner=runner)
    if found is None or fallback is None:
        return found
    subscribed, name = found
    failed = threading.Event()

    def chat(system: str, user: str) -> str:
        if not failed.is_set():
            try:
                return subscribed(system, user)
            except Exception as exc:  # noqa: BLE001 - the key takes over; its own failure is raised
                logger.warning("Claude subscription call failed, using the configured key: %s", exc)
                failed.set()
        return fallback(system, user)

    # Who actually answered, for a caller that records it: the name returned
    # here is the subscription's even after the key took over.
    chat.fell_back = failed  # type: ignore[attr-defined]
    return chat, name


__all__ = ["Chat", "claude_chat", "subscription_chat", "subscription_first"]
