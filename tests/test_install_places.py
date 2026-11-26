"""Requirements for what updating and uninstalling may delete (independent
reviews of batch 5, 2026-11-26).

- A data, link or model folder that the service would put inside the code
  folder stops an update and keeps the code folder on uninstall, however the
  setting is written: a relative path (the service runs in the code folder), a
  ``${NAME}`` reference, ``~``, ``..``, a Chinese path. A setting the scripts
  cannot read for sure (a ``$`` left after expanding) counts as inside.
- The scripts that run before either copy's own helpers can be relied on
  (install.ps1, install.sh) carry the helpers' exact text.
- The install folder given to the installer is made absolute, with no
  trailing separator (``'D:\\FF\\'`` in .env swallows the next line).
- Removing the data folder removes only what FluentFlow recognises; anything
  else stays and is named. The list is in one file, and every place the code
  writes in the data folder is on it.
- A copy the user made of the code folder (app.old) is not an update's
  leftover, even though it carries the files of one.
- The Mac uninstaller stops programs still running from the code folder
  (the background model download), as the Windows one does.

The PowerShell halves run only on Windows (the GitHub Windows check runs them).
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAC_HELPER = ROOT / "launchers" / "macos" / "fluentflow-env.sh"
WIN_HELPER = ROOT / "launchers" / "windows" / "fluentflow-env.ps1"
REGISTRY = ROOT / "launchers" / "data-folder-contents.txt"
POSIX = sys.platform != "win32"
_BASHES = sorted({b for b in ("/bin/bash", "/opt/homebrew/bin/bash", "/usr/local/bin/bash", shutil.which("bash")) if b and os.path.isfile(b)})
_POWERSHELL = shutil.which("powershell") if sys.platform == "win32" else None


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


# --- the helpers' text in the two installers ---------------------------------

SHARED_BASH = ("ff_env_file_value", "ff_setting", "ff_normalize", "ff_place", "ff_lower", "ff_physical", "ff_inside",
               "ff_update_marker_says", "ff_mark_update_copy")
SHARED_POWERSHELL = ("Expand-FluentFlowReferences", "ConvertFrom-FluentFlowEnvValue", "Get-FluentFlowEnvFileValue",
                     "Get-FluentFlowSetting", "Resolve-FluentFlowPlace", "Test-FluentFlowInside",
                     "Test-FluentFlowUpdateCopy", "Set-FluentFlowUpdateCopy")


def _bash_function(text: str, name: str) -> str | None:
    match = re.search(rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n", text, re.S | re.M)
    return match.group(0) if match else None


def _powershell_function(text: str, name: str) -> str | None:
    match = re.search(rf"^function {re.escape(name)}\b.*?^\}}\r?\n", text, re.S | re.M)
    return match.group(0) if match else None


@pytest.mark.parametrize("name", SHARED_BASH)
def test_install_sh_carries_the_mac_helpers_exactly(name):
    helper = _bash_function(_read(MAC_HELPER), name)
    assert helper, f"{name} missing from fluentflow-env.sh"
    assert _bash_function(_read(ROOT / "install.sh"), name) == helper


@pytest.mark.parametrize("name", SHARED_POWERSHELL)
def test_install_ps1_carries_the_windows_helpers_exactly(name):
    helper = _powershell_function(_read(WIN_HELPER), name)
    assert helper, f"{name} missing from fluentflow-env.ps1"
    assert _powershell_function(_read(ROOT / "install.ps1"), name) == helper


def test_the_update_and_uninstall_checks_use_the_service_reading():
    install_ps1, install_sh = _read(ROOT / "install.ps1"), _read(ROOT / "install.sh")
    assert "IsPathRooted($Where)" not in install_ps1 and "Get-InstalledSetting" not in install_ps1
    assert "Resolve-FluentFlowPlace" in install_ps1 and "ff_place" in install_sh
    assert "IsPathRooted($Path)" not in _read(ROOT / "launchers" / "windows" / "uninstall-local.ps1")
    # Agents pass the folder through PowerShell, which does not expand %VAR%.
    agent_install = _read(ROOT / "AGENT_INSTALL.md")
    assert "%USERPROFILE%" not in agent_install and "$env:USERPROFILE\\FluentFlow" in agent_install


# --- where a setting lands, read as the service reads it ----------------------

def _cases(repo: Path, home: Path, other: Path):
    """(.env line, name, environment, expected): expected is 'inside', or
    'unsure', or the outside path."""
    return [
        ("FLUENTFLOW_DATA_DIR=data", "FLUENTFLOW_DATA_DIR", {}, "inside"),
        ("FLUENTFLOW_DATA_DIR='./x/../data'", "FLUENTFLOW_DATA_DIR", {}, "inside"),
        ("FLUENTFLOW_DATA_DIR='%USERPROFILE%/FluentFlow/data'", "FLUENTFLOW_DATA_DIR", {}, "inside"),
        (f"FLUENTFLOW_DATA_DIR='{repo}/数据'", "FLUENTFLOW_DATA_DIR", {}, "inside"),
        (f"FLUENTFLOW_HOME='{repo}'\nFLUENTFLOW_DATA_DIR='${{FLUENTFLOW_HOME}}/data'", "FLUENTFLOW_DATA_DIR", {}, "inside"),
        ("FLUENTFLOW_DATA_DIR=$HOME/data", "FLUENTFLOW_DATA_DIR", {}, "unsure"),
        ("FLUENTFLOW_DATA_DIR='${HOME}/数据'", "FLUENTFLOW_DATA_DIR", {"HOME": str(home)}, str(home / "数据")),
        ("HF_HOME=~/models", "HF_HOME", {"HOME": str(home)}, str(home / "models")),
        (f"FLUENTFLOW_SOURCE_DIR='{other}/sources'", "FLUENTFLOW_SOURCE_DIR", {}, str(other / "sources")),
        (f"FLUENTFLOW_SOURCE_DIR='{other}/sources'", "FLUENTFLOW_SOURCE_DIR", {"FLUENTFLOW_SOURCE_DIR": "data"}, "inside"),
    ]


@pytest.mark.skipif(not POSIX or not _BASHES, reason="the macOS helpers, run by bash")
def test_the_mac_helpers_place_settings_where_the_service_does(tmp_path):
    repo, home, other = tmp_path / "app", tmp_path / "home", tmp_path / "elsewhere"
    repo.mkdir()
    home.mkdir()
    script = (
        f'set -euo pipefail; source "{MAC_HELPER}"; v="$(ff_setting "$1" "$2")"; '
        'if p="$(ff_place "$1" "$v")"; then if ff_inside "$p" "$1"; then echo inside; else echo "$p"; fi; '
        'else echo unsure; fi'
    )
    for line, name, extra, expected in _cases(repo, home, other):
        (repo / ".env").write_text(line + "\n", encoding="utf-8")
        env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "en_US.UTF-8", **extra}
        for bash in _BASHES:
            result = subprocess.run([bash, "-c", script, "-", str(repo), name], capture_output=True, text=True, env=env)
            assert result.returncode == 0, (bash, line, result.stderr)
            assert result.stdout.strip() == expected, (bash, line)


@pytest.mark.skipif(not POSIX, reason="macOS paths")
def test_the_mac_helpers_see_through_links(tmp_path):
    real = tmp_path / "real"
    (real / "app").mkdir(parents=True)
    (tmp_path / "link").symlink_to(real)
    script = f'source "{MAC_HELPER}"; ff_inside "$1" "$2" && echo inside || echo outside'
    result = subprocess.run(["bash", "-c", script, "-", str(tmp_path / "link" / "app" / "data"), str(real / "app")],
                            capture_output=True, text=True)
    assert result.stdout.strip() == "inside"


def _powershell(script: str, env: dict | None = None) -> subprocess.CompletedProcess:
    # Without the test run's own data folder and the other places it sets:
    # a setting in the environment wins over .env, as in the service.
    base = {k: v for k, v in os.environ.items() if not k.upper().startswith("FLUENTFLOW_") and k.upper() != "HF_HOME"}
    return subprocess.run([_POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, encoding="utf-8", env={**base, **(env or {})})


@pytest.mark.skipif(not _POWERSHELL, reason="the Windows helpers, run by Windows PowerShell")
def test_the_windows_helpers_place_settings_where_the_service_does(tmp_path):
    repo, home, other = tmp_path / "app", tmp_path / "home", tmp_path / "elsewhere"
    repo.mkdir()
    home.mkdir()
    for line, name, extra, expected in _cases(repo, home, other):
        if name == "HF_HOME":  # ~ is the user's profile folder on Windows
            extra = {"USERPROFILE": str(home)}
        # Without a BOM, as the installer writes it: Windows PowerShell's
        # Get-Content would read it in the system code page.
        (repo / ".env").write_bytes((line + "\r\n").encode("utf-8"))
        script = (
            "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
            f". '{WIN_HELPER}'; $p = Resolve-FluentFlowPlace '{repo}' (Get-FluentFlowSetting '{repo}' '{name}'); "
            f"if (-not $p.Sure) {{ 'unsure' }} elseif (Test-FluentFlowInside $p.Path '{repo}') {{ 'inside' }} else {{ $p.Path }}"
        )
        result = _powershell(script, extra)
        assert result.returncode == 0, (line, result.stderr)
        assert result.stdout.strip().lower() == expected.replace("/", "\\").lower(), line


@pytest.mark.skipif(not _POWERSHELL, reason="install.ps1, run by Windows PowerShell")
@pytest.mark.parametrize("asked", ["{tmp}\\FF\\", "{tmp}\\FF", "$env:FF_TEST_BASE\\FF", "%FF_TEST_BASE%\\FF", "FF\\"])
def test_the_install_folder_is_absolute_without_a_trailing_separator(tmp_path, asked):
    function = _powershell_function(_read(ROOT / "install.ps1"), "Get-FluentFlowInstallRoot")
    assert function
    script = f"Set-Location '{tmp_path}'; {function}\nGet-FluentFlowInstallRoot '{asked.format(tmp=tmp_path)}'"
    result = _powershell(script, {"FF_TEST_BASE": str(tmp_path)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().lower() == str(tmp_path / "FF").lower()


@pytest.mark.skipif(not _POWERSHELL, reason="install.ps1, run by Windows PowerShell")
def test_a_drive_root_is_not_an_install_folder():
    function = _powershell_function(_read(ROOT / "install.ps1"), "Get-FluentFlowInstallRoot")
    result = _powershell(f"{function}\nGet-FluentFlowInstallRoot 'D:\\'")
    assert result.returncode != 0


# --- the data folder's own contents ------------------------------------------

def _registered() -> list[str]:
    lines = (line.split("#", 1)[0].strip() for line in _read(REGISTRY).splitlines())
    return [line for line in lines if line]


def _is_registered(name: str) -> bool:
    from fnmatch import fnmatchcase
    return any(fnmatchcase(name, pattern) for pattern in _registered())


def _python_data_children() -> dict[str, str]:
    """First path part of every place the Python code puts in the data
    folder: runtime_path(VAR, "part", ...), app_data_root() / "part" (also
    through a variable holding it) and .joinpath("part")."""
    found: dict[str, str] = {}
    # Only a test copy's own folder, never an install's.
    skipped = {"backend/core/test_copy.py", "scripts/prepare_test_copy.py"}
    for path in [*sorted((ROOT / "backend").rglob("*.py")), *sorted((ROOT / "scripts").rglob("*.py"))]:
        relative = path.relative_to(ROOT).as_posix()
        if relative in skipped:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
                     and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
                     for t in n.targets if isinstance(t, ast.Name)}

        def text(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.Name):
                return constants.get(node.id)
            return None

        def called(node, name):
            func = node.func if isinstance(node, ast.Call) else None
            return func is not None and (getattr(func, "id", None) == name or getattr(func, "attr", None) == name)

        aliases: set[str] = set()

        def is_root(node):
            if called(node, "app_data_root"):
                return True
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("expanduser", "resolve"):
                return is_root(node.func.value)
            return isinstance(node, ast.Name) and node.id in aliases

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and is_root(node.value):
                aliases.update(t.id for t in node.targets if isinstance(t, ast.Name))
        for node in ast.walk(tree):
            part = None
            if called(node, "runtime_path") and len(node.args) >= 2:
                part = text(node.args[1])
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and is_root(node.left):
                part = text(node.right)
            elif called(node, "joinpath") and is_root(node.func.value) and node.args:
                part = text(node.args[0])
            if part:
                found.setdefault(part.split("/")[0], relative)
    return found


def _script_data_children() -> dict[str, str]:
    patterns = [
        r"\$\{DATA_DIR\}/([^/\"\s}]+)",
        r"Join-Path \(Get-FluentFlowDataDir \$Repo\) \"([^\"\\]+)",
        r"Join-Path \$FfmpegDataDir \"([^\"\\]+)\"",
    ]
    found: dict[str, str] = {}
    scripts = [ROOT / "install.sh", ROOT / "install.ps1", *sorted((ROOT / "launchers").rglob("*.sh")),
               *sorted((ROOT / "launchers").rglob("*.ps1"))]
    for path in scripts:
        for pattern in patterns:
            for match in re.finditer(pattern, _read(path)):
                found.setdefault(match.group(1).split("\\")[0], path.relative_to(ROOT).as_posix())
    return found


def test_every_place_the_code_writes_in_the_data_folder_is_registered():
    found = {**_script_data_children(), **_python_data_children()}
    # The scan sees the parts it should (guards the scan itself).
    assert {"fluentflow_jobs.sqlite", "sources", "bin", "tools", "logs", "yt_dlp_last_update",
            ".fluentflow-write-check", "note_skills"} <= set(found)
    missing = {name: where for name, where in found.items() if not _is_registered(name)}
    assert not missing, f"add these to {REGISTRY.relative_to(ROOT)}: {missing}"


def test_the_sqlite_companions_and_temporary_files_are_registered():
    for name in ("fluentflow_jobs.sqlite-wal", "fluentflow_events.sqlite-shm", ".recommended_models.json.ab12.tmp"):
        assert _is_registered(name), name
    for name in ("mine.txt", "Documents", "fluentflow_notes_backup"):
        assert not _is_registered(name), name


def test_both_uninstallers_read_the_registry():
    assert "data-folder-contents.txt" in _read(ROOT / "launchers" / "macos" / "uninstall-local.sh")
    assert "data-folder-contents.txt" in _read(ROOT / "launchers" / "windows" / "uninstall-local.ps1")


# --- the Mac scripts end to end, in a sandbox --------------------------------

def _sandbox_env(tmp_path: Path) -> dict:
    home = tmp_path / "home"
    (home / "Desktop").mkdir(parents=True, exist_ok=True)
    # No claude or codex on PATH: the real registrations are not touched.
    # A port nothing listens on: the owner's running service is not touched.
    return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(home), "LANG": "en_US.UTF-8",
            "FLUENTFLOW_LOCAL_PORT": "9", "CODEX_HOME": str(home / ".codex")}


def _fake_code(folder: Path, version: str = "0.1.0", setup_exit: int = 1) -> Path:
    """A code folder with the scripts under test and a setup that does nothing."""
    (folder / "backend").mkdir(parents=True)
    (folder / "backend" / "local_main.py").write_text("", encoding="utf-8")
    (folder / "VERSION").write_text(version + "\n", encoding="utf-8")
    macos = folder / "launchers" / "macos"
    macos.mkdir(parents=True)
    for name in ("uninstall-local.sh", "fluentflow-env.sh", "count_active_jobs.py"):
        shutil.copy2(ROOT / "launchers" / "macos" / name, macos / name)
    shutil.copy2(REGISTRY, folder / "launchers" / REGISTRY.name)
    (macos / "setup-local.sh").write_text(f"#!/bin/bash\necho fake setup\nexit {setup_exit}\n", encoding="utf-8")
    return folder


def _install(tmp_path: Path, folder: Path, source: Path) -> subprocess.CompletedProcess:
    env = {**_sandbox_env(tmp_path), "FLUENTFLOW_INSTALL_DIR": str(folder), "FLUENTFLOW_INSTALL_SOURCE": str(source)}
    return subprocess.run(["bash", str(ROOT / "install.sh"), "--skip-model"], capture_output=True, text=True,
                          env=env, cwd=tmp_path, timeout=120)


def _uninstall(tmp_path: Path, folder: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(folder / "launchers" / "macos" / "uninstall-local.sh"), *args],
                          capture_output=True, text=True, env=_sandbox_env(tmp_path), cwd=tmp_path, timeout=120)


mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="the macOS installer and uninstaller")


@mac_only
@pytest.mark.parametrize("line", ["FLUENTFLOW_DATA_DIR=data", "FLUENTFLOW_DATA_DIR='${FLUENTFLOW_HOME_TEST}/data'",
                                  "FLUENTFLOW_SOURCE_DIR=$HOME/sources"])
def test_an_update_stops_when_data_may_be_in_the_code_folder(tmp_path, line):
    app = _fake_code(tmp_path / "app")
    env_text = f"FLUENTFLOW_HOME_TEST='{app}'\n{line}\n"
    (app / ".env").write_text(env_text, encoding="utf-8")
    (app / "data").mkdir()
    (app / "data" / "fluentflow_jobs.sqlite").write_text("tasks", encoding="utf-8")
    source = _fake_code(tmp_path / "release", version="9.0.0")

    result = _install(tmp_path, app, source)

    assert result.returncode != 0
    assert "代码目录" in result.stderr, result.stderr
    assert (app / "data" / "fluentflow_jobs.sqlite").read_text(encoding="utf-8") == "tasks"
    assert (app / "VERSION").read_text(encoding="utf-8").strip() == "0.1.0"
    assert not (tmp_path / "app.new").exists() and not (tmp_path / "app.old").exists()


@mac_only
def test_an_update_keeps_a_copy_the_user_made_of_the_code_folder(tmp_path):
    source_old = _fake_code(tmp_path / "release-old", version="0.1.0")
    app = tmp_path / "app"
    _install(tmp_path, app, source_old)  # the fake setup fails; the code is in place, as the installer left it
    assert (app / "backend" / "local_main.py").is_file()
    shutil.copytree(app, tmp_path / "app.old", symlinks=True)
    (tmp_path / "app.old" / "my-notes.txt").write_text("mine", encoding="utf-8")

    result = _install(tmp_path, app, _fake_code(tmp_path / "release-new", version="9.0.0"))

    assert result.returncode != 0
    assert (tmp_path / "app.old" / "my-notes.txt").read_text(encoding="utf-8") == "mine"
    assert (app / "VERSION").read_text(encoding="utf-8").strip() == "0.1.0"


@mac_only
def test_what_a_failed_update_left_is_still_cleaned_up(tmp_path):
    app = tmp_path / "app"
    _install(tmp_path, app, _fake_code(tmp_path / "release-old", version="0.1.0"))
    first = _install(tmp_path, app, _fake_code(tmp_path / "release-new", version="9.0.0"))
    assert "已换回 0.1.0" in first.stderr and (tmp_path / "app.failed").is_dir()
    assert not (app / ".fluentflow-update").exists()  # the copy in use carries no marker

    again = _install(tmp_path, app, tmp_path / "release-new")
    assert "不是 FluentFlow 的更新留下的" not in again.stderr and "已换回 0.1.0" in again.stderr

    result = _uninstall(tmp_path, app, "--yes")
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "app.failed").exists() and not app.exists()


@mac_only
def test_the_uninstaller_keeps_the_code_folder_when_data_may_be_inside(tmp_path):
    app = _fake_code(tmp_path / "app")
    (app / ".env").write_text("FLUENTFLOW_DATA_DIR=data\n", encoding="utf-8")
    (app / "data").mkdir()
    (app / "data" / "fluentflow_jobs.sqlite").write_text("tasks", encoding="utf-8")

    result = _uninstall(tmp_path, app, "--models", "--yes")

    assert result.returncode == 0, result.stderr
    assert (app / "data" / "fluentflow_jobs.sqlite").read_text(encoding="utf-8") == "tasks"
    assert (app / "backend" / "local_main.py").is_file()


@mac_only
@pytest.mark.parametrize("dry_run", [True, False])
def test_removing_all_data_leaves_what_is_not_fluentflows(tmp_path, dry_run):
    app = _fake_code(tmp_path / "app")
    data = tmp_path / "共用文件夹"
    (data / "sources").mkdir(parents=True)
    (data / "fluentflow_jobs.sqlite").write_text("tasks", encoding="utf-8")
    (data / "fluentflow_jobs.sqlite-wal").write_text("wal", encoding="utf-8")
    (data / "我的笔记.txt").write_text("mine", encoding="utf-8")
    (data / "Photos").mkdir()
    (app / ".env").write_text(f"FLUENTFLOW_DATA_DIR='{data}'\n", encoding="utf-8")

    result = _uninstall(tmp_path, app, "--all", "--yes", *(["--dry-run"] if dry_run else []))

    assert result.returncode == 0, result.stderr
    assert "我的笔记.txt" in result.stdout and "Photos" in result.stdout
    assert (data / "我的笔记.txt").read_text(encoding="utf-8") == "mine" and (data / "Photos").is_dir()
    assert (data / "fluentflow_jobs.sqlite").exists() == dry_run
    assert (data / "fluentflow_jobs.sqlite-wal").exists() == dry_run
    assert (data / "sources").exists() == dry_run


@mac_only
def test_removing_all_data_removes_a_folder_that_is_only_fluentflows(tmp_path):
    app = _fake_code(tmp_path / "app")
    data = tmp_path / "data"
    (data / "sources").mkdir(parents=True)
    (data / "fluentflow_jobs.sqlite").write_text("tasks", encoding="utf-8")
    (app / ".env").write_text(f"FLUENTFLOW_DATA_DIR='{data}'\n", encoding="utf-8")

    result = _uninstall(tmp_path, app, "--all", "--yes")

    assert result.returncode == 0, result.stderr
    assert not data.exists()


@mac_only
def test_the_uninstaller_leaves_a_copy_the_user_made(tmp_path):
    app = _fake_code(tmp_path / "app")
    (app / ".fluentflow-update").write_text("made by a FluentFlow Local install or update\n", encoding="utf-8")
    shutil.copytree(app, tmp_path / "app.old", symlinks=True)

    result = _uninstall(tmp_path, app, "--yes")

    assert result.returncode == 0, result.stderr
    assert not app.exists()
    assert (tmp_path / "app.old" / "backend" / "local_main.py").is_file()


@mac_only
def test_the_uninstaller_stops_programs_running_from_the_code_folder(tmp_path):
    app = _fake_code(tmp_path / "app")
    (app / "data").mkdir()
    (app / ".env").write_text(f"FLUENTFLOW_DATA_DIR='{tmp_path / 'data'}'\n", encoding="utf-8")
    # Stands in for the background model download: its command line names a
    # file in the code folder, as the venv's python running stt_model.py does.
    downloader = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", str(app / "scripts" / "stt_model.py")])
    try:
        result = _uninstall(tmp_path, app, "--models", "--yes")
        assert result.returncode == 0, result.stderr
        for _ in range(20):
            if downloader.poll() is not None:
                break
            time.sleep(0.25)
        assert downloader.poll() is not None
    finally:
        if downloader.poll() is None:
            os.kill(downloader.pid, signal.SIGKILL)
            downloader.wait()
