"""Requirements for where the data folder and the models are, when only .env says.

- Every Python entry point reads the repository .env before it asks for a path,
  so the data folder (FLUENTFLOW_DATA_DIR) and the models (HF_HOME) written
  there by the installer are the ones the service, the readiness check, the
  model fetchers and the uninstaller all use.
- A variable already set in the environment wins over .env.
- HF_HOME from .env takes effect for the transcription models: the model
  script lists and fetches under it (checked end to end in a copy of the code).
- The .env reader agrees with python-dotenv on the lines people write.
- Downloads with a mirror: a fast original keeps its place, a slow or failed
  one puts the faster host first.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from backend.core import download_source, runtime_paths

ROOT = Path(__file__).resolve().parents[1]


def test_reader_agrees_with_python_dotenv(tmp_path):
    dotenv = pytest.importorskip("dotenv")
    env = tmp_path / ".env"
    env.write_text(
        "\n".join([
            "# 注释",
            "PLAIN=value",
            "SPACED = spaced value ",
            "export EXPORTED=1",
            "COMMENTED=keep # dropped",
            'DOUBLE="D:\\\\FluentFlow\\\\data"',
            "SINGLE='a # b'",
            "WINDOWS=D:\\FluentFlow\\models",
            "EMPTY=",
            "REPEATED=first",
            "REPEATED=second",
            "",
        ]),
        encoding="utf-8",
    )
    expected = {k: v for k, v in dotenv.dotenv_values(env).items() if v is not None}
    assert runtime_paths.read_env_file(env) == expected


def test_set_variables_win_over_env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("FLUENTFLOW_DATA_DIR=/from/file\nHF_HOME=/models/from/file\n", encoding="utf-8")
    monkeypatch.setenv("FLUENTFLOW_DATA_DIR", "/from/shell")
    monkeypatch.delenv("HF_HOME", raising=False)

    runtime_paths.load_project_env(env)

    assert os.environ["FLUENTFLOW_DATA_DIR"] == "/from/shell"
    assert os.environ["HF_HOME"] == "/models/from/file"


def test_every_script_that_asks_for_a_path_reads_env_first():
    asks = re.compile(r"from backend\.core(?:\.runtime_paths| import hf_endpoint|\.local_stt)")
    missing = []
    for script in sorted((ROOT / "scripts").glob("*.py")):
        text = script.read_text(encoding="utf-8")
        if asks.search(text) and "load_project_env()" not in text:
            missing.append(script.name)
    assert missing == []


def test_the_model_script_reads_env_before_huggingface_loads():
    text = (ROOT / "scripts" / "stt_model.py").read_text(encoding="utf-8")
    assert text.index("load_project_env()\n") < text.index("from backend.core.local_stt_assets import")


def _copy_of_the_code(tmp_path: Path) -> Path:
    copy = tmp_path / "app"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "backend", copy / "backend", ignore=ignore)
    shutil.copytree(ROOT / "scripts", copy / "scripts", ignore=ignore)
    return copy


def _clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HF_", "FLUENTFLOW_"))}
    env.pop("PYTHONPATH", None)
    return env


def test_hf_home_in_env_moves_the_transcription_models(tmp_path):
    """The measurement the one-folder install depends on: a model folder
    written only into .env is where huggingface_hub looks, so the model the
    installer fetches lands in the chosen folder and the uninstaller finds it."""
    pytest.importorskip("huggingface_hub")
    copy = _copy_of_the_code(tmp_path)
    models = tmp_path / "FluentFlow" / "models"
    cached = models / "hub" / "models--Systran--faster-whisper-large-v3"
    cached.mkdir(parents=True)
    (copy / ".env").write_text(f"HF_HOME={models}\n", encoding="utf-8")

    listed = subprocess.run(
        [sys.executable, str(copy / "scripts" / "stt_model.py"), "cache-paths"],
        capture_output=True, text=True, env=_clean_env(), cwd=tmp_path, check=True,
    ).stdout.split()

    assert str(cached) in listed


def test_data_dir_in_env_reaches_the_readiness_check_and_the_uninstaller(tmp_path):
    copy = _copy_of_the_code(tmp_path)
    data = tmp_path / "FluentFlow" / "data"
    (copy / ".env").write_text(f'FLUENTFLOW_DATA_DIR="{data}"\n', encoding="utf-8")
    # The uninstallers ask exactly this.
    question = (
        "from backend.core.runtime_paths import load_project_env, app_data_root; "
        "load_project_env(); print(app_data_root())"
    )
    answer = subprocess.run(
        [sys.executable, "-c", question],
        capture_output=True, text=True, env=_clean_env(), cwd=copy, check=True,
    ).stdout.strip()
    assert answer == str(data)


def test_uninstallers_ask_with_env_loaded():
    for script in ("launchers/macos/uninstall-local.sh", "launchers/windows/uninstall-local.ps1"):
        text = (ROOT / script).read_text(encoding="utf-8-sig")
        assert "load_project_env(); print(app_data_root())" in text, script


def test_a_fast_original_keeps_its_place(monkeypatch):
    probed = []
    monkeypatch.setattr(download_source, "measure", lambda url, *a: probed.append(url) or download_source.PROBE_BYTES)
    assert download_source.order(["https://origin", "https://mirror"]) == ["https://origin", "https://mirror"]
    assert probed == ["https://origin"]


def test_a_slow_or_failed_original_puts_the_faster_host_first(monkeypatch):
    speeds = {"https://origin": 4096, "https://mirror": download_source.PROBE_BYTES}
    monkeypatch.setattr(download_source, "measure", lambda url, *a: speeds[url])
    assert download_source.order(["https://origin", "https://mirror"]) == ["https://mirror", "https://origin"]

    speeds["https://origin"] = 0
    speeds["https://mirror"] = 0
    # Neither answers: the original stays first, so its own error is reported.
    assert download_source.order(["https://origin", "https://mirror"]) == ["https://origin", "https://mirror"]


def test_windows_scripts_call_agent_tools_through_the_safe_wrapper():
    """npm installs claude and codex on Windows as PowerShell shims; called
    directly under ErrorActionPreference=Stop, their stderr stopped the
    installer at its last step (found on GitHub's Windows runner, 2026-11-21)."""
    pattern = re.compile(r"&\s*(claude|codex)\b")
    offenders = []
    for script in [ROOT / "install.ps1", *sorted((ROOT / "launchers" / "windows").glob("*.ps1"))]:
        if pattern.search(script.read_text(encoding="utf-8-sig")):
            offenders.append(script.name)
    assert offenders == []


def test_both_setup_scripts_install_packages_with_a_pinned_uv_and_pass_the_index():
    windows = (ROOT / "launchers" / "windows" / "setup-local.ps1").read_text(encoding="utf-8-sig")
    macos = (ROOT / "launchers" / "macos" / "setup-local.sh").read_text(encoding="utf-8")
    pins = {re.search(r'UvVersion = "([\d.]+)"', windows).group(1), re.search(r'UV_VERSION="([\d.]+)"', macos).group(1)}
    assert len(pins) == 1
    for text in (windows, macos):
        assert '"-m", "uv", "pip"' in text or "-m uv pip install" in text
        assert "--index-url" in text
