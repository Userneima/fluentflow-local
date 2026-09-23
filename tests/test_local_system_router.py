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
