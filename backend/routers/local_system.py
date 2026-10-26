"""Local-only health, version, credential, and runtime configuration routes."""

from __future__ import annotations

import os
import sys
from typing import Any, Callable

import httpx
from fastapi import APIRouter, Body, HTTPException

from backend.core.local_config import (
    LOCAL_PREFERENCE_FIELDS,
    LOCAL_SENSITIVE_FIELDS,
    PROJECT_ROOT,
    credential_status,
    load_preferences,
    resolve_secret,
    save_preferences,
    save_sensitive_settings,
)
from backend.core import claude_code_note, claude_vision, local_intake_flow, visual_note_channel
from backend.core import model_usage, text_providers
from backend.core.ai_client import _provider_base_url
from backend.core.local_limits_config import (
    max_media_duration_seconds,
    max_queue_files,
    max_upload_mb,
)
from backend.core.local_stt_policy import allowed_stt_providers, default_stt_provider
from backend.core.result_schema import RESULT_SCHEMA_VERSION
from backend.core.schema_versions import EVENT_SCHEMA_VERSION
from backend.core.speaker_diarization import diarization_status
from backend.core.versioning import get_app_version, version_payload
from backend.core.edition_identity import identity_payload
from backend.core.local_request_scope import agent_access_status
from backend.core.workflow_status import workflow_status
from backend.core.local_entry_guards import text_model_in_use


router = APIRouter()


def _limits() -> dict[str, Any]:
    duration = max_media_duration_seconds()
    return {
        "max_upload_mb": max_upload_mb() or None,
        "max_queue_files": max_queue_files(),
        "max_media_duration_seconds": duration or None,
    }


@router.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "app_version": get_app_version(),
        "event_schema_version": EVENT_SCHEMA_VERSION,
        # Which edition answered, and what it takes in. A client routing between
        # the two editions reads this instead of inferring from a rejection.
        **identity_payload(),
        "runtime": {"execution": "local"},
        "limits": _limits(),
    }


@router.get("/version")
def version() -> dict[str, Any]:
    return {
        **version_payload(component="backend-local"),
        "schemas": {
            "event": EVENT_SCHEMA_VERSION,
            "result": RESULT_SCHEMA_VERSION,
        },
    }


@router.get("/credentials/status")
def get_credentials_status() -> dict[str, Any]:
    # Whether the visual note can run is not the same as whether its key is
    # filled in: a source checkout can route it through the local Claude Code
    # login instead. The start page warns "no note" from this, so it has to be
    # the channel the note would actually use, not a guess from key fields.
    channel = visual_note_channel.resolve_channel(resolve_secret(None, "anthropic_api_key"))
    return {
        **credential_status(),
        "visual_note_available": channel.available,
        # Only for the channel that runs on this machine's Claude login: an
        # expired login is the one reason worth interrupting the start page for,
        # because the user can fix it in a minute and every note fails until then.
        "visual_note_login_expired": (
            channel.name == visual_note_channel.CHANNEL_SUBSCRIPTION
            and claude_code_note.cli_available()
            and claude_code_note.login_state() is False
        ),
    }


@router.post("/credentials")
def update_credentials(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return save_sensitive_settings(
        {key: value for key, value in payload.items() if key in LOCAL_SENSITIVE_FIELDS}
    )


# The first-run note setup saves a key and then asks whether it works, so a
# wrong paste or an empty account is found now, not after an hour-long job
# finishes without a note. One small request with a short timeout: DeepSeek's
# balance lookup costs nothing and also says whether there is money on the
# account; Anthropic has no free equivalent that reports credit, so it gets a
# one-token message (a fraction of a cent) to the model the note would use.
# The key is read from what was saved, never taken in this request's body.
_KEY_CHECK_TIMEOUT = httpx.Timeout(15.0, connect=8.0)
_KEY_CHECK_PROVIDERS = {
    "anthropic": ("anthropic_api_key", "ANTHROPIC_API_KEY", "Anthropic"),
    # Every text provider: DeepSeek by its balance, the others by listing
    # their models, which costs nothing and is refused for a wrong key.
    **{
        entry.id: (entry.key_settings[0], entry.key_envs[0], entry.label)
        for entry in text_providers.PROVIDERS.values()
    },
}

# Replaced in tests; the real one sends the request.
_http_request: Callable[..., httpx.Response] = httpx.request


def _key_check_result(ok: bool, reason: str, message: str) -> dict[str, Any]:
    return {"ok": ok, "reason": reason, "message": message}


def _check_deepseek_key(key: str) -> dict[str, Any]:
    response = _http_request(
        "GET",
        f"{_provider_base_url('deepseek')}/user/balance",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        timeout=_KEY_CHECK_TIMEOUT,
    )
    if response.status_code == 401:
        return _key_check_result(False, "invalid_key", "这个 Key 不对，DeepSeek 不认它。请回到申请页重新复制一遍，注意别多带空格。")
    if response.status_code == 402:
        return _key_check_result(False, "no_balance", "Key 是对的，但这个 DeepSeek 账户余额不足。充值后就能写笔记。")
    if response.status_code != 200:
        return _key_check_result(False, "provider_error", f"DeepSeek 返回了错误（{response.status_code}），稍后再试。")
    try:
        data = response.json()
    except ValueError:
        data = {}
    if isinstance(data, dict) and data.get("is_available") is False:
        return _key_check_result(False, "no_balance", "Key 是对的，但这个 DeepSeek 账户余额不足。充值后就能写笔记。")
    return _key_check_result(True, "ok", "Key 可以用。")


def _check_listing_key(provider: str, label: str, key: str) -> dict[str, Any]:
    response = _http_request(
        "GET", f"{_provider_base_url(provider)}/models",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        timeout=_KEY_CHECK_TIMEOUT,
    )
    if response.status_code in (401, 403):
        return _key_check_result(False, "invalid_key", f"这个 Key 不对，{label} 不认它。请回到申请页重新复制一遍，注意别多带空格。")
    if response.status_code != 200:
        return _key_check_result(False, "provider_error", f"{label} 返回了错误（{response.status_code}），稍后再试。")
    return _key_check_result(True, "ok", "Key 可以用。")


def _check_anthropic_key(key: str) -> dict[str, Any]:
    base = (os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
    model = claude_vision.configured_model()
    # One token asked for; it is a call to a model like any other (``model_usage``).
    with model_usage.item("检查 Key"), model_usage.call(model_usage.ANTHROPIC, model) as call:
        response = _http_request(
            "POST",
            f"{base}/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]},
            timeout=_KEY_CHECK_TIMEOUT,
        )
        if response.status_code == 200:
            try:
                call.claude_usage((response.json() or {}).get("usage"))
            except (ValueError, AttributeError):
                pass
        else:
            call.refused = f"HTTP {response.status_code}"
    status = response.status_code
    if status == 200:
        return _key_check_result(True, "ok", "Key 可以用。")
    try:
        detail = str(((response.json() or {}).get("error") or {}).get("message") or "")
    except (ValueError, AttributeError):
        detail = ""
    if status == 401:
        return _key_check_result(False, "invalid_key", "这个 Key 不对，Anthropic 不认它。请回到申请页重新复制一遍，注意别多带空格。")
    if status == 400 and "credit" in detail.lower():
        return _key_check_result(False, "no_balance", "Key 是对的，但这个 Anthropic 账户余额不足。充值后就能写笔记。")
    if status == 403:
        return _key_check_result(False, "no_permission", f"Key 是对的，但这个账户还不能用写笔记的模型 {model}。")
    if status == 429:
        # The key was accepted; the account is only being slowed down.
        return _key_check_result(True, "ok", "Key 可以用（Anthropic 暂时限流，不影响之后写笔记）。")
    return _key_check_result(False, "provider_error", f"Anthropic 返回了错误（{status}），稍后再试。")


@router.post("/credentials/check")
def check_credential(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    provider = str(payload.get("provider") or "").strip().lower()
    if provider not in _KEY_CHECK_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"provider must be one of {', '.join(_KEY_CHECK_PROVIDERS)}")
    secret_name, env_name, label = _KEY_CHECK_PROVIDERS[provider]
    key = resolve_secret(None, secret_name) or (os.environ.get(env_name) or "").strip()
    if not key:
        return _key_check_result(False, "missing", f"还没有保存 {label} 的 Key。")
    try:
        if provider == "deepseek":
            return _check_deepseek_key(key)
        if provider == "anthropic":
            return _check_anthropic_key(key)
        return _check_listing_key(provider, label, key)
    except httpx.TimeoutException:
        return _key_check_result(False, "network", f"连 {label} 的服务器超时了，检查一下网络后再试。")
    except httpx.HTTPError:
        return _key_check_result(False, "network", f"连不上 {label} 的服务器，检查一下网络后再试。")


# Which models a text provider offers, for the settings page's model field.
# Read from the provider's own list with the saved key, so a model released
# after this build is still offered; the field also takes any name typed in.
_MODEL_LIST_PROVIDERS = {
    entry.id: (entry.key_settings, entry.key_envs[0], entry.lists_model)
    for entry in text_providers.PROVIDERS.values()
}


@router.get("/models/defaults")
def model_defaults_table() -> dict[str, Any]:
    """Each provider's default model now, from the recommended-model table
    (``model_defaults``): what "default" means on the settings page."""
    from backend.core import model_defaults

    return {"providers": model_defaults.defaults()}


@router.get("/credentials/models")
def list_provider_models(provider: str) -> dict[str, Any]:
    name = (provider or "").strip().lower()
    if name not in _MODEL_LIST_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"provider must be one of {', '.join(_MODEL_LIST_PROVIDERS)}")
    secret_names, env_name, wanted = _MODEL_LIST_PROVIDERS[name]
    key = next((value for value in (resolve_secret(None, secret) for secret in secret_names) if value), None)
    key = key or (os.environ.get(env_name) or "").strip()
    if not key:
        return {"models": [], "reason": "missing"}
    try:
        response = _http_request(
            "GET", f"{_provider_base_url(name)}/models",
            headers={"Authorization": f"Bearer {key}"}, timeout=_KEY_CHECK_TIMEOUT,
        )
    except httpx.HTTPError:
        return {"models": [], "reason": "network"}
    if response.status_code != 200:
        return {"models": [], "reason": f"http_{response.status_code}"}
    try:
        items = response.json().get("data") or []
    except ValueError:
        return {"models": [], "reason": "unreadable"}
    rows = [item for item in items if isinstance(item, dict) and wanted(str(item.get("id") or ""))]
    rows.sort(key=lambda item: item.get("created") or 0, reverse=True)
    return {"models": [str(item["id"]) for item in rows], "reason": "ok"}


@router.get("/preferences")
def get_preferences() -> dict[str, Any]:
    # ``text_model``: the provider and model a task that names none will use
    # (the saved one, or the first with a key when it has none), so the page
    # shows that instead of guessing a default (decision 6.31).
    return {"preferences": load_preferences(), "text_model": text_model_in_use()}


@router.post("/preferences")
def update_preferences(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Persist remembered user choices (browser login, Feishu export route)."""
    patch = {key: value for key, value in payload.items() if key in LOCAL_PREFERENCE_FIELDS}
    if not patch:
        raise HTTPException(status_code=400, detail="No supported preference in payload")
    try:
        return {"preferences": save_preferences(patch)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runtime-config")
def runtime_config() -> dict[str, Any]:
    return {
        "auth_mode": "open",
        "execution": "local",
        "allowed_stt_providers": list(allowed_stt_providers()),
        "default_stt_provider": default_stt_provider(),
        "show_maintainer_settings": True,
        "limits": _limits(),
        # The Agent page prints MCP config and check commands from these, so
        # they name this checkout and the interpreter actually running it
        # instead of placeholders the user has to fill in.
        "repo_root": str(PROJECT_ROOT),
        "python_executable": sys.executable,
        # Wired by the local processing router: POST /jobs/{id}/retry re-runs
        # from the stored source file on the local hub.
        "features": {
            "job_retry_from_stored_source": True,
            # Whether this edition writes the note itself, from the cut media.
            # When it does, the pipeline's own note stage never runs — and the
            # settings that only steer that stage (illustrate the note, which
            # note strategy) steer nothing, so the page must not offer them.
            # Switchable by env, so the page asks rather than assuming.
            "writes_its_own_note": local_intake_flow.auto_note_enabled(),
        },
    }


# Who does each step and what needs the person (decision 6.24). The page passes
# the two choices still kept in the browser; see backend/core/workflow_status.py.
@router.get("/workflow-status")
def get_workflow_status(
    default_source: str = "",
    voice_enhance: str = "",
    skip_ai_summary: str = "",
) -> dict[str, Any]:
    return workflow_status({
        "default_source": default_source,
        "voice_enhance": voice_enhance,
        "skip_ai_summary": skip_ai_summary,
    })


# Whether an agent can connect (the access token is set) and when one last did.
@router.get("/agent-access/status")
def get_agent_access_status() -> dict[str, Any]:
    return agent_access_status()


@router.get("/speaker-diarization/status")
def get_speaker_diarization_status() -> dict[str, Any]:
    return diarization_status()


@router.get("/hotword-libraries", include_in_schema=False)
def removed_hotword_libraries() -> None:
    raise HTTPException(
        status_code=410,
        detail="Built-in hotword libraries have been removed",
    )
