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
            "HOMEDATA=${HOME}/FluentFlow",
            'QUOTED_REF="${PLAIN}/x"',
            "LITERAL_REF='${PLAIN}/x'",
            "DEFAULTED=${FLUENTFLOW_SURELY_UNSET:-fallback}",
            "ESCAPED_SINGLE='it\\'s here'",
            'ESCAPED_DOUBLE="say \\"hi\\" \\\\ done"',
            'MULTILINE="first line',
            'second line"',
            "AFTER_MULTILINE=yes",
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


def test_the_settings_file_follows_a_data_folder_set_after_import(tmp_path, monkeypatch):
    """local_main imports local_config before it reads .env; the settings file
    (the user's keys) must still land in the data folder .env names."""
    from backend.core import local_config

    monkeypatch.delenv("FLUENTFLOW_CONFIG_PATH", raising=False)
    monkeypatch.setenv("FLUENTFLOW_DATA_DIR", str(tmp_path / "FluentFlow" / "data"))
    assert local_config.config_path() == tmp_path / "FluentFlow" / "data" / "fluentflow_config.json"


def test_windows_installs_its_tested_lock():
    lock = ROOT / "requirements-local.lock.windows-AMD64.txt"
    text = lock.read_text(encoding="utf-8")
    pins = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert pins and all("==" in line for line in pins)
    assert not any(line.lower().startswith("yt-dlp") for line in pins)
    assert any(line.startswith("av==") for line in pins)
    setup = (ROOT / "launchers" / "windows" / "setup-local.ps1").read_text(encoding="utf-8-sig")
    assert "requirements-local.lock.windows-AMD64.txt" in setup


def test_installers_take_the_system_proxy_when_none_is_set():
    """A friend behind a local proxy added it to every command by hand (2026-11)."""
    assert "Use-FluentFlowSystemProxy" in (ROOT / "launchers" / "windows" / "setup-local.ps1").read_text(encoding="utf-8-sig")
    assert "ff_use_system_proxy" in (ROOT / "launchers" / "macos" / "setup-local.sh").read_text(encoding="utf-8")
    assert "scutil --proxy" in (ROOT / "install.sh").read_text(encoding="utf-8")
    helper = (ROOT / "launchers" / "windows" / "fluentflow-env.ps1").read_text(encoding="utf-8-sig")
    assert "Internet Settings" in helper and "NO_PROXY" in helper


# macOS ships bash 3.2; Homebrew's bash 5 often comes first on PATH. Bash 5
# treats a declared-but-unset local as unbound under set -u, 3.2 does not.
_BASHES = sorted({b for b in ("/bin/bash", "/opt/homebrew/bin/bash", "/usr/local/bin/bash", shutil.which("bash")) if b and os.path.isfile(b)})


@pytest.mark.skipif(sys.platform == "win32" or not _BASHES, reason="the macOS scripts, run by bash")
@pytest.mark.parametrize(
    ("scutil_output", "expected"),
    [
        # No proxy switched on: the common case, which once stopped the Mac
        # install with "host: unbound variable" under bash 5.
        ("<dictionary> {\n  HTTPEnable : 0\n  HTTPSEnable : 0\n}\n", ""),
        (
            "<dictionary> {\n  HTTPSEnable : 1\n  HTTPSPort : 7890\n  HTTPSProxy : 127.0.0.1\n}\n",
            "http://127.0.0.1:7890",
        ),
    ],
)
def test_the_mac_scripts_read_the_system_proxy_under_set_u(tmp_path, scutil_output, expected):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    scutil = fake_bin / "scutil"
    scutil.write_text(f"#!/bin/sh\ncat <<'EOF'\n{scutil_output}EOF\n", encoding="utf-8")
    scutil.chmod(0o755)
    helper = ROOT / "launchers" / "macos" / "fluentflow-env.sh"
    env = {k: v for k, v in _clean_env().items() if "proxy" not in k.lower()}
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    script = f'set -euo pipefail; source "{helper}"; ff_use_system_proxy >/dev/null; printf "%s" "${{HTTPS_PROXY:-}}"'
    for bash in _BASHES:
        result = subprocess.run([bash, "-c", script], capture_output=True, text=True, env=env)
        assert result.returncode == 0, (bash, result.stderr)
        assert result.stdout == expected, bash


def test_settings_saved_by_the_old_path_come_along(tmp_path, monkeypatch):
    """Before the fix a data folder set only in .env still had its settings in
    the platform folder; updating must not lose the user's keys."""
    from backend.core import local_config

    legacy_root = tmp_path / "platform"
    legacy_root.mkdir()
    (legacy_root / "fluentflow_config.json").write_text('{"secrets": {"deepseek_api_key": "k"}}', encoding="utf-8")
    monkeypatch.setattr(runtime_paths, "default_app_data_root", lambda: legacy_root)
    monkeypatch.delenv("FLUENTFLOW_CONFIG_PATH", raising=False)
    monkeypatch.delenv("FLUENTFLOW_DATA_DIR", raising=False)
    env = tmp_path / ".env"
    env.write_text(f"FLUENTFLOW_DATA_DIR={tmp_path / 'FluentFlow' / 'data'}\n", encoding="utf-8")
    monkeypatch.setattr(runtime_paths, "FROM_ENV_FILE", set())
    runtime_paths.load_project_env(env)
    monkeypatch.setenv("FLUENTFLOW_DATA_DIR", os.environ["FLUENTFLOW_DATA_DIR"])  # undone after the test

    assert local_config.load_config()["secrets"]["deepseek_api_key"] == "k"
    assert (tmp_path / "FluentFlow" / "data" / "fluentflow_config.json").is_file()


def test_a_data_folder_set_in_the_environment_takes_no_old_settings(tmp_path, monkeypatch):
    """A test run or a test copy sets the data folder itself: the maintainer's
    real settings (keys) must not be copied into it."""
    from backend.core import local_config

    legacy_root = tmp_path / "platform"
    legacy_root.mkdir()
    (legacy_root / "fluentflow_config.json").write_text('{"secrets": {"deepseek_api_key": "real"}}', encoding="utf-8")
    monkeypatch.setattr(runtime_paths, "default_app_data_root", lambda: legacy_root)
    monkeypatch.setattr(runtime_paths, "FROM_ENV_FILE", set())
    monkeypatch.delenv("FLUENTFLOW_CONFIG_PATH", raising=False)
    monkeypatch.setenv("FLUENTFLOW_DATA_DIR", str(tmp_path / "isolated"))

    assert local_config.load_config() == {}
    assert not (tmp_path / "isolated" / "fluentflow_config.json").exists()


def test_update_and_uninstall_leave_what_is_not_theirs():
    """A folder the user named app.old, data kept inside the code folder, and
    another Windows account's install are not the scripts' to delete or take."""
    windows = (ROOT / "install.ps1").read_text(encoding="utf-8")
    macos = (ROOT / "install.sh").read_text(encoding="utf-8")
    for text in (windows, macos):
        assert ".fluentflow-update" in text
        # Every setting that can place the user's files, not a fixed few.
        assert ("FLUENTFLOW_\\w*(DIR|PATH|CACHE)" in text or "FLUENTFLOW_[A-Z0-9_]*(DIR|PATH|CACHE)" in text) and "HF_HOME" in text
    assert "Assert-OwnLeftover" in windows and "assert_own_leftover" in macos
    assert "WindowsIdentity]::GetCurrent().User.Value" in windows
    assert "another Windows account's FluentFlow" in windows and "icacls" in windows
    # A release older than this installer is installed by its own installer.
    assert "predates this installer" in windows
    assert "KeepsUserFiles" in (ROOT / "launchers" / "windows" / "uninstall-local.ps1").read_text(encoding="utf-8-sig")
    assert "KEEPS_USER_FILES" in (ROOT / "launchers" / "macos" / "uninstall-local.sh").read_text(encoding="utf-8")
    assert '"release-check"' in (ROOT / "backend" / "core" / "local_http_boundary.py").read_text(encoding="utf-8")
