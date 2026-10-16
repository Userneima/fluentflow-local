"""OpenAI-compatible client + chat/vision calls and provider resolution,
extracted from ai_summarizer.py. Imports only ai_config + stdlib, so there is
no circular import back to ai_summarizer."""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from openai import OpenAI

from backend.core.ai_usage import record_token_usage
from backend.core.ai_config import (
    DEEPSEEK_BASE_URL,
    OPENAI_BASE_URL,
    QWEN_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    RETIRED_DEEPSEEK_MODELS,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_QWEN_MODEL,
    SUPPORTED_PROVIDERS,
)


logger = logging.getLogger(__name__)

def _normalize_provider(provider: str | None) -> str:
    p = (provider or os.environ.get("AI_PROVIDER") or "deepseek").strip().lower()
    if p not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported AI provider: {provider}")
    return p


def _provider_base_url(provider: str) -> str:
    if provider == "openai":
        return (os.environ.get("OPENAI_BASE_URL") or OPENAI_BASE_URL).rstrip("/")
    if provider == "qwen":
        return (os.environ.get("QWEN_BASE_URL") or QWEN_BASE_URL).rstrip("/")
    return (os.environ.get("DEEPSEEK_BASE_URL") or DEEPSEEK_BASE_URL).rstrip("/")


_BUILT_IN_DEFAULTS = {"openai": DEFAULT_OPENAI_MODEL, "qwen": DEFAULT_QWEN_MODEL, "deepseek": DEFAULT_DEEPSEEK_MODEL}
_MODEL_ENV = {"openai": "OPENAI_MODEL", "qwen": "QWEN_MODEL", "deepseek": "DEEPSEEK_MODEL"}


def _provider_default_model(provider: str) -> str:
    """The provider's default: a model named in ``.env``, else the
    recommended-model table (``model_defaults``), else this build's own."""
    from backend.core import model_defaults

    key = provider if provider in _BUILT_IN_DEFAULTS else "deepseek"
    named = (os.environ.get(_MODEL_ENV[key]) or "").strip()
    chosen = named or model_defaults.default_model(key) or _BUILT_IN_DEFAULTS[key]
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
    if provider == "openai":
        env_name = "OPENAI_API_KEY"
        env_names = (env_name,)
    elif provider == "qwen":
        env_name = "DASHSCOPE_API_KEY"
        env_names = ("DASHSCOPE_API_KEY", "QWEN_API_KEY")
    else:
        env_name = "DEEPSEEK_API_KEY"
        env_names = (env_name,)
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

    base = str(getattr(client, "base_url", "") or "")
    if "dashscope" in base:
        return "qwen"
    if "deepseek" in base:
        return "deepseek"
    if base:
        return "openai"
    return "unknown"


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
        return client.chat.completions.create(model=model, **kwargs), model
    except Exception as exc:
        if not _model_gone(exc):
            raise
        # The defaults are text models: a request with pictures is not moved onto one.
        fallback = _provider_default_model(provider) if may_replace and provider in SUPPORTED_PROVIDERS else ""
        listed = _listed_models(client)
        if not fallback or fallback == model or (listed is not None and fallback not in listed):
            raise RuntimeError(
                f"模型 {model} 已不在服务商的可用列表里，请在设置页换一个模型。"
                + (f"现在可用的有：{'、'.join(sorted(listed)[:8])}" if listed else "")
            ) from exc
        logger.warning("model %s is gone at %s; using %s", model, provider, fallback)
        _REPLACED[(provider, model)] = fallback
        return client.chat.completions.create(model=fallback, **kwargs), fallback


def _chat(
    client: OpenAI,
    model: str,
    system: str,
    user: str,
    *,
    temperature: float = 0.3,
) -> str:
    resp, model = _create(
        client, model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
    )
    record_token_usage(provider=_provider_of(client), model=model, response=resp)
    msg = resp.choices[0].message
    return (msg.content or "").strip()


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
    resp, model = _create(
        client, model, may_replace=False,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        temperature=temperature,
    )
    record_token_usage(provider=_provider_of(client), model=model, response=resp)
    msg = resp.choices[0].message
    return (msg.content or "").strip()


def can_use_multimodal(provider: str | None) -> bool:
    return (provider or "").strip().lower() == "qwen"
