"""OpenAI-compatible client + chat/vision calls and provider resolution,
extracted from ai_summarizer.py. Imports only ai_config + stdlib, so there is
no circular import back to ai_summarizer."""

from __future__ import annotations

import os
import base64
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from backend.core.ai_config import (
    DEEPSEEK_BASE_URL,
    OPENAI_BASE_URL,
    QWEN_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_QWEN_MODEL,
    DEFAULT_MODEL,
    SUPPORTED_PROVIDERS,
)


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


def _provider_default_model(provider: str) -> str:
    if provider == "openai":
        return (os.environ.get("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL).strip()
    if provider == "qwen":
        return _normalize_model(provider, os.environ.get("QWEN_MODEL") or DEFAULT_QWEN_MODEL)
    return _normalize_model(provider, os.environ.get("DEEPSEEK_MODEL") or DEFAULT_DEEPSEEK_MODEL)


def _normalize_model(provider: str, model: str | None) -> str:
    value = (model or "").strip()
    if provider == "deepseek" and (not value or value == "deepseek-chat"):
        return DEFAULT_DEEPSEEK_MODEL
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


def _get_client(*, provider: str, api_key: str | None = None) -> OpenAI:
    key = _provider_api_key(provider, api_key)
    return OpenAI(api_key=key, base_url=_provider_base_url(provider))


def _chat(
    client: OpenAI,
    model: str,
    system: str,
    user: str,
    *,
    temperature: float = 0.3,
) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
    )
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
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        temperature=temperature,
    )
    msg = resp.choices[0].message
    return (msg.content or "").strip()


def can_use_multimodal(provider: str | None) -> bool:
    return (provider or "").strip().lower() == "qwen"
