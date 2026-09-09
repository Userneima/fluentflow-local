"""Local-only health, version, credential, and runtime configuration routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from backend.core.local_config import (
    LOCAL_PREFERENCE_FIELDS,
    LOCAL_SENSITIVE_FIELDS,
    credential_status,
    load_preferences,
    save_preferences,
    save_sensitive_settings,
)
from backend.core import local_intake_flow
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


router = APIRouter()


def _limits() -> dict[str, Any]:
    duration = max_media_duration_seconds()
    return {
        "max_upload_mb": max_upload_mb(),
        "max_queue_files": max_queue_files(),
        "max_media_duration_seconds": duration or None,
    }


@router.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "app_version": get_app_version(),
        "event_schema_version": EVENT_SCHEMA_VERSION,
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
    return credential_status()


@router.post("/credentials")
def update_credentials(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return save_sensitive_settings(
        {key: value for key, value in payload.items() if key in LOCAL_SENSITIVE_FIELDS}
    )


@router.get("/preferences")
def get_preferences() -> dict[str, Any]:
    return {"preferences": load_preferences()}


@router.post("/preferences")
def update_preferences(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Persist remembered user choices (e.g. the Douyin miuistore consent)."""
    patch = {key: value for key, value in payload.items() if key in LOCAL_PREFERENCE_FIELDS}
    if not patch:
        raise HTTPException(status_code=400, detail="No supported preference in payload")
    return {"preferences": save_preferences(patch)}


@router.get("/runtime-config")
def runtime_config() -> dict[str, Any]:
    return {
        "auth_mode": "open",
        "execution": "local",
        "allowed_stt_providers": list(allowed_stt_providers()),
        "default_stt_provider": default_stt_provider(),
        "show_maintainer_settings": True,
        "limits": _limits(),
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


@router.get("/speaker-diarization/status")
def get_speaker_diarization_status() -> dict[str, Any]:
    return diarization_status()


@router.get("/hotword-libraries", include_in_schema=False)
def removed_hotword_libraries() -> None:
    raise HTTPException(
        status_code=410,
        detail="Built-in hotword libraries have been removed",
    )
