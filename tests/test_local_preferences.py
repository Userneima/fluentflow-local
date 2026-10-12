"""Remembered local preferences."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.local_config import get_preference, load_preferences, save_preferences
import backend.routers.local_system as local_system
from backend.routers.local_system import router as system_router


def test_preferences_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    assert load_preferences(path) == {}
    saved = save_preferences({"video_cookies_browser": "Safari", "unknown": "x"}, path)
    assert saved == {"video_cookies_browser": "safari"}
    assert get_preference("video_cookies_browser", path) == "safari"
    assert get_preference("unknown", path) is None
    # None clears the stored choice.
    assert save_preferences({"video_cookies_browser": None}, path) == {}


def test_preferences_do_not_clobber_secrets(tmp_path):
    path = tmp_path / "config.json"
    from backend.core.local_config import save_sensitive_settings, get_sensitive_setting

    save_sensitive_settings({"deepseek_api_key": "ds"}, path)
    save_preferences({"video_cookies_browser": "chrome"}, path)
    assert get_sensitive_setting("deepseek_api_key", path) == "ds"
    assert get_preference("video_cookies_browser", path) == "chrome"


def test_preferences_routes(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    monkeypatch.setattr(local_system, "load_preferences", lambda: load_preferences(path))
    monkeypatch.setattr(local_system, "save_preferences", lambda patch: save_preferences(patch, path))
    app = FastAPI()
    app.include_router(system_router)
    client = TestClient(app)

    r = client.post("/preferences", json={"video_cookies_browser": "chrome"})
    assert r.status_code == 200
    assert r.json()["preferences"] == {"video_cookies_browser": "chrome"}
    assert client.get("/preferences").json()["preferences"] == {"video_cookies_browser": "chrome"}
    assert client.post("/preferences", json={"nope": 1}).status_code == 400
