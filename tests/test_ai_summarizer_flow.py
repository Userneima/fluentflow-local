"""What the user gets back from the note writer, and what they are told when
the provider refuses.

The deadline guard, the empty-revision guard and the planner's coverage
report live in ``tests/test_ai_summarizer_guards.py``; this file covers the
plain path (a note comes back with its sections and the record says which
provider, model and mode produced it) and the failure path (a provider error
reaches the user as a Chinese sentence that names the cause).
"""

from __future__ import annotations

import httpx
import openai
import pytest

import backend.core.ai_client as ai_client
import backend.core.ai_summarizer as summ
from backend.core.local_entry_guards import friendly_error
from backend.core.local_error_diagnostics import diagnose_error

NOTE = (
    "## 一、开场\n\n课程要解决的问题是什么。\n\n"
    "## 二、核心方法\n\n三步法的每一步。\n\n"
    "## 三、结尾\n\n下一讲的预告。\n"
)
TRANSCRIPT = "今天这一讲先说问题，再说方法，最后预告下一讲。" * 20


def _has_chinese(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


@pytest.fixture()
def recorded_provider(monkeypatch):
    """No network: the client is a stand-in and the chat answers a fixed note,
    while what was asked for (provider, model) is recorded."""
    seen: dict = {}

    def fake_client(*, provider, api_key=None):
        seen["provider"] = provider
        seen["api_key"] = api_key
        return object()

    def fake_chat(client, model, system, user, *, temperature=0.3):
        seen.setdefault("models", []).append(model)
        seen.setdefault("prompts", []).append(system)
        return NOTE

    monkeypatch.setattr(summ, "_get_client", fake_client)
    monkeypatch.setattr(summ, "_chat", fake_chat)
    monkeypatch.setattr(summ, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("FLUENTFLOW_NOTE_MODE", raising=False)
    monkeypatch.delenv("FLUENTFLOW_NOTE_DEADLINE_SECONDS", raising=False)
    return seen


# ── the plain path ──────────────────────────────────────────────────────────

def test_a_short_transcript_gets_a_note_with_its_sections_and_a_record_of_how(recorded_provider):
    result = summ.summarize_transcript_with_metadata(
        TRANSCRIPT, provider="deepseek", model="deepseek-reasoner", api_key="sk-test", note_mode="auto"
    )

    for heading in ("## 一、开场", "## 二、核心方法", "## 三、结尾"):
        assert heading in result.markdown
    assert recorded_provider["provider"] == "deepseek"
    assert recorded_provider["api_key"] == "sk-test"
    assert recorded_provider["models"] == ["deepseek-reasoner"], "the model the user picked is the one called"
    assert result.requested_mode == "auto"
    assert result.resolved_mode == "direct", "a transcript this short is written in one call"
    assert result.chunk_count == 1
    assert result.transcript_length == len(TRANSCRIPT)


def test_the_providers_default_model_is_used_when_none_is_chosen(recorded_provider):
    summ.summarize_transcript_with_metadata(TRANSCRIPT, provider="qwen")

    assert recorded_provider["provider"] == "qwen"
    assert recorded_provider["models"] == [ai_client._provider_default_model("qwen")]


def test_the_users_own_prompt_reaches_the_model(recorded_provider):
    summ.summarize_transcript_with_metadata(TRANSCRIPT, provider="deepseek", system_prompt="只写三条要点。")

    assert any("只写三条要点。" in prompt for prompt in recorded_provider["prompts"])


def test_an_empty_transcript_produces_no_note_and_calls_nobody(recorded_provider):
    result = summ.summarize_transcript_with_metadata("   \n ", provider="deepseek")

    assert result.markdown == "" and result.chunk_count == 0
    assert "models" not in recorded_provider


def test_an_unknown_note_mode_is_refused_before_any_call(recorded_provider):
    with pytest.raises(ValueError, match="note generation mode"):
        summ.summarize_transcript_with_metadata(TRANSCRIPT, provider="deepseek", note_mode="poetry")

    assert "models" not in recorded_provider


# ── the provider refuses ────────────────────────────────────────────────────

def _provider_error(cls, status: int, message: str) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    response = httpx.Response(status, request=request)
    return cls(message, response=response, body={"error": {"message": message}})


def _refusing_chat(error: Exception):
    def chat(*_a, **_k):
        raise error

    return chat


OPENAI_401 = (
    "Error code: 401 - {'error': {'message': 'Incorrect API key provided: sk-abc. You can find your "
    "API key at https://platform.openai.com/account/api-keys.', 'type': 'invalid_request_error', "
    "'code': 'invalid_api_key'}}"
)
# DeepSeek's wording for the same failure (its 401 body).
DEEPSEEK_401 = (
    "Error code: 401 - {'error': {'message': 'Authentication Fails, Your api key: ****abcd is invalid', "
    "'type': 'authentication_error', 'code': 'invalid_request_error'}}"
)


def _rejected_key(monkeypatch, message: str) -> str:
    monkeypatch.setattr(summ, "_chat", _refusing_chat(_provider_error(openai.AuthenticationError, 401, message)))
    with pytest.raises(openai.AuthenticationError) as caught:
        summ.summarize_transcript_with_metadata(TRANSCRIPT, provider="deepseek")
    return friendly_error(caught.value)


def test_a_rejected_api_key_is_explained_in_chinese_and_names_the_key(recorded_provider, monkeypatch):
    """The note fails, the transcript stays, and the user is told it was the
    key — not shown an English stack of JSON."""
    shown = _rejected_key(monkeypatch, OPENAI_401)

    assert _has_chinese(shown)
    assert "API Key" in shown
    assert "Error code" not in shown, "the raw provider payload is not what the user reads"
    assert diagnose_error(OPENAI_401)["code"] == "invalid_api_key"


def test_a_key_rejected_in_deepseeks_words_is_explained_the_same_way(recorded_provider, monkeypatch):
    shown = _rejected_key(monkeypatch, DEEPSEEK_401)

    assert _has_chinese(shown) and "API Key" in shown
    assert "Error code" not in shown


def test_a_rate_limited_provider_is_explained_in_chinese_as_rate_limiting(recorded_provider, monkeypatch):
    message = (
        "Error code: 429 - {'error': {'message': 'Rate limit reached for deepseek-chat: "
        "Limit 60, Used 60, Requested 1. Please try again in 1s.', 'type': 'rate_limit_error'}}"
    )
    monkeypatch.setattr(summ, "_chat", _refusing_chat(_provider_error(openai.RateLimitError, 429, message)))

    with pytest.raises(openai.RateLimitError) as caught:
        summ.summarize_transcript_with_metadata(TRANSCRIPT, provider="deepseek")

    shown = friendly_error(caught.value)
    assert _has_chinese(shown)
    assert any(word in shown for word in ("频繁", "限流", "额度", "稍后")), shown
    assert "Error code" not in shown


def test_a_missing_api_key_is_explained_in_chinese_before_any_call(monkeypatch):
    """No key anywhere: the message names the setting to fill in, in Chinese,
    and nothing was sent to the provider."""
    monkeypatch.setattr(ai_client, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(summ, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    called = []
    monkeypatch.setattr(summ, "_chat", lambda *a, **k: called.append(1))

    with pytest.raises(ValueError) as caught:
        summ.summarize_transcript_with_metadata(TRANSCRIPT, provider="deepseek")

    shown = friendly_error(caught.value)
    assert _has_chinese(shown) and "DEEPSEEK_API_KEY" in shown and "API Key" in shown
    assert called == []
