"""Remembered local preferences and the miuistore consent plumbing."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.local_config import get_preference, load_preferences, save_preferences
import backend.routers.local_system as local_system
import backend.routers.local_video_sources as local_video_sources
from backend.routers.local_system import router as system_router


def test_preferences_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    assert load_preferences(path) == {}
    saved = save_preferences({"allow_miuistore": "true", "unknown": "x"}, path)
    assert saved == {"allow_miuistore": True}
    assert get_preference("allow_miuistore", path) is True
    assert get_preference("unknown", path) is None
    # None clears the stored choice.
    assert save_preferences({"allow_miuistore": None}, path) == {}


def test_preferences_do_not_clobber_secrets(tmp_path):
    path = tmp_path / "config.json"
    from backend.core.local_config import save_sensitive_settings, get_sensitive_setting

    save_sensitive_settings({"deepseek_api_key": "ds"}, path)
    save_preferences({"allow_miuistore": True}, path)
    assert get_sensitive_setting("deepseek_api_key", path) == "ds"
    assert get_preference("allow_miuistore", path) is True


def test_preferences_routes(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    monkeypatch.setattr(local_system, "load_preferences", lambda: load_preferences(path))
    monkeypatch.setattr(local_system, "save_preferences", lambda patch: save_preferences(patch, path))
    app = FastAPI()
    app.include_router(system_router)
    client = TestClient(app)

    r = client.post("/preferences", json={"allow_miuistore": True})
    assert r.status_code == 200
    assert r.json()["preferences"] == {"allow_miuistore": True}
    assert client.get("/preferences").json()["preferences"] == {"allow_miuistore": True}
    assert client.post("/preferences", json={"nope": 1}).status_code == 400


def test_submit_uses_saved_consent_when_request_is_silent(monkeypatch):
    seen: dict = {}

    async def fake_scenario():
        monkeypatch.setattr(local_video_sources, "get_preference", lambda name: True)
        monkeypatch.setattr(local_video_sources, "claim_task_id", lambda *a, **k: "t-1")
        monkeypatch.setattr(local_video_sources, "log_event", lambda **v: None)
        monkeypatch.setattr(local_video_sources, "upsert_job", lambda **v: None)

        async def fake_start(task_id, runner):
            seen["started"] = task_id

        monkeypatch.setattr(local_video_sources.JOB_EVENTS, "start", fake_start)
        job = await local_video_sources.submit_video_source_job(
            input_text="https://example.com/v/1",
            title="",
            raw_options={},  # no per-request choice → saved preference applies
            client_id="desktop-a",
        )
        seen["metadata"] = job["metadata"]

    import asyncio

    asyncio.run(fake_scenario())
    assert seen["metadata"]["video_source_allow_miuistore"] is True


def test_explicit_request_choice_overrides_saved(monkeypatch):
    seen: dict = {}

    async def fake_scenario():
        monkeypatch.setattr(
            local_video_sources, "get_preference",
            lambda name: (_ for _ in ()).throw(AssertionError("saved choice must not be read")),
        )
        monkeypatch.setattr(local_video_sources, "claim_task_id", lambda *a, **k: "t-2")
        monkeypatch.setattr(local_video_sources, "log_event", lambda **v: None)
        monkeypatch.setattr(local_video_sources, "upsert_job", lambda **v: None)

        async def fake_start(task_id, runner):
            return None

        monkeypatch.setattr(local_video_sources.JOB_EVENTS, "start", fake_start)
        job = await local_video_sources.submit_video_source_job(
            input_text="抖音分享文本",
            title="",
            raw_options={"allow_miuistore": "0"},  # explicit opt-out wins
            client_id="desktop-a",
        )
        seen["metadata"] = job["metadata"]

    import asyncio

    asyncio.run(fake_scenario())
    assert seen["metadata"]["video_source_allow_miuistore"] is False
