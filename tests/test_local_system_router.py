from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.local_system import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_local_system_routes_report_local_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("FLUENTFLOW_CONFIG_PATH", str(tmp_path / "config.json"))
    client = _client()

    health = client.get("/health").json()
    runtime = client.get("/runtime-config").json()

    assert health["status"] == "ok"
    assert health["runtime"]["execution"] == "local"
    # Declared, not inferred: a client routing between the two editions reads this
    # instead of guessing from an intake rejection.
    assert health["edition"] == "local"
    assert "local_path" in health["accepted_agent_inputs"]
    assert runtime["auth_mode"] == "open"
    assert runtime["allowed_stt_providers"] == ["local"]
    assert runtime["default_stt_provider"] == "local"
    assert "guest_trial" not in runtime
    assert "direct_oss_upload" not in runtime["features"]
    # The local processing router provides POST /jobs/{id}/retry, so the
    # capability is advertised (review round-1 P1.2: flag must track the route).
    assert runtime["features"]["job_retry_from_stored_source"] is True


def test_local_credentials_accept_only_user_owned_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("FLUENTFLOW_CONFIG_PATH", str(tmp_path / "config.json"))
    client = _client()

    response = client.post(
        "/credentials",
        json={
            "deepseek_api_key": "user-key",
            "hosted_api_key": "must-not-save",
        },
    )

    assert response.status_code == 200
    assert response.json()["deepseek_api_key_configured"] is True
    assert "hosted_api_key_configured" not in response.json()
    assert "user-key" not in str(response.json())


def test_credential_status_says_whether_the_visual_note_can_run(monkeypatch, tmp_path):
    # The start page warns "you will get no note" from this flag, so it has to
    # follow the key saved on the settings page, not only the environment.
    monkeypatch.setenv("FLUENTFLOW_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Pinned to the key channel: a checkout whose .env opts into the Claude Code
    # subscription would otherwise report the note as available with no key.
    monkeypatch.setenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", "api_key")
    client = _client()

    assert client.get("/credentials/status").json()["visual_note_available"] is False

    client.post("/credentials", json={"anthropic_api_key": "sk-ant-test"})

    assert client.get("/credentials/status").json()["visual_note_available"] is True


def test_runtime_config_names_this_checkout_and_its_interpreter(monkeypatch, tmp_path):
    # Requirement: the Agent page shows MCP config and a check command the user
    # can paste as-is, so it needs the real repo path and the Python running the
    # service, not placeholders.
    import sys
    from pathlib import Path

    monkeypatch.setenv("FLUENTFLOW_CONFIG_PATH", str(tmp_path / "config.json"))
    runtime = _client().get("/runtime-config").json()

    repo_root = Path(runtime["repo_root"])
    assert repo_root.is_absolute()
    assert (repo_root / "scripts" / "fluentflow_mcp_server.py").is_file()
    assert runtime["python_executable"] == sys.executable


# POST /credentials/check. Requirement: right after a first-time user pastes a
# key, say in plain Chinese whether it works: a wrong key, an empty account and
# an unreachable server are three different things to go and fix. The check
# uses the saved key and never echoes it back.

import httpx
import pytest

from backend.routers import local_system


def _fake_provider(monkeypatch, status=200, body=None, error=None):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        if error is not None:
            raise error
        return httpx.Response(status, json=body if body is not None else {}, request=httpx.Request(method, url))

    monkeypatch.setattr(local_system, "_http_request", fake_request)
    return calls


def _saved(monkeypatch, tmp_path, **secrets):
    monkeypatch.setenv("FLUENTFLOW_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = _client()
    if secrets:
        client.post("/credentials", json=secrets)
    return client


def test_key_check_says_a_working_deepseek_key_works(monkeypatch, tmp_path):
    client = _saved(monkeypatch, tmp_path, deepseek_api_key="sk-good")
    calls = _fake_provider(monkeypatch, body={"is_available": True, "balance_infos": []})

    result = client.post("/credentials/check", json={"provider": "deepseek"}).json()

    assert result["ok"] is True
    assert len(calls) == 1
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-good"
    assert "sk-good" not in str(result)


@pytest.mark.parametrize(
    ("provider", "secret", "status", "body", "reason", "phrase"),
    [
        ("deepseek", "deepseek_api_key", 401, {}, "invalid_key", "Key 不对"),
        ("deepseek", "deepseek_api_key", 402, {}, "no_balance", "余额不足"),
        ("deepseek", "deepseek_api_key", 200, {"is_available": False}, "no_balance", "余额不足"),
        ("anthropic", "anthropic_api_key", 401, {"error": {"message": "invalid x-api-key"}}, "invalid_key", "Key 不对"),
        (
            "anthropic", "anthropic_api_key", 400,
            {"error": {"message": "Your credit balance is too low to access the Anthropic API."}},
            "no_balance", "余额不足",
        ),
    ],
)
def test_key_check_tells_a_wrong_key_from_an_empty_account(monkeypatch, tmp_path, provider, secret, status, body, reason, phrase):
    client = _saved(monkeypatch, tmp_path, **{secret: "sk-test"})
    _fake_provider(monkeypatch, status=status, body=body)

    result = client.post("/credentials/check", json={"provider": provider}).json()

    assert result["ok"] is False
    assert result["reason"] == reason
    assert phrase in result["message"]


def test_key_check_says_when_the_server_cannot_be_reached(monkeypatch, tmp_path):
    client = _saved(monkeypatch, tmp_path, deepseek_api_key="sk-test")
    _fake_provider(monkeypatch, error=httpx.ConnectTimeout("timed out"))

    result = client.post("/credentials/check", json={"provider": "deepseek"}).json()

    assert result == {"ok": False, "reason": "network", "message": result["message"]}
    assert "网络" in result["message"]


def test_key_check_makes_one_tiny_anthropic_request(monkeypatch, tmp_path):
    client = _saved(monkeypatch, tmp_path, anthropic_api_key="sk-ant-good")
    calls = _fake_provider(monkeypatch, body={"content": []})

    result = client.post("/credentials/check", json={"provider": "anthropic"}).json()

    assert result["ok"] is True
    assert calls[0]["json"]["max_tokens"] == 1
    assert calls[0]["headers"]["x-api-key"] == "sk-ant-good"


def test_key_check_without_a_saved_key_sends_nothing(monkeypatch, tmp_path):
    client = _saved(monkeypatch, tmp_path)
    calls = _fake_provider(monkeypatch)

    result = client.post("/credentials/check", json={"provider": "deepseek"}).json()

    assert result["reason"] == "missing"
    assert calls == []
    assert client.post("/credentials/check", json={"provider": "nope"}).status_code == 400
