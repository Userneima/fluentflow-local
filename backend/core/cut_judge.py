"""Who decides what to cut, and whether it can look at the picture.

Requirement (owner, 2026-10-16). The survey of the recording is the model's
reading of it, and the owner's design has the strongest model at hand do it,
looking at screenshots of the long silent stretches the transcript cannot
settle. The first version reused the note checker's choice instead, which puts
any configured text model first: on the owner's machine DeepSeek decided every
cut, from text alone. The order now is:

1. Claude, through the subscription signed in on this machine or the user's
   Anthropic API key (``visual_note_channel`` decides which, with its opt-in
   rules), shown the screenshots;
2. a model behind an OpenAI-compatible endpoint the user named for pictures
   (``FLUENTFLOW_VISION_BASE_URL``, ``_API_KEY``, ``_MODEL``: Kimi, MiMo, a
   relay), shown the screenshots;
3. the configured text model, which is sent the text only.

Measured 2026-10-16 on a screenshot of a shared document, asked for its four
highlighted lines: kimi-k2.6 and mimo-v2.6-flash read all four; deepseek-flash
(what ``deepseek-chat`` answers as) read two and mixed in others.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

VISION_BASE_URL_ENV = "FLUENTFLOW_VISION_BASE_URL"
VISION_API_KEY_ENV = "FLUENTFLOW_VISION_API_KEY"
VISION_MODEL_ENV = "FLUENTFLOW_VISION_MODEL"
MAX_TOKENS = 16_000

Image = tuple[str, Path]  # (label shown before it, file)
Ask = Callable[[str, str, list[Image]], str]


@dataclass(frozen=True)
class Judge:
    name: str
    sees_images: bool
    ask: Ask

    def text(self, system: str, user: str) -> str:
        return self.ask(system, user, [])


def _b64(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode("ascii")


def _claude_blocks(user: str, images: list[Image]) -> list[dict]:
    content: list[dict] = [{"type": "text", "text": user}]
    for label, path in images:
        content.append({"type": "text", "text": label})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _b64(path)}})
    return content


def _subscription(model: str) -> Ask:
    from backend.core import claude_code_note, claude_text

    def ask(system: str, user: str, images: list[Image]) -> str:
        command = claude_text._subscription_command(model, system)
        if not images:
            payload, _ = claude_code_note._run_cli(command, user, claude_code_note._run_tracked)
            return claude_code_note._payload_text(payload)
        # Pictures travel as one JSON user message, content blocks and all.
        message = json.dumps(
            {"type": "user", "message": {"role": "user", "content": _claude_blocks(user, images)}}, ensure_ascii=False,
        )
        payload, _ = claude_code_note._run_cli(
            [*command, "--input-format", "stream-json"], message, claude_code_note._run_tracked,
        )
        return claude_code_note._payload_text(payload)

    return ask


def _api_key(api_key: str | None) -> Ask:
    from backend.core import claude_vision

    model = claude_vision.latest_model(api_key)

    def ask(system: str, user: str, images: list[Image]) -> str:
        client = claude_vision.anthropic.Anthropic(api_key=claude_vision.resolve_api_key(api_key))
        with client.messages.stream(
            model=model, max_tokens=MAX_TOKENS, system=system,
            messages=[{"role": "user", "content": _claude_blocks(user, images)}],
        ) as stream:
            message = stream.get_final_message()
        return claude_vision._response_text(message)

    return ask


def vision_endpoint() -> tuple[str, str, str] | None:
    """The picture model the user set up, in the settings page or ``.env``."""
    from backend.core.local_config import get_sensitive_setting

    base, key, model = (
        (get_sensitive_setting(name) or "").strip() for name in ("vision_base_url", "vision_api_key", "vision_model")
    )
    return (base, key, model) if base and key and model else None


def _openai_compatible(base: str, key: str, model: str) -> Ask:
    def ask(system: str, user: str, images: list[Image]) -> str:
        from openai import OpenAI

        content: list[dict] = [{"type": "text", "text": user}]
        for label, path in images:
            content.append({"type": "text", "text": label})
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_b64(path)}"}})
        response = OpenAI(base_url=base, api_key=key).chat.completions.create(
            model=model, max_tokens=MAX_TOKENS,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
        )
        return (response.choices[0].message.content or "").strip()

    return ask


def _text_model() -> Judge | None:
    from backend.core import ai_summarizer
    from backend.core.local_entry_guards import local_ai_kwargs

    chat = ai_summarizer.text_model_chat(**local_ai_kwargs())
    if chat is None:
        return None
    return Judge("text_model", False, lambda system, user, images: chat(system, user))


def find(api_key: str | None = None) -> Judge | None:
    """The judge for this machine, in the owner's order; None with no model.
    (``choose`` is what callers use; tests replace it so they never reach a
    real model, and test this one directly.)"""
    from backend.core.local_config import resolve_secret
    from backend.core.visual_note_channel import CHANNEL_SUBSCRIPTION, resolve_channel

    key = api_key if api_key is not None else resolve_secret(None, "anthropic_api_key")
    channel = resolve_channel(key)
    if channel.available:
        if channel.name == CHANNEL_SUBSCRIPTION:
            return Judge(channel.name, True, _subscription(channel.model))
        return Judge(channel.name, True, _api_key(key))
    endpoint = vision_endpoint()
    if endpoint is not None:
        return Judge(f"vision:{endpoint[2]}", True, _openai_compatible(*endpoint))
    return _text_model()


choose = find


__all__ = ["Judge", "choose", "find", "vision_endpoint"]
