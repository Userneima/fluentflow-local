"""Runtime storage paths for FluentFlow.

Code lives in the repository. Runtime data belongs in a user/system data
directory unless an explicit environment variable says otherwise.

Where such a variable comes from is decided here too: the process environment
first, then the repository's ``.env``. Every Python entry point (the service,
the readiness check, the model fetchers, the uninstaller's question) calls
:func:`load_project_env` before it asks for a path, so they all agree on the
data folder and on ``HF_HOME`` (the transcription models). The installer writes
both into ``.env`` when it puts the whole product in one folder. The shell
scripts read the same lines with the same rule:
``launchers/windows/fluentflow-env.ps1`` and ``launchers/macos/fluentflow-env.sh``.

Standard library only: the speaker-model fetcher runs this before anything
else is importable.
"""

from __future__ import annotations

import os
import platform
import re
from pathlib import Path


APP_NAME = "FluentFlow"

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def project_env_file() -> Path:
    return PROJECT_ROOT / ".env"


def _env_value(raw: str) -> str:
    value = raw.strip()
    if value[:1] in ("'", '"'):
        quote = value[0]
        end = value.find(quote, 1)
        if end > 0:
            inner = value[1:end]
            # Double quotes take the escapes python-dotenv takes; single quotes
            # are literal.
            if quote == '"':
                inner = inner.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
            return inner
    # An unquoted value ends at a " #" comment, as in python-dotenv.
    return re.split(r"\s+#", value, maxsplit=1)[0].strip()


def read_env_file(path: Path) -> dict[str, str]:
    """The ``KEY=value`` lines of a dotenv file; the last one of a key wins."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = _ENV_LINE.match(line)
        if match:
            values[match.group(1)] = _env_value(match.group(2))
    return values


def load_project_env(path: Path | None = None) -> None:
    """Put the repository ``.env`` into the environment; set variables win."""
    for key, value in read_env_file(path or project_env_file()).items():
        os.environ.setdefault(key, value)


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
    """Where the speaker-diarization model files live.

    Holds ``segmentation.onnx`` and ``embedding.onnx`` for the default
    sherpa-onnx backend, put there by `scripts/fetch_diarization_models.py`
    (the installers run it).
    """
    return runtime_path("FLUENTFLOW_DIARIZATION_MODEL_DIR", "models", "speaker")


def default_pyannote_model_dir() -> Path:
    """Where the optional pyannote backend's model files live.

    Filled by `scripts/fetch_pyannote_models.py` (``config.yaml`` plus two
    checkpoints, from ModelScope). Only used with
    ``FLUENTFLOW_SPEAKER_BACKEND=pyannote``.
    """
    return runtime_path("FLUENTFLOW_PYANNOTE_MODEL_DIR", "models", "pyannote")


def default_artifact_dir() -> Path:
    return runtime_path("FLUENTFLOW_ARTIFACT_DIR", "artifacts")


def default_edited_transcript_dir() -> Path:
    return runtime_path("FLUENTFLOW_EDITED_TRANSCRIPT_DIR", "edited_transcripts")


def default_transcript_edit_records_dir() -> Path:
    return runtime_path("FLUENTFLOW_TRANSCRIPT_EDIT_RECORDS_DIR", "transcript_edit_records")


def default_video_source_dir() -> Path:
    return runtime_path("FLUENTFLOW_VIDEO_SOURCE_DIR", "video_sources")


def bundled_tools_bin() -> Path:
    """Where the installer puts the tools it downloads (FFmpeg, uv).

    The installer downloads them here instead of going through a package manager
    that would ask for a password, so an agent can install FluentFlow unattended.
    """
    return app_data_root() / "bin"


def put_bundled_tools_on_path() -> None:
    """Put the installer's tool directory first on PATH.

    Prepended, so a downloaded FFmpeg is found by every ``shutil.which`` and
    subprocess call; skipped when the directory does not exist, so a machine
    whose FFmpeg came from elsewhere is unaffected.
    """
    directory = bundled_tools_bin()
    if not directory.is_dir():
        return
    entries = os.environ.get("PATH", "").split(os.pathsep)
    if str(directory) not in entries:
        os.environ["PATH"] = os.pathsep.join([str(directory), *[e for e in entries if e]])
