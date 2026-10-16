"""Each text provider's default comes from one table that can change without a
release.

Requirements (owner, 2026-10-16):
1. The defaults are read from the recommended-model table; a model named in
   .env still wins.
2. The table on GitHub is read at most once a day, in the background; a good
   copy is kept and used next time, offline included. An unreadable one is
   ignored and the copy shipped with the build stands.
3. Nothing about the user is sent: the request carries no data.
"""

from __future__ import annotations

import json

import httpx
import pytest

from backend.core import ai_client, model_defaults


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("FLUENTFLOW_MODEL_TABLE_CACHE", str(tmp_path / "table.json"))
    for name in ("DEEPSEEK_MODEL", "OPENAI_MODEL", "QWEN_MODEL"):
        monkeypatch.delenv(name, raising=False)
    model_defaults._reset_for_tests()
    yield
    model_defaults._reset_for_tests()


def test_the_bundled_table_gives_each_provider_a_default():
    assert ai_client._provider_default_model("deepseek") == "deepseek-flash"
    assert ai_client._provider_default_model("openai") == "gpt-6-luna"
    assert model_defaults.default_model("qwen", "vision") == "qwen3.7-plus"


def test_a_model_named_in_env_still_wins(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-mine")
    assert ai_client._provider_default_model("openai") == "gpt-mine"


def test_a_table_fetched_from_github_changes_the_default_and_is_kept(monkeypatch, tmp_path):
    seen = []

    def fake_get(url, **kwargs):
        seen.append((url, kwargs))
        return httpx.Response(200, json={"providers": {"deepseek": {"text": "deepseek-flash-2"}}})

    monkeypatch.setenv(model_defaults.URL_ENV, "https://example.test/table.json")
    monkeypatch.setattr(model_defaults.httpx, "get", fake_get)
    model_defaults._refresh()

    assert ai_client._provider_default_model("deepseek") == "deepseek-flash-2"
    assert ai_client._provider_default_model("openai") == "gpt-6-luna", "a provider the table leaves out keeps the bundled one"
    assert json.loads((tmp_path / "table.json").read_text())["providers"]["deepseek"]["text"] == "deepseek-flash-2"
    assert set(seen[0][1]) <= {"timeout", "follow_redirects"}, "no headers, no body: nothing about the user"

    model_defaults._reset_for_tests()
    monkeypatch.setattr(model_defaults.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("offline")))
    assert ai_client._provider_default_model("deepseek") == "deepseek-flash-2", "offline, the kept copy is used"


def test_an_unreadable_table_is_ignored(monkeypatch):
    monkeypatch.setenv(model_defaults.URL_ENV, "https://example.test/table.json")
    monkeypatch.setattr(model_defaults.httpx, "get", lambda *a, **k: httpx.Response(200, json={"providers": "nonsense"}))
    model_defaults._refresh()
    assert ai_client._provider_default_model("deepseek") == "deepseek-flash"


def test_the_table_is_checked_at_most_once_a_day(monkeypatch):
    started = []
    monkeypatch.setattr(model_defaults.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: started.append(1)})())
    model_defaults.table(now=1_000_000)
    model_defaults.table(now=1_000_000 + 3600)
    model_defaults.table(now=1_000_000 + 25 * 3600)
    assert len(started) == 2
