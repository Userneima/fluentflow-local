"""Local backend-owned configuration for sensitive FluentFlow settings."""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from backend.core import runtime_paths
from backend.core.runtime_paths import default_config_path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"

LOCAL_SENSITIVE_FIELDS = {
    "deepseek_api_key",
    "openai_api_key",
    "dashscope_api_key",
    "qwen_api_key",
    "kimi_api_key",
    "mimo_api_key",
    # The user's own Anthropic key, used only by the visual-note entry. Kept a
    # separate field rather than folded into the note-provider keys because it
    # buys a different capability: without it that entry refuses and says so,
    # instead of quietly producing a subtitles-only note.
    "anthropic_api_key",
    "lark_app_id",
    "lark_app_secret",
    # Only the optional pyannote backend uses it, to fetch its models from
    # Hugging Face when they are not staged locally.
    "pyannote_auth_token",
    # A picture model behind an OpenAI-compatible endpoint (Kimi, MiMo, a
    # relay) that judges the cut from screenshots when Claude is not there
    # (``cut_judge``). The address and model are not secret but are kept with
    # the key, so the three are saved and read in one place.
    "vision_base_url",
    "vision_api_key",
    "vision_model",
}

LOCAL_ENV_FALLBACKS = {
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "dashscope_api_key": "DASHSCOPE_API_KEY",
    "qwen_api_key": "QWEN_API_KEY",
    "kimi_api_key": "KIMI_API_KEY",
    "mimo_api_key": "MIMO_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "lark_app_id": "LARK_APP_ID",
    "lark_app_secret": "LARK_APP_SECRET",
    "pyannote_auth_token": "PYANNOTE_AUTH_TOKEN",
    "vision_base_url": "FLUENTFLOW_VISION_BASE_URL",
    "vision_api_key": "FLUENTFLOW_VISION_API_KEY",
    "vision_model": "FLUENTFLOW_VISION_MODEL",
}

SECRET_ALIASES = {
    "dashscope_api_key": ("dashscope_api_key", "qwen_api_key"),
    "qwen_api_key": ("qwen_api_key", "dashscope_api_key"),
}

ENV_ALIAS_FALLBACKS = {
    "dashscope_api_key": ("DASHSCOPE_API_KEY", "QWEN_API_KEY"),
    "qwen_api_key": ("QWEN_API_KEY", "DASHSCOPE_API_KEY"),
    "kimi_api_key": ("KIMI_API_KEY", "MOONSHOT_API_KEY"),
}


def load_project_env() -> None:
    # One rule for every entry point: runtime_paths.load_project_env.
    runtime_paths.load_project_env(DEFAULT_ENV_PATH)


def config_path() -> Path:
    # Resolved on every call, never at import: the service imports this module
    # before it reads .env, and a path frozen then put the user's saved keys in
    # the default data folder while everything else followed FLUENTFLOW_DATA_DIR
    # from .env (found on GitHub's Windows runner, 2026-11-21).
    target = default_config_path()
    _carry_over_settings_saved_by_the_old_path(target)
    return target


def _carry_over_settings_saved_by_the_old_path(target: Path) -> None:
    """Before that fix, a data folder set only in .env still had its settings
    file in the platform's default folder. On the first read after updating,
    that file is copied to where it belongs, so the user's keys stay."""
    # Only a data folder that came from .env had the old behaviour; one set in
    # the environment (a test run, a test copy) never shared the default file.
    if target.exists() or os.environ.get("FLUENTFLOW_CONFIG_PATH") or "FLUENTFLOW_DATA_DIR" not in runtime_paths.FROM_ENV_FILE:
        return
    legacy = runtime_paths.default_app_data_root() / target.name
    if legacy == target or not legacy.is_file():
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy, target)
    except OSError:
        pass


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    target = Path(path) if path else config_path()
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


logger = logging.getLogger(__name__)

# Every save reads the whole file, changes one part and writes it back: two
# saves at once (a page setting and an agent's record) would each write the
# file without the other's change. One lock for the process around the
# read-modify-write, and the write goes to a temporary file swapped in whole,
# so a reader never sees half a file.
_WRITE_LOCK = threading.RLock()


def _load_for_update(target: Path) -> dict[str, Any]:
    """The file's settings before a save. A file that is there but cannot be
    read as settings is copied aside (``<name>.broken-<time>``) before the save
    replaces it, so a damaged file with the user's keys is never lost silently."""
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, UnicodeDecodeError, ValueError):
        pass
    backup = target.with_name(f"{target.name}.broken-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}")
    shutil.copy2(target, backup)
    logger.warning("设置文件 %s 读不出来，已原样备份到 %s，再写入这次的改动。", target, backup)
    return {}


def _write_config(target: Path, data: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f"{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        try:
            os.chmod(temp, 0o600)  # the file holds secrets: readable by the user only
        except OSError:
            pass
        for attempt in range(5):
            try:
                os.replace(temp, target)
                break
            except PermissionError:
                # Windows refuses the swap while a reader has the file open.
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def update_config(change: Callable[[dict[str, Any]], dict[str, Any]], path: Path | str | None = None) -> dict[str, Any]:
    """Read the settings, apply ``change`` (it returns the new whole), write
    them back, all under the lock; returns what was written."""
    target = Path(path) if path else config_path()
    with _WRITE_LOCK:
        updated = change(_load_for_update(target))
        _write_config(target, updated)
        return updated


def save_secret_patch(
    patch: dict[str, Any],
    *,
    allowed_fields: set[str],
    path: Path | str | None = None,
) -> None:
    def change(current: dict[str, Any]) -> dict[str, Any]:
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
        return {**current, "secrets": next_secrets}

    update_config(change, path)


def save_section(name: str, value: Any, path: Path | str | None = None) -> None:
    """Replace one top-level section of the config, keeping the rest (the
    file also holds secrets, so it stays readable by the user only)."""
    update_config(lambda current: {**current, name: value}, path)


def save_sensitive_settings(patch: dict[str, Any], path: Path | str | None = None) -> dict[str, Any]:
    save_secret_patch(patch, allowed_fields=LOCAL_SENSITIVE_FIELDS, path=path)
    return credential_status(path=path)


# Non-sensitive, user-remembered choices. ``video_cookies_browser`` is the browser whose login link downloads use ("" =
# none). ``lark_export_route`` ("auto" | "local_cli" | "openapi") and
# ``lark_folder_token`` are the Feishu export route and target folder. They
# live here, not only in the web page, so tasks driven by an AI tool (MCP) use
# the same choices as the page. ``cpu_usage`` ("light" | "balanced" |
# "full") is how much of the processor a cut may take, and
# ``processing_preset`` ("quality" | "size"; a retired "speed" is saved as "quality") what a cut favours; see
# ``device_profile``.
#
# ``ai_provider`` and ``ai_model`` (the text model that writes notes; empty
# model = the provider's default), ``speaker_diarization`` and
# ``export_to_lark`` (booleans) are the choices a task uses when it does not
# name its own (decision 6.31): the page and an agent submitting the same
# recording get the same result. See ``saved_choices``.
LOCAL_PREFERENCE_FIELDS = {
    "video_cookies_browser", "lark_export_route", "lark_folder_token", "cpu_usage", "processing_preset",
    "ai_provider", "ai_model", "speaker_diarization", "export_to_lark",
}
_BOOLEAN_PREFERENCES = ("speaker_diarization", "export_to_lark")
# Preferences that no longer exist, dropped from the file on the next save.
# ``allow_miuistore`` switched the Douyin third-party resolver, which now
# always runs.
_RETIRED_PREFERENCES = ("allow_miuistore",)
LARK_EXPORT_ROUTES = ("auto", "local_cli", "openapi")
CPU_USAGE_CHOICES = ("light", "balanced", "full")
PROCESSING_PRESET_CHOICES = ("quality", "size")
ALLOWED_COOKIE_BROWSERS = frozenset({
    "chrome", "edge", "firefox", "safari", "brave", "chromium", "opera", "vivaldi",
})


def normalize_cookie_browser(value: Any) -> str | None:
    """A known browser name, "" for none, or None for anything else."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return text if text in ALLOWED_COOKIE_BROWSERS else None


def load_preferences(path: Path | str | None = None) -> dict[str, Any]:
    data = load_config(path)
    preferences = data.get("preferences")
    return preferences if isinstance(preferences, dict) else {}


def save_preferences(patch: dict[str, Any], path: Path | str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {}

    def change(current: dict[str, Any]) -> dict[str, Any]:
        result.update(_next_preferences(current, patch))
        return {**current, "preferences": dict(result)}

    update_config(change, path)
    return result


def _next_preferences(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    preferences = current.get("preferences") if isinstance(current.get("preferences"), dict) else {}
    next_preferences = dict(preferences)
    for key in _RETIRED_PREFERENCES:
        next_preferences.pop(key, None)
    for key in LOCAL_PREFERENCE_FIELDS:
        if key not in patch:
            continue
        value = patch.get(key)
        if value is None:
            next_preferences.pop(key, None)
        elif key == "lark_export_route":
            route = str(value).strip().lower()
            if route and route not in LARK_EXPORT_ROUTES:
                raise ValueError(f"不支持的飞书导出路线：{value}")
            if route:
                next_preferences[key] = route
            else:
                next_preferences.pop(key, None)
        elif key in ("cpu_usage", "processing_preset"):
            choice = str(value).strip().lower()
            if key == "processing_preset" and choice == "speed":
                choice = "quality"
            allowed = CPU_USAGE_CHOICES if key == "cpu_usage" else PROCESSING_PRESET_CHOICES
            if choice and choice not in allowed:
                raise ValueError(f"不支持的选项：{value}")
            if choice:
                next_preferences[key] = choice
            else:
                next_preferences.pop(key, None)
        elif key in _BOOLEAN_PREFERENCES:
            next_preferences[key] = value is True or str(value).strip().lower() in {"1", "true", "yes", "on"}
        elif key == "ai_provider":
            from backend.core import text_providers
            provider = str(value).strip().lower()
            if provider and provider not in text_providers.PROVIDERS:
                raise ValueError(f"不支持的服务商：{value}")
            if provider:
                next_preferences[key] = provider
            else:
                next_preferences.pop(key, None)
        elif key in ("lark_folder_token", "ai_model"):
            token = str(value).strip()
            if token:
                next_preferences[key] = token
            else:
                next_preferences.pop(key, None)
        else:
            browser = normalize_cookie_browser(value)
            if browser is None:
                raise ValueError(f"不支持的浏览器：{value}")
            if browser:
                next_preferences[key] = browser
            else:
                next_preferences.pop(key, None)
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
        "kimi_api_key_configured": bool(get_sensitive_setting("kimi_api_key", path)),
        "mimo_api_key_configured": bool(get_sensitive_setting("mimo_api_key", path)),
        "anthropic_api_key_configured": bool(get_sensitive_setting("anthropic_api_key", path)),
        "lark_app_id_configured": bool(get_sensitive_setting("lark_app_id", path)),
        "lark_app_secret_configured": bool(get_sensitive_setting("lark_app_secret", path)),
        "pyannote_auth_token_configured": bool(get_sensitive_setting("pyannote_auth_token", path)),
        "vision_api_key_configured": bool(get_sensitive_setting("vision_api_key", path)),
        # Shown as they are: an address and a model name are not secrets, and
        # the page has to say which model will look at the pictures.
        "vision_base_url": get_sensitive_setting("vision_base_url", path) or "",
        "vision_model": get_sensitive_setting("vision_model", path) or "",
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


def saved_choices(options: dict[str, Any] | None) -> dict[str, Any]:
    """``options`` with the saved choices filled in where the task names none.

    A task's own value always wins, an explicit "false" included; an absent
    key, ``None`` or an empty string takes the saved preference. The switches,
    and the Feishu route and folder an automatic export uses, go through here;
    the text model is resolved in ``local_entry_guards.local_ai_kwargs``, which
    every note path calls.
    """
    result = dict(options or {})
    preferences = load_preferences()

    def absent(key: str) -> bool:
        given = result.get(key)
        return given is None or (isinstance(given, str) and not given.strip())

    for key in _BOOLEAN_PREFERENCES:
        if absent(key) and key in preferences:
            result[key] = "true" if preferences[key] else "false"
    # An automatic export goes the way Settings says, to its folder; without
    # this a task that named no route was exported with the Feishu app.
    if absent("lark_export_route") and absent("lark_via_cli"):
        result["lark_export_route"] = str(preferences.get("lark_export_route") or "auto")
    if absent("folder_token") and preferences.get("lark_folder_token"):
        result["folder_token"] = str(preferences["lark_folder_token"])
    return result


def as_recorded(options: dict[str, Any] | None) -> dict[str, Any]:
    """A stored task's options for a retry: the switches it ran with.

    New tasks record the filled-in choices (``saved_choices`` runs before the
    options are stored). Older ones recorded speaker labels and auto export only
    when they were on, so a missing one meant off; it is said out loud here, or
    a retry would pick up whatever Settings says today (and export a task to
    Feishu that never was).
    """
    result = dict(options or {})
    for key in _BOOLEAN_PREFERENCES:
        given = result.get(key)
        if given is None or (isinstance(given, str) and not given.strip()):
            result[key] = "false"
    return result
