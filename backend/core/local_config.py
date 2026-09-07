"""Local backend-owned configuration for sensitive FluentFlow settings."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.core.runtime_paths import default_config_path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = default_config_path()
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"

LOCAL_SENSITIVE_FIELDS = {
    "deepseek_api_key",
    "openai_api_key",
    "dashscope_api_key",
    "qwen_api_key",
    # The user's own Anthropic key, used only by the visual-note entry. Kept a
    # separate field rather than folded into the note-provider keys because it
    # buys a different capability: without it that entry refuses and says so,
    # instead of quietly producing a subtitles-only note.
    "anthropic_api_key",
    "lark_app_id",
    "lark_app_secret",
    "pyannote_auth_token",
}

LOCAL_ENV_FALLBACKS = {
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "dashscope_api_key": "DASHSCOPE_API_KEY",
    "qwen_api_key": "QWEN_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "lark_app_id": "LARK_APP_ID",
    "lark_app_secret": "LARK_APP_SECRET",
    "pyannote_auth_token": "PYANNOTE_AUTH_TOKEN",
}

SECRET_ALIASES = {
    "dashscope_api_key": ("dashscope_api_key", "qwen_api_key"),
    "qwen_api_key": ("qwen_api_key", "dashscope_api_key"),
}

ENV_ALIAS_FALLBACKS = {
    "dashscope_api_key": ("DASHSCOPE_API_KEY", "QWEN_API_KEY"),
    "qwen_api_key": ("QWEN_API_KEY", "DASHSCOPE_API_KEY"),
}


def load_project_env() -> None:
    if DEFAULT_ENV_PATH.exists():
        load_dotenv(DEFAULT_ENV_PATH, override=False)


def config_path() -> Path:
    override = os.environ.get("FLUENTFLOW_CONFIG_PATH")
    return Path(override).expanduser() if override else DEFAULT_CONFIG_PATH


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    target = Path(path) if path else config_path()
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_secret_patch(
    patch: dict[str, Any],
    *,
    allowed_fields: set[str],
    path: Path | str | None = None,
) -> None:
    target = Path(path) if path else config_path()
    current = load_config(target)
    secrets = current.get("secrets") if isinstance(current.get("secrets"), dict) else {}
    next_secrets = dict(secrets)
    for key in allowed_fields:
        if key not in patch:
            continue
        value = patch.get(key)
        if value is None or str(value) == "":
            next_secrets.pop(key, None)
        else:
            next_secrets[key] = str(value)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({**current, "secrets": next_secrets}, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    try:
        target.chmod(0o600)
    except OSError:
        pass


def save_sensitive_settings(patch: dict[str, Any], path: Path | str | None = None) -> dict[str, Any]:
    save_secret_patch(patch, allowed_fields=LOCAL_SENSITIVE_FIELDS, path=path)
    return credential_status(path=path)


# Non-sensitive, user-remembered choices. ``allow_miuistore`` is the Douyin
# third-party fallback: on by default, and remembered here when switched off.
LOCAL_PREFERENCE_FIELDS = {"allow_miuistore"}


def load_preferences(path: Path | str | None = None) -> dict[str, Any]:
    data = load_config(path)
    preferences = data.get("preferences")
    return preferences if isinstance(preferences, dict) else {}


def save_preferences(patch: dict[str, Any], path: Path | str | None = None) -> dict[str, Any]:
    target = Path(path) if path else config_path()
    current = load_config(target)
    preferences = current.get("preferences") if isinstance(current.get("preferences"), dict) else {}
    next_preferences = dict(preferences)
    for key in LOCAL_PREFERENCE_FIELDS:
        if key not in patch:
            continue
        value = patch.get(key)
        if value is None:
            next_preferences.pop(key, None)
        elif isinstance(value, bool):
            next_preferences[key] = value
        else:
            next_preferences[key] = str(value).strip().lower() in {"1", "true", "yes", "on"}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {**current, "preferences": next_preferences},
            ensure_ascii=False, indent=2, sort_keys=True,
        ),
        encoding="utf-8",
    )
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return next_preferences


def get_preference(name: str, path: Path | str | None = None) -> Any:
    if name not in LOCAL_PREFERENCE_FIELDS:
        return None
    return load_preferences(path).get(name)


def get_configured_secret(
    name: str,
    *,
    allowed_fields: set[str],
    env_fallbacks: dict[str, str],
    secret_aliases: dict[str, tuple[str, ...]] | None = None,
    env_alias_fallbacks: dict[str, tuple[str, ...]] | None = None,
    path: Path | str | None = None,
) -> str | None:
    if name not in allowed_fields:
        return None
    data = load_config(path)
    secrets = data.get("secrets") if isinstance(data.get("secrets"), dict) else {}
    for secret_name in (secret_aliases or {}).get(name, (name,)):
        value = (secrets.get(secret_name) or "").strip()
        if value:
            return value
    env_names = (env_alias_fallbacks or {}).get(name, (env_fallbacks.get(name),))
    for env_name in env_names:
        if not env_name:
            continue
        env_value = (os.environ.get(env_name) or "").strip()
        if env_value:
            return env_value
    return None


def get_sensitive_setting(name: str, path: Path | str | None = None) -> str | None:
    if path is None:
        load_project_env()
    return get_configured_secret(
        name,
        allowed_fields=LOCAL_SENSITIVE_FIELDS,
        env_fallbacks=LOCAL_ENV_FALLBACKS,
        secret_aliases=SECRET_ALIASES,
        env_alias_fallbacks=ENV_ALIAS_FALLBACKS,
        path=path,
    )


def credential_status(path: Path | str | None = None) -> dict[str, Any]:
    return {
        "deepseek_api_key_configured": bool(get_sensitive_setting("deepseek_api_key", path)),
        "openai_api_key_configured": bool(get_sensitive_setting("openai_api_key", path)),
        "dashscope_api_key_configured": bool(get_sensitive_setting("dashscope_api_key", path)),
        "qwen_api_key_configured": bool(get_sensitive_setting("qwen_api_key", path)),
        "anthropic_api_key_configured": bool(get_sensitive_setting("anthropic_api_key", path)),
        "lark_app_id_configured": bool(get_sensitive_setting("lark_app_id", path)),
        "lark_app_secret_configured": bool(get_sensitive_setting("lark_app_secret", path)),
        "pyannote_auth_token_configured": bool(get_sensitive_setting("pyannote_auth_token", path)),
        "storage": "backend_local_file",
    }


def resolve_secret(
    form_value: str | None,
    name: str,
    path: Path | str | None = None,
) -> str | None:
    value = (form_value or "").strip()
    if value:
        return value
    return get_sensitive_setting(name, path)
