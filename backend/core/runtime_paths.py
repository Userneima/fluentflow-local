"""Runtime storage paths for FluentFlow.

Code lives in the repository. Runtime data belongs in a user/system data
directory unless an explicit environment variable says otherwise.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path


APP_NAME = "FluentFlow"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def app_data_root() -> Path:
    override = (os.environ.get("FLUENTFLOW_DATA_DIR") or "").strip()
    if override:
        return Path(override).expanduser()

    system = platform.system().lower()
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if system == "windows":
        appdata = os.environ.get("APPDATA")
        return (Path(appdata).expanduser() if appdata else Path.home() / "AppData" / "Roaming") / APP_NAME

    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    root = Path(xdg_data_home).expanduser() if xdg_data_home else Path.home() / ".local" / "share"
    return root / "fluentflow"


def runtime_path(env_name: str, *parts: str) -> Path:
    override = (os.environ.get(env_name) or "").strip()
    if override:
        return Path(override).expanduser()
    return app_data_root().joinpath(*parts)


def default_config_path() -> Path:
    return runtime_path("FLUENTFLOW_CONFIG_PATH", "fluentflow_config.json")


def default_job_db_path() -> Path:
    return runtime_path("FLUENTFLOW_JOB_DB_PATH", "fluentflow_jobs.sqlite")


def default_event_db_path() -> Path:
    return runtime_path("FLUENTFLOW_EVENT_DB_PATH", "fluentflow_events.sqlite")


def default_source_dir() -> Path:
    return runtime_path("FLUENTFLOW_SOURCE_DIR", "sources")


def default_diarization_model_dir() -> Path:
    """Where local speaker-diarization model files live.

    pyannote normally fetches these from Hugging Face on first use. Where the
    Hugging Face file CDN is unreachable, `scripts/fetch_diarization_models.py`
    fills this directory from ModelScope instead and the loader prefers it.
    """
    return runtime_path("FLUENTFLOW_DIARIZATION_MODEL_DIR", "models", "pyannote")


def default_artifact_dir() -> Path:
    return runtime_path("FLUENTFLOW_ARTIFACT_DIR", "artifacts")


def default_edited_transcript_dir() -> Path:
    return runtime_path("FLUENTFLOW_EDITED_TRANSCRIPT_DIR", "edited_transcripts")


def default_transcript_edit_records_dir() -> Path:
    return runtime_path("FLUENTFLOW_TRANSCRIPT_EDIT_RECORDS_DIR", "transcript_edit_records")


def default_video_source_dir() -> Path:
    return runtime_path("FLUENTFLOW_VIDEO_SOURCE_DIR", "video_sources")


def default_codex_export_dir() -> Path:
    return runtime_path("FLUENTFLOW_CODEX_EXPORT_DIR", "codex_exports")


def legacy_repo_data_root() -> Path:
    return PROJECT_ROOT / "data"


def legacy_backend_data_root() -> Path:
    return PROJECT_ROOT / "backend" / "data"


def legacy_backend_video_source_dir() -> Path:
    return PROJECT_ROOT / "backend" / "视频文件"
