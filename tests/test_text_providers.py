"""Kimi and MiMo write notes like the other text providers, from one table.

Owner, 2026-10-18: most users in China cannot get an Anthropic key; Qwen,
Kimi and MiMo are what they have. Each provider is one row in
``text_providers``; everything that needs a provider reads that row.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from backend.core import ai_client, ai_config, local_config, text_providers
from backend.core import local_entry_guards as guards


def test_the_table_and_the_supported_set_agree():
    assert set(text_providers.PROVIDERS) == ai_config.SUPPORTED_PROVIDERS
    assert set(text_providers.KEY_LOOKUP_ORDER) == set(text_providers.PROVIDERS)


@pytest.mark.parametrize("provider, address, model", [
    ("kimi", "https://api.moonshot.cn/v1", "kimi-k2.6"),
    ("mimo", "https://api.xiaomimimo.com/v1", "mimo-v2.6-pro"),
])
def test_kimi_and_mimo_are_called_at_their_own_address(provider, address, model, monkeypatch):
    monkeypatch.delenv(text_providers.PROVIDERS[provider].base_url_env, raising=False)
    monkeypatch.delenv(text_providers.PROVIDERS[provider].model_env, raising=False)
    assert ai_client._normalize_provider(provider) == provider
    assert ai_client._provider_base_url(provider) == address
    assert ai_client._normalize_model(provider, None) == model


def test_an_address_can_be_overridden(monkeypatch):
    monkeypatch.setenv("KIMI_BASE_URL", "https://api.moonshot.ai/v1/")
    assert ai_client._provider_base_url("kimi") == "https://api.moonshot.ai/v1"


def test_the_moonshot_key_name_is_read_too(monkeypatch):
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-moon")
    monkeypatch.setattr(ai_client, "load_dotenv", lambda: None)
    assert ai_client._provider_api_key("kimi") == "sk-moon"


def test_a_stored_kimi_key_goes_to_kimi_only(monkeypatch):
    stored = {"kimi_api_key": "sk-kimi"}
    monkeypatch.setattr(guards, "resolve_secret", lambda form_value, name: (form_value or "").strip() or stored.get(name))
    assert guards.local_ai_kwargs(ai_provider="kimi")["api_key"] == "sk-kimi"
    assert "api_key" not in guards.local_ai_kwargs(ai_provider="mimo")
    # With nothing chosen, the only key there is picks the provider.
    assert guards.local_ai_kwargs() == {"provider": "kimi", "api_key": "sk-kimi"}


def test_the_settings_page_can_store_both_keys():
    for name in ("kimi_api_key", "mimo_api_key"):
        assert name in local_config.LOCAL_SENSITIVE_FIELDS
    status = local_config.credential_status(Path("does-not-exist.json"))
    assert "kimi_api_key_configured" in status and "mimo_api_key_configured" in status


def test_the_recommended_table_names_a_default_for_each():
    table = json.loads(Path(text_providers.__file__).with_name("recommended_models.json").read_text(encoding="utf-8"))
    for provider in text_providers.PROVIDERS:
        assert table["providers"][provider]["text"], provider


def test_a_kimi_key_is_checked_by_listing_its_models(monkeypatch):
    from backend.routers import local_system

    seen = {}

    def answer(method, url, headers=None, timeout=None, **_kw):
        seen["url"], seen["auth"] = url, headers.get("Authorization")
        return httpx.Response(401, request=httpx.Request(method, url))

    monkeypatch.setattr(local_system, "_http_request", answer)
    monkeypatch.setattr(local_system, "resolve_secret", lambda _form, name: "sk-bad" if name == "kimi_api_key" else None)
    result = local_system.check_credential({"provider": "kimi"})
    assert seen["url"].endswith("/models") and seen["auth"] == "Bearer sk-bad"
    assert result["ok"] is False and result["reason"] == "invalid_key" and "Kimi" in result["message"]


def test_mimo_models_are_listed_for_the_settings_page(monkeypatch):
    from backend.routers import local_system

    def answer(method, url, headers=None, timeout=None, **_kw):
        body = {"data": [{"id": "mimo-v2.6-pro", "created": 2}, {"id": "embedding-x", "created": 3}]}
        return httpx.Response(200, json=body, request=httpx.Request(method, url))

    monkeypatch.setattr(local_system, "_http_request", answer)
    monkeypatch.setattr(local_system, "resolve_secret", lambda _form, name: "sk-mimo" if name == "mimo_api_key" else None)
    assert local_system.list_provider_models("mimo") == {"models": ["mimo-v2.6-pro"], "reason": "ok"}
