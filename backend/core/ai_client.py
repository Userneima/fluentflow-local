"""Model clients plus the chat/vision calls, and provider resolution.

Three providers (deepseek, openai, qwen) speak the OpenAI chat-completions wire
format and differ only by base URL. Anthropic does not, so a client carries the
provider whose format it speaks and every call dispatches on that tag rather
than inspecting the client object: type-sniffing would send a future fourth
provider down whichever branch happened to be the fallback.

Imports only ai_config + stdlib, so there is no circular import back to
ai_summarizer.
"""

from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from backend.core.ai_usage import record_token_usage
from backend.core.ai_config import (
    ANTHROPIC_PROVIDER,
    DEEPSEEK_BASE_URL,
    OPENAI_BASE_URL,
    QWEN_BASE_URL,
    DEFAULT_ANTHROPIC_MAX_TOKENS,
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_QWEN_MODEL,
    DEFAULT_QWEN_VISION_MODEL,
    DEFAULT_MODEL,
    SUPPORTED_PROVIDERS,
)

logger = logging.getLogger(__name__)

# Providers reached through the OpenAI SDK by pointing it at their base URL.
_OPENAI_COMPATIBLE_PROVIDERS = frozenset({"deepseek", "openai", "qwen"})


@dataclass(frozen=True)
class AiClient:
    """A model client together with the provider whose wire format it speaks."""

    provider: str
    raw: Any


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
    if provider == ANTHROPIC_PROVIDER:
        return (os.environ.get("ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL).strip()
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
    elif provider == ANTHROPIC_PROVIDER:
        env_name = "ANTHROPIC_API_KEY"
        env_names = (env_name,)
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


def anthropic_max_tokens() -> int:
    try:
        return max(int(os.environ.get("ANTHROPIC_MAX_TOKENS", str(DEFAULT_ANTHROPIC_MAX_TOKENS))), 1024)
    except ValueError:
        return DEFAULT_ANTHROPIC_MAX_TOKENS


def _anthropic_raw_client(api_key: str) -> Any:
    # Imported lazily so a checkout whose dependencies predate this provider
    # still boots and serves every other provider, instead of failing at import.
    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise ValueError(
            "未安装 anthropic 依赖：请执行 pip install -r requirements-local.txt 后重试。"
        ) from exc
    return Anthropic(api_key=api_key)


def _get_client(*, provider: str, api_key: str | None = None) -> AiClient:
    key = _provider_api_key(provider, api_key)
    if provider == ANTHROPIC_PROVIDER:
        return AiClient(provider=provider, raw=_anthropic_raw_client(key))
    return AiClient(provider=provider, raw=OpenAI(api_key=key, base_url=_provider_base_url(provider)))


def _image_to_base64(image_path: str) -> tuple[str, str]:
    """Return ``(mime_subtype, base64_data)`` for a frame image."""
    path = Path(image_path)
    suffix = path.suffix.lower().lstrip(".")
    mime = "jpeg" if suffix in {"jpg", "jpeg"} else suffix
    if mime not in {"jpeg", "png", "webp", "gif"}:
        mime = "jpeg"
    return mime, base64.b64encode(path.read_bytes()).decode("utf-8")


def _image_to_base64_data_url(image_path: str) -> str:
    mime, encoded = _image_to_base64(image_path)
    return f"data:image/{mime};base64,{encoded}"


def _anthropic_content(text: str, image_paths: list[str] | None = None) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for image_path in image_paths or []:
        mime, encoded = _image_to_base64(image_path)
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": f"image/{mime}", "data": encoded},
        })
    return content


def _anthropic_chat(raw: Any, model: str, system: str, content: list[dict[str, Any]]) -> str:
    """One Messages API turn, returned as plain text.

    Streamed on purpose: a long note under a high ``max_tokens`` can outlast the
    SDK's non-streaming HTTP timeout. Nothing here consumes the stream
    incrementally — the pipeline wants the finished text — so the events are
    collected and the final message read.

    ``temperature`` is absent by design. Current Claude models removed the
    sampling parameters and reject a request carrying one, so the pipeline's
    per-call temperatures cannot be forwarded; they were a determinism hint that
    never guaranteed identical output anyway. Thinking is likewise left
    unconfigured so the model's own default applies.
    """
    with raw.messages.stream(
        model=model,
        max_tokens=anthropic_max_tokens(),
        system=system,
        messages=[{"role": "user", "content": content}],
    ) as stream:
        message = stream.get_final_message()

    # Check why generation stopped before reading content: a refusal carries no
    # usable text, and treating it as an empty note would look like the model
    # simply had nothing to say.
    stop_reason = getattr(message, "stop_reason", None)
    if stop_reason == "refusal":
        raise ValueError("Claude 拒绝了这次请求：请检查转录内容或改用其他服务商。")
    if stop_reason == "max_tokens":
        logger.warning(
            "Anthropic note hit max_tokens (%s) and is truncated; raise ANTHROPIC_MAX_TOKENS.",
            anthropic_max_tokens(),
        )
    return "".join(
        getattr(block, "text", "") for block in message.content
        if getattr(block, "type", "") == "text"
    ).strip()


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


def _chat(
    client: AiClient,
    model: str,
    system: str,
    user: str,
    *,
    temperature: float = 0.3,
) -> str:
    if client.provider == ANTHROPIC_PROVIDER:
        return _anthropic_chat(client.raw, model, system, _anthropic_content(user))
    resp = client.raw.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
    )
    record_token_usage(provider=_provider_of(client), model=model, response=resp)
    msg = resp.choices[0].message
    return (msg.content or "").strip()


def _vision_chat(
    client: AiClient,
    model: str,
    system: str,
    user_text: str,
    image_paths: list[str],
    *,
    temperature: float = 0.3,
) -> str:
    if client.provider == ANTHROPIC_PROVIDER:
        return _anthropic_chat(
            client.raw, model, system, _anthropic_content(user_text, image_paths)
        )
    content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    for path in image_paths:
        data_url = _image_to_base64_data_url(path)
        content.append({
            "type": "image_url",
            "image_url": {"url": data_url},
        })
    resp = client.raw.chat.completions.create(
        model=model,
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
    return (provider or "").strip().lower() in {"qwen", ANTHROPIC_PROVIDER}


def vision_model(provider: str | None, model: str | None = None) -> str:
    """The model to use for a call that includes images.

    Qwen splits text and vision across separate models, so a vision call there
    must switch models or a text-only model silently drops the images. Claude
    models are multimodal, so the configured note model is used unchanged —
    which is why this decision lives here rather than being inlined at each
    call site with one provider's environment variable hardcoded.
    """
    name = _normalize_provider(provider)
    if name == "qwen":
        return _normalize_model(
            name, model or os.environ.get("QWEN_VISION_MODEL") or DEFAULT_QWEN_VISION_MODEL
        )
    return _normalize_model(name, model)
