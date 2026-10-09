"""A plain text request to Claude, through whichever channel the note uses.

For the text jobs that otherwise need a DeepSeek, OpenAI or Qwen key, such as
the Chinese side of an English recording's subtitles. Someone whose notes are
written by Claude often has no other key at all, and every English recording
they processed came back without Chinese subtitles. The channel is the one
``visual_note_channel`` picks, so the same credential pays and the same
opt-in rules apply.
"""

from __future__ import annotations

from typing import Callable

from backend.core import claude_code_note, claude_vision
from backend.core.visual_note_channel import CHANNEL_SUBSCRIPTION, resolve_channel

TEXT_MAX_TOKENS = 16_000

Chat = Callable[[str, str], str]


def _api_key_chat(api_key: str | None, model: str) -> Chat:
    def chat(system: str, user: str) -> str:
        client = claude_vision.anthropic.Anthropic(api_key=claude_vision.resolve_api_key(api_key))
        try:
            with client.messages.stream(
                model=model,
                max_tokens=TEXT_MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
            ) as stream:
                message = stream.get_final_message()
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


def claude_chat(api_key: str | None = None, *, runner: Callable | None = None) -> tuple[Chat, str] | None:
    """A ``(system, user) -> text`` function and the channel's name, or ``None``
    when this machine cannot reach Claude either way."""
    channel = resolve_channel(api_key)
    if not channel.available:
        return None
    if channel.name == CHANNEL_SUBSCRIPTION:
        return _subscription_chat(channel.model, runner), channel.name
    return _api_key_chat(api_key, channel.model), channel.name


__all__ = ["Chat", "claude_chat"]
