"""OpenAI-compatible client + chat/vision calls and provider resolution,
extracted from ai_summarizer.py. Imports only ai_config + stdlib, so there is
no circular import back to ai_summarizer."""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import httpx
from dotenv import load_dotenv
from openai import OpenAI

from backend.core import fake_model, model_answers, model_usage, note_progress
from backend.core import text_providers
from backend.core.ai_config import (
    DEFAULT_DEEPSEEK_MODEL,
    RETIRED_DEEPSEEK_MODELS,
)


logger = logging.getLogger(__name__)

def _normalize_provider(provider: str | None) -> str:
    p = (provider or os.environ.get("AI_PROVIDER") or "deepseek").strip().lower()
    if p not in text_providers.PROVIDERS:
        raise ValueError(f"Unsupported AI provider: {provider}")
    return p


def _provider_base_url(provider: str) -> str:
    return text_providers.base_url(text_providers.get(provider) or text_providers.PROVIDERS["deepseek"])




def _provider_default_model(provider: str) -> str:
    """The provider's default: a model named in ``.env``, else the
    recommended-model table (``model_defaults``), else this build's own."""
    from backend.core import model_defaults

    entry = text_providers.get(provider) or text_providers.PROVIDERS["deepseek"]
    key = entry.id
    named = (os.environ.get(entry.model_env) or "").strip()
    chosen = named or model_defaults.default_model(key) or entry.default_model
    if key == "deepseek" and chosen in RETIRED_DEEPSEEK_MODELS:
        return model_defaults.default_model("deepseek") or DEFAULT_DEEPSEEK_MODEL
    return chosen


def _normalize_model(provider: str, model: str | None) -> str:
    """The model to call: the caller's choice, or the provider's default.

    A named model is used as given. This used to rewrite ``deepseek-chat`` to
    the reasoner, so a user who picked the cheaper model in settings was billed
    for the other one without being told.
    """
    value = (model or "").strip()
    if provider == "deepseek" and value in RETIRED_DEEPSEEK_MODELS:
        # A retired name the provider itself sends to its successor; asking
        # for the successor by name keeps working after the routing ends.
        return _provider_default_model("deepseek")
    return value or _provider_default_model(provider)


def _provider_api_key(provider: str, api_key: str | None = None) -> str:
    load_dotenv()
    entry = text_providers.get(provider) or text_providers.PROVIDERS["deepseek"]
    env_names = entry.key_envs
    env_name = env_names[0]
    env_key = next(
        ((os.environ.get(name) or "").strip() for name in env_names if (os.environ.get(name) or "").strip()),
        "",
    )
    key = (api_key or env_key).strip()
    if not key:
        raise ValueError(f"{env_name} 未设置：请在 .env 中配置或在设置页填写 API Key。")
    return key


# Per-request limits. The SDK's default is a ten-minute timeout with two
# retries, so one stalled connection could hold a note stage for half an hour
# before anything above it noticed. Reading is given five minutes because a
# long reasoning answer legitimately streams nothing for a while; connecting
# and writing never should.
REQUEST_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=10.0)
REQUEST_MAX_RETRIES = 1


def _get_client(*, provider: str, api_key: str | None = None) -> OpenAI:
    key = _provider_api_key(provider, api_key)
    return OpenAI(
        api_key=key,
        base_url=_provider_base_url(provider),
        timeout=REQUEST_TIMEOUT,
        max_retries=REQUEST_MAX_RETRIES,
    )


def _provider_of(client: OpenAI) -> str:
    """Recover the provider from the client's base URL.

    Callers pass a client, not a provider name, so this avoids changing ten
    call sites in ai_summarizer just to label token usage.
    """

    base = str(getattr(client, "base_url", "") or "").rstrip("/")
    if not base:
        return "unknown"
    for entry in text_providers.PROVIDERS.values():
        if base == text_providers.base_url(entry) or base == entry.base_url.rstrip("/"):
            return entry.id
    if "dashscope" in base:
        return "qwen"
    if "deepseek" in base:
        return "deepseek"
    return "openai"


# Models a provider turned away on this run, and what was used instead, so the
# next call goes straight to the replacement.
_REPLACED: dict[tuple[str, str], str] = {}
_GONE_WORDS = ("model not exist", "model_not_found", "does not exist", "not found", "no such model", "invalid model")


def _model_gone(exc: Exception) -> bool:
    """Whether the provider refused the request because it has no such model:
    a name it retired or renamed. Other refusals (key, balance, limits) are not."""
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    return status in (400, 404) and "model" in text and any(word in text for word in _GONE_WORDS)


def _listed_models(client: OpenAI) -> set[str] | None:
    try:
        return {str(getattr(model, "id", "")) for model in client.models.list()}
    except Exception:  # noqa: BLE001 - the list only narrows the choice
        return None


def _create(client: OpenAI, model: str, *, may_replace: bool = True, **kwargs: Any) -> tuple[Any, str]:
    """Send the request; when the provider no longer has ``model``, send it
    again with the provider's current default (owner, 2026-10-16: a provider
    retired deepseek-reasoner and nothing here noticed for months). The model
    the user picked stays in their settings; the settings page says it is gone.
    Returns the response and the model that answered."""
    provider = _provider_of(client)
    model = _REPLACED.get((provider, model), model)
    try:
        return _send(client, provider, model, **kwargs), model
    except Exception as exc:
        if not _model_gone(exc):
            raise
        # The defaults are text models: a request with pictures is not moved onto one.
        fallback = _provider_default_model(provider) if may_replace and provider in text_providers.PROVIDERS else ""
        listed = _listed_models(client)
        if not fallback or fallback == model or (listed is not None and fallback not in listed):
            raise RuntimeError(
                f"模型 {model} 已不在服务商的可用列表里，请在设置页换一个模型。"
                + (f"现在可用的有：{'、'.join(sorted(listed)[:8])}" if listed else "")
            ) from exc
        logger.warning("model %s is gone at %s; using %s", model, provider, fallback)
        _REPLACED[(provider, model)] = fallback
        return _send(client, provider, fallback, **kwargs), fallback


def _send(client: OpenAI, provider: str, model: str, *, on_text: Callable[[str], None] | None = None,
          **kwargs: Any) -> Any:
    """One request, recorded (``model_usage``). With ``on_text`` the answer is
    streamed and ``on_text`` gets the text so far as it grows, so the page can
    show a note while it is written; the response returned is the same shape.
    A provider that refuses the streaming request before answering anything
    is asked again without streaming: nothing was written, so nothing is paid
    twice."""
    with model_usage.call(provider, model) as call:
        if call.fake:
            response = fake_model.openai_response(fake_model.reply_to_messages(kwargs.get("messages")))
            if on_text is not None:
                fake_model.stream(str(response.choices[0].message.content or ""), on_text)
        elif on_text is not None:
            try:
                stream = client.chat.completions.create(
                    model=model, stream=True, stream_options={"include_usage": True}, **kwargs,
                )
            except Exception as exc:  # noqa: BLE001 - refused up front only
                if getattr(exc, "status_code", None) != 400 or _model_gone(exc):
                    raise
                logger.warning("streaming refused by %s (%s); asking without streaming", model, exc)
                stream = None
                response = client.chat.completions.create(model=model, **kwargs)
            if stream is not None:
                response = _read_stream(stream, model, on_text)
        else:
            response = client.chat.completions.create(model=model, **kwargs)
        call.openai(response)
    return response


def _read_stream(stream: Any, model: str, on_text: Callable[[str], None]) -> Any:
    """A streamed answer read to its end, as one response with the usage the
    last chunk carries."""
    pieces: list[str] = []
    usage = None
    finish_reason = None
    for chunk in stream:
        if getattr(chunk, "usage", None) is not None:
            usage = chunk.usage
        for choice in getattr(chunk, "choices", None) or []:
            delta = getattr(choice, "delta", None)
            piece = getattr(delta, "content", None) if delta is not None else None
            if piece:
                pieces.append(piece)
                try:
                    on_text("".join(pieces))
                except Exception:  # noqa: BLE001 - showing progress never stops the note
                    logger.debug("progress callback failed", exc_info=True)
            if getattr(choice, "finish_reason", None):
                finish_reason = choice.finish_reason
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="".join(pieces)), finish_reason=finish_reason)],
        usage=usage,
        model=model,
    )


def _chat(
    client: OpenAI,
    model: str,
    system: str,
    user: str,
    *,
    temperature: float = 0.3,
    on_text: Callable[[str], None] | None = None,
) -> str:
    """``on_text``: stream the answer and hand it the text so far as it grows
    (``_send``); by default, wherever the caller asked a note's progress to be
    shown (``note_progress.showing``). An answer kept from before an
    interruption is handed over whole."""
    if on_text is None:
        on_text = note_progress.sink()

    def ask() -> str:
        resp, _model = _create(
            client, model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            **({"on_text": on_text} if on_text is not None else {}),
        )
        msg = resp.choices[0].message
        return (msg.content or "").strip()

    # A step carried on after an interruption reads what it was answered then.
    answer = model_answers.remembered(
        model_answers.request_key("chat", _provider_of(client), model, system, user, temperature), ask,
    )
    if on_text is not None:
        try:
            on_text(answer)
        except Exception:  # noqa: BLE001 - showing progress never stops the note
            logger.debug("progress callback failed", exc_info=True)
    return answer


def _image_to_base64_data_url(image_path: str) -> str:
    path = Path(image_path)
    suffix = path.suffix.lower().lstrip(".")
    mime = "jpeg" if suffix in {"jpg", "jpeg"} else suffix
    if mime not in {"jpeg", "png", "webp"}:
        mime = "jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:image/{mime};base64,{encoded}"


def _vision_chat(
    client: OpenAI,
    model: str,
    system: str,
    user_text: str,
    image_paths: list[str],
    *,
    temperature: float = 0.3,
) -> str:
    content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    for path in image_paths:
        data_url = _image_to_base64_data_url(path)
        content.append({
            "type": "image_url",
            "image_url": {"url": data_url},
        })
    def ask() -> str:
        resp, _model = _create(
            client, model, may_replace=False,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            temperature=temperature,
        )
        msg = resp.choices[0].message
        return (msg.content or "").strip()

    return model_answers.remembered(
        model_answers.request_key("vision_chat", _provider_of(client), model, system, user_text, temperature,
                                  images=image_paths),
        ask,
    )


def can_use_multimodal(provider: str | None) -> bool:
    return (provider or "").strip().lower() == "qwen"
