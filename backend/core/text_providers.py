"""The text-model providers a note can be written with, in one table.

Every provider used to be spelled out in each place that needed it: the
address in ai_config, the key names in local_config, the key order in
local_entry_guards, the model filter in local_system, the labels on the page.
Adding Kimi and MiMo (owner, 2026-10-18: users in China rarely get an
Anthropic key, and these are what they have) is one row here.

All of them speak the OpenAI Chat Completions protocol at ``base_url``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from backend.core.ai_config import (
    DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_QWEN_MODEL,
    OPENAI_BASE_URL,
    QWEN_BASE_URL,
)


@dataclass(frozen=True)
class TextProvider:
    id: str
    label: str
    base_url: str
    # Overrides the address, e.g. a relay or the international endpoint.
    base_url_env: str
    # Where the key is stored (settings page) and read from (.env), in order.
    key_settings: tuple[str, ...]
    key_envs: tuple[str, ...]
    default_model: str
    model_env: str
    # Which ids from the provider's model list are text models worth offering.
    lists_model: Callable[[str], bool]


PROVIDERS: dict[str, TextProvider] = {
    provider.id: provider
    for provider in (
        TextProvider(
            id="deepseek", label="DeepSeek",
            base_url=DEEPSEEK_BASE_URL, base_url_env="DEEPSEEK_BASE_URL",
            key_settings=("deepseek_api_key",), key_envs=("DEEPSEEK_API_KEY",),
            default_model=DEFAULT_DEEPSEEK_MODEL, model_env="DEEPSEEK_MODEL",
            lists_model=lambda name: True,
        ),
        TextProvider(
            id="openai", label="OpenAI",
            base_url=OPENAI_BASE_URL, base_url_env="OPENAI_BASE_URL",
            key_settings=("openai_api_key",), key_envs=("OPENAI_API_KEY",),
            default_model=DEFAULT_OPENAI_MODEL, model_env="OPENAI_MODEL",
            lists_model=lambda name: name.startswith(("gpt-", "o")),
        ),
        TextProvider(
            id="qwen", label="通义千问（阿里云百炼）",
            base_url=QWEN_BASE_URL, base_url_env="QWEN_BASE_URL",
            key_settings=("qwen_api_key", "dashscope_api_key"), key_envs=("DASHSCOPE_API_KEY", "QWEN_API_KEY"),
            default_model=DEFAULT_QWEN_MODEL, model_env="QWEN_MODEL",
            lists_model=lambda name: name.startswith("qwen"),
        ),
        # Moonshot's mainland platform; a key from the international one
        # (api.moonshot.ai) does not work here and needs KIMI_BASE_URL.
        TextProvider(
            id="kimi", label="Kimi（月之暗面）",
            base_url="https://api.moonshot.cn/v1", base_url_env="KIMI_BASE_URL",
            key_settings=("kimi_api_key",), key_envs=("KIMI_API_KEY", "MOONSHOT_API_KEY"),
            default_model="kimi-k2.6", model_env="KIMI_MODEL",
            lists_model=lambda name: name.startswith("kimi"),
        ),
        # Xiaomi's own platform (docs, 2026-10-18: OpenAI-compatible at this
        # address; the V2.5 names move to V2.6 and retire on 2026-10-21).
        TextProvider(
            id="mimo", label="小米 MiMo",
            base_url="https://api.xiaomimimo.com/v1", base_url_env="MIMO_BASE_URL",
            key_settings=("mimo_api_key",), key_envs=("MIMO_API_KEY",),
            default_model="mimo-v2.6-pro", model_env="MIMO_MODEL",
            lists_model=lambda name: name.startswith("mimo"),
        ),
    )
}

# The order a key is looked for when no provider was chosen. The first three
# keep the order this build always used, so nobody's notes move to another
# provider because a key was added for something else.
KEY_LOOKUP_ORDER: tuple[str, ...] = ("deepseek", "openai", "qwen", "kimi", "mimo")


def get(provider_id: str) -> TextProvider | None:
    return PROVIDERS.get((provider_id or "").strip().lower())


def base_url(provider: TextProvider) -> str:
    return (os.environ.get(provider.base_url_env) or provider.base_url).rstrip("/")


def all_key_settings() -> tuple[str, ...]:
    return tuple(name for provider in PROVIDERS.values() for name in provider.key_settings)


__all__ = ["KEY_LOOKUP_ORDER", "PROVIDERS", "TextProvider", "all_key_settings", "base_url", "get"]
