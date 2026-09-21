"""Pytest bootstrap for FluentFlow.

Runtime storage must be redirected BEFORE any backend module is imported.
``backend.core.job_store`` and its siblings freeze their storage location into
a module constant at import time (``DEFAULT_DB_PATH = default_job_db_path()``)
and then bake that constant into every ``db_path=`` default argument, so a
fixture that sets ``FLUENTFLOW_JOB_DB_PATH`` during a test comes too late and
the writes land in the real user data directory. Conftest module import is the
last moment early enough to redirect them; `tests/test_runtime_storage_isolation.py`
guards that this file keeps doing its job.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Env var -> location under the data root, mirroring backend/core/runtime_paths.py
#. Local-only paths are intentionally listed here: hosted account, OSS and
# desktop-sync stores do not exist in this repository.
RUNTIME_PATH_ENV_VARS = {
    "FLUENTFLOW_CONFIG_PATH": "fluentflow_config.json",
    "FLUENTFLOW_JOB_DB_PATH": "fluentflow_jobs.sqlite",
    "FLUENTFLOW_EVENT_DB_PATH": "fluentflow_events.sqlite",
    "FLUENTFLOW_SOURCE_DIR": "sources",
    "FLUENTFLOW_ARTIFACT_DIR": "artifacts",
    "FLUENTFLOW_EDITED_TRANSCRIPT_DIR": "edited_transcripts",
    "FLUENTFLOW_TRANSCRIPT_EDIT_RECORDS_DIR": "transcript_edit_records",
    "FLUENTFLOW_VIDEO_SOURCE_DIR": "video_sources",
    "FLUENTFLOW_CODEX_EXPORT_DIR": "codex_exports",
}


def _isolate_runtime_storage() -> Path:
    root = Path(tempfile.mkdtemp(prefix="fluentflow-test-runtime-"))
    os.environ["FLUENTFLOW_DATA_DIR"] = str(root)

    env_file = PROJECT_ROOT / ".env"
    dotenv_keys = set(dotenv_values(env_file)) if env_file.is_file() else set()
    for name, relative in RUNTIME_PATH_ENV_VARS.items():
        if name in dotenv_keys:
            # Backend imports call ``load_dotenv(override=False)``. Clearing the
            # key would let the developer .env fill it back in with a real path,
            # so pin it inside the isolated root instead.
            os.environ[name] = str(root / relative)
        else:
            # A developer shell may export these; the data root override alone
            # would not win against a specific path.
            os.environ.pop(name, None)

    atexit.register(shutil.rmtree, root, ignore_errors=True)
    return root


TEST_RUNTIME_ROOT = _isolate_runtime_storage()


@pytest.fixture(scope="session")
def test_runtime_root() -> Path:
    """Isolated data root every runtime storage path must resolve inside."""
    return TEST_RUNTIME_ROOT


@pytest.fixture(autouse=True)
def isolate_local_auth_env(monkeypatch):
    """Keep developer .env auth settings from changing default API tests."""
    monkeypatch.delenv("FLUENTFLOW_AUTH_MODE", raising=False)
    monkeypatch.delenv("FLUENTFLOW_ACCOUNT_AUTH", raising=False)
    monkeypatch.delenv("FLUENTFLOW_ALLOW_SIGNUPS", raising=False)
    # A developer who turns on the local Agent API has an access token in .env,
    # and every route that takes one then answers 401 to a test client that sends
    # none: 46 failures that CI never sees, because CI has no .env. Three test
    # files had each found this and cleared it for themselves; it belongs here.
    monkeypatch.delenv("FLUENTFLOW_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("FLUENTFLOW_ACCESS_TOKENS", raising=False)
    # OAuth redirect/domain overrides must not leak from a developer .env into
    # tests that assert the default request-derived redirect_uri and base URL.
    monkeypatch.delenv("FEISHU_OAUTH_REDIRECT_URI", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_REDIRECT_URI", raising=False)
    monkeypatch.delenv("LARK_OPEN_BASE_URL", raising=False)


@pytest.fixture(autouse=True)
def reset_auth_rate_limits():
    """Per-IP auth/webhook budgets live in module state, so without this reset
    one test's registrations or failed logins would throttle the next test
    (every case shares the TestClient's 'testclient' IP)."""
    try:
        from backend.core import auth_rate_limit
    except ImportError:
        yield
        return

    auth_rate_limit.reset_rate_limits()
    yield
    auth_rate_limit.reset_rate_limits()
