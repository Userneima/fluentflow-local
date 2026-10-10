"""The settings page's model suggestions (2026-10-10).

Requirements: a text provider's current models are listed newest first, read
with the saved key, so models released after this build can be picked; with no
key, or when the provider cannot be reached, the list is empty and the page
still works.
"""

from __future__ import annotations

import httpx

from backend.routers import local_system


def _response(status, body):
    return httpx.Response(status, json=body, request=httpx.Request("GET", "https://x/models"))


def test_models_are_listed_newest_first_with_the_saved_key(monkeypatch):
    seen = {}
    monkeypatch.setattr(local_system, "resolve_secret", lambda value, name: "sk-test" if name == "openai_api_key" else None)

    def request(method, url, **kwargs):
        seen["auth"] = kwargs["headers"]["Authorization"]
        return _response(200, {"data": [
            {"id": "gpt-5.4-mini", "created": 100}, {"id": "gpt-9-mini", "created": 300},
            {"id": "text-embedding-3", "created": 400}, {"id": "gpt-9", "created": 290},
        ]})

    monkeypatch.setattr(local_system, "_http_request", request)
    result = local_system.list_provider_models("openai")
    assert result["models"] == ["gpt-9-mini", "gpt-9", "gpt-5.4-mini"]
    assert seen["auth"] == "Bearer sk-test"


def test_no_key_or_no_network_gives_an_empty_list(monkeypatch):
    monkeypatch.setattr(local_system, "resolve_secret", lambda value, name: None)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert local_system.list_provider_models("deepseek") == {"models": [], "reason": "missing"}

    monkeypatch.setattr(local_system, "resolve_secret", lambda value, name: "sk")

    def offline(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(local_system, "_http_request", offline)
    assert local_system.list_provider_models("deepseek")["models"] == []
