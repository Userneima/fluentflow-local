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

SHARED_BASH = ("ff_env_file_value", "ff_unquote", "ff_setting", "ff_normalize", "ff_physical_path", "ff_place", "ff_lower", "ff_physical",
               "ff_inside", "ff_update_marker_says", "ff_mark_update_copy", "ff_codex_home", "ff_stop_folder_programs")
SHARED_POWERSHELL = ("Expand-FluentFlowReferences", "ConvertFrom-FluentFlowQuoted", "ConvertFrom-FluentFlowEnvValue", "Get-FluentFlowEnvFileValue",
                     "Get-FluentFlowSetting", "Get-FluentFlowRealPath", "Get-FluentFlowLinkTarget", "Resolve-FluentFlowPlace", "Test-FluentFlowInside",
                     "Test-FluentFlowUpdateCopy", "Set-FluentFlowUpdateCopyAs", "Set-FluentFlowUpdateCopy",
                     "Get-FluentFlowUpdatingMarker", "Start-FluentFlowUpdating", "Stop-FluentFlowUpdating")


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
        # A reference takes the line above it, not a later one of the same name.
        (f"BASE=.\nFLUENTFLOW_DATA_DIR=${{BASE}}/data\nBASE={other}", "FLUENTFLOW_DATA_DIR", {}, "inside"),
        ("FLUENTFLOW_DATA_DIR=${FF_SURELY_UNSET:-data}", "FLUENTFLOW_DATA_DIR", {}, "inside"),
        # Escapes in quotes are decoded as the service decodes them.
        # (Forward slashes: in double quotes C:\Users\runneradmin holds \r, which the
        # service reads as a carriage return.)
        (f'FLUENTFLOW_DATA_DIR="{other.as_posix()}/x\\\\notes"', "FLUENTFLOW_DATA_DIR", {}, f"{other}/x\\notes"),
        # A quote not closed on its line: the service reads the next line into
        # the value, and that line is no setting of its own any more.
        (f"FLUENTFLOW_DATA_DIR='{other}/data\\'\nOTHER=1", "FLUENTFLOW_DATA_DIR", {}, "unsure"),
        (f"NOTE='it\\'\nFLUENTFLOW_DATA_DIR='{other}/data'", "FLUENTFLOW_DATA_DIR", {}, "unsure"),
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


def _bash_place(bash: str, repo: Path, env: dict) -> subprocess.CompletedProcess:
    script = (
        f'set -euo pipefail; source "{MAC_HELPER}"; v="$(ff_setting "$1" FLUENTFLOW_DATA_DIR)"; '
        'if p="$(ff_place "$1" "$v")"; then if ff_inside "$p" "$1"; then echo inside; else echo "$p"; fi; '
        'else echo unsure; fi'
    )
    return subprocess.run([bash, "-c", script, "-", str(repo)], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", **env}, timeout=10)


@pytest.mark.skipif(not POSIX or not _BASHES, reason="the macOS helpers, run by bash")
@pytest.mark.parametrize(("raw", "expected"), [
    (b"\xef\xbb\xbfFLUENTFLOW_DATA_DIR=data\n", "inside"),  # a BOM before the first line
    (b"FLUENTFLOW_DATA_DIR='/tmp/\xff\xfe/data'\n", "unsure"),  # not UTF-8
])
def test_the_mac_helpers_read_the_file_as_the_service_does(tmp_path, raw, expected):
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".env").write_bytes(raw)
    for bash in _BASHES:
        result = _bash_place(bash, repo, {"HOME": str(tmp_path)})
        assert result.returncode == 0, (bash, result.stderr)
        assert result.stdout.strip() == expected, bash


@pytest.mark.skipif(not POSIX or not _BASHES, reason="the macOS helpers, run by bash")
def test_a_reference_holding_an_ampersand_is_expanded_under_every_bash(tmp_path):
    """bash 5.2 puts the match where ${x//pat/rep} has an &: the old loop never ended."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".env").write_text("FLUENTFLOW_DATA_DIR=${MYDIR}/data\n", encoding="utf-8")
    for bash in _BASHES:
        result = _bash_place(bash, repo, {"HOME": str(tmp_path), "MYDIR": "/Users/x/R&D"})
        assert result.stdout.strip() == "/Users/x/R&D/data", bash


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
        if expected not in ("inside", "unsure"):
            expected = expected.replace("\\\\", "\\")
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


@pytest.mark.skipif(not _POWERSHELL, reason="the Windows helpers, run by Windows PowerShell")
@pytest.mark.parametrize(("raw", "expected"), [
    (b"\xef\xbb\xbfFLUENTFLOW_DATA_DIR=data\r\n", "inside"),
    (b"FLUENTFLOW_DATA_DIR='C:\\\xff\xfe\\data'\r\n", "unsure"),  # read as U+FFFD
])
def test_the_windows_helpers_read_the_file_as_the_service_does(tmp_path, raw, expected):
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".env").write_bytes(raw)
    script = (
        f". '{WIN_HELPER}'; $p = Resolve-FluentFlowPlace '{repo}' (Get-FluentFlowSetting '{repo}' 'FLUENTFLOW_DATA_DIR'); "
        f"if (-not $p.Sure) {{ 'unsure' }} elseif (Test-FluentFlowInside $p.Path '{repo}') {{ 'inside' }} else {{ $p.Path }}"
    )
    result = _powershell(script)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def _install_root_script() -> str:
    """Get-FluentFlowInstallRoot with what it calls (the real folder is walked
    the way the system opens it, so the path helpers come along)."""
    text = _read(ROOT / "install.ps1")
    parts = []
    for name in ("Get-FluentFlowRealPath", "Get-FluentFlowLinkTarget", "Get-FluentFlowInstallRoot"):
        function = _powershell_function(text, name)
        assert function, f"{name} missing from install.ps1"
        parts.append(function)
    return "\n".join(parts)

@pytest.mark.skipif(not _POWERSHELL, reason="install.ps1, run by Windows PowerShell")
@pytest.mark.parametrize("asked", ["{tmp}\\FF\\", "{tmp}\\FF", "$env:FF_TEST_BASE\\FF", "%FF_TEST_BASE%\\FF", "FF\\"])
def test_the_install_folder_is_absolute_without_a_trailing_separator(tmp_path, asked):
    script = f"Set-Location '{tmp_path}'; {_install_root_script()}\nGet-FluentFlowInstallRoot '{asked.format(tmp=tmp_path)}'"
    result = _powershell(script, {"FF_TEST_BASE": str(tmp_path)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().lower() == str(tmp_path / "FF").lower()


@pytest.mark.skipif(not _POWERSHELL, reason="install.ps1, run by Windows PowerShell")
def test_a_drive_root_is_not_an_install_folder():
    result = _powershell(f"{_install_root_script()}\nGet-FluentFlowInstallRoot 'D:\\'")
    assert result.returncode != 0


# --- the data folder's own contents ------------------------------------------

def _registered() -> list[str]:
    lines = (line.split("#", 1)[0].split() for line in _read(REGISTRY).splitlines())
    return [words[0] for words in lines if words]


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
    for name in ("fluentflow_jobs.sqlite-wal", "fluentflow_events.sqlite-shm", "fluentflow_jobs.sqlite-journal",
                 ".recommended_models.json.ab12.tmp"):
        assert _is_registered(name), name
    # A backup the user named after the task store is theirs, not SQLite's.
    for name in ("mine.txt", "Documents", "fluentflow_notes_backup",
                 "fluentflow_jobs.sqlite.bak-20261126", "fluentflow_jobs.sqlite-backup"):
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


def _install(tmp_path: Path, folder: Path, source: Path, **extra: str) -> subprocess.CompletedProcess:
    env = {**_sandbox_env(tmp_path), "FLUENTFLOW_INSTALL_DIR": str(folder), "FLUENTFLOW_INSTALL_SOURCE": str(source), **extra}
    return subprocess.run(["bash", str(ROOT / "install.sh"), "--skip-model"], capture_output=True, text=True,
                          env=env, cwd=tmp_path, timeout=120)


def _uninstall(tmp_path: Path, folder: Path, *args: str, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(folder / "launchers" / "macos" / "uninstall-local.sh"), *args],
                          capture_output=True, text=True, env={**_sandbox_env(tmp_path), **extra}, cwd=tmp_path, timeout=120)


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
def test_an_update_checks_the_env_file_even_when_the_window_sets_the_same_name(tmp_path):
    """The service a launcher starts later may not get this window's variable."""
    app = _fake_code(tmp_path / "app")
    (app / ".env").write_text("FLUENTFLOW_DATA_DIR=data\n", encoding="utf-8")
    (app / "data").mkdir()
    result = _install(tmp_path, app, _fake_code(tmp_path / "release", version="9.0.0"),
                      FLUENTFLOW_DATA_DIR=str(tmp_path / "elsewhere"))
    assert result.returncode != 0 and "代码目录" in result.stderr, result.stderr
    assert (app / "VERSION").read_text(encoding="utf-8").strip() == "0.1.0"


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
def test_common_names_in_a_shared_folder_go_only_with_what_fluentflow_puts_in_them(tmp_path):
    app = _fake_code(tmp_path / "app")
    data = tmp_path / "shared"
    for folder in ("models/speaker", "bin", "logs", "sources/0508c4e78c784bdc9a13eadbebc124c8", "mine"):
        (data / folder).mkdir(parents=True, exist_ok=True)
    (data / "fluentflow_jobs.sqlite").write_text("tasks", encoding="utf-8")
    (data / "logs" / "model-download.log").write_text("log", encoding="utf-8")
    (data / "bin" / "ffmpeg").write_text("tool", encoding="utf-8")
    (data / "bin" / "my-script").write_text("mine", encoding="utf-8")  # the user's own bin
    (data / "models" / "my-model.bin").write_text("mine", encoding="utf-8")  # and their own models
    (app / ".env").write_text(f"FLUENTFLOW_DATA_DIR='{data}'\n", encoding="utf-8")

    plan = _uninstall(tmp_path, app, "--all", "--yes", "--dry-run")
    assert "bin" in plan.stdout.split("认不出")[-1] and "models" in plan.stdout.split("认不出")[-1]
    result = _uninstall(tmp_path, app, "--all", "--yes")

    assert result.returncode == 0, result.stderr
    assert (data / "bin" / "my-script").exists() and (data / "bin" / "ffmpeg").exists()
    assert (data / "models" / "my-model.bin").exists()
    assert not (data / "fluentflow_jobs.sqlite").exists() and not (data / "logs").exists()
    assert not (data / "sources").exists()


@mac_only
def test_a_folder_of_only_fluentflow_names_is_kept_when_one_holds_the_users_files(tmp_path):
    app = _fake_code(tmp_path / "app")
    data = tmp_path / "data"
    (data / "models").mkdir(parents=True)
    (data / "models" / "my-model.bin").write_text("mine", encoding="utf-8")
    (data / "fluentflow_jobs.sqlite").write_text("tasks", encoding="utf-8")
    (app / ".env").write_text(f"FLUENTFLOW_DATA_DIR='{data}'\n", encoding="utf-8")

    result = _uninstall(tmp_path, app, "--all", "--yes")

    assert result.returncode == 0, result.stderr
    assert (data / "models" / "my-model.bin").exists() and not (data / "fluentflow_jobs.sqlite").exists()


@mac_only
def test_removing_the_program_leaves_a_bin_with_the_users_files(tmp_path):
    app = _fake_code(tmp_path / "app")
    data = tmp_path / "data"
    (data / "bin").mkdir(parents=True)
    (data / "bin" / "ffmpeg").write_text("tool", encoding="utf-8")
    (data / "bin" / "my-script").write_text("mine", encoding="utf-8")
    (app / ".env").write_text(f"FLUENTFLOW_DATA_DIR='{data}'\n", encoding="utf-8")

    result = _uninstall(tmp_path, app, "--models", "--yes")

    assert result.returncode == 0, result.stderr
    assert (data / "bin" / "my-script").exists()


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
    # Stands in for the background model download: a python running a script
    # in the code folder, as the venv's python runs stt_model.py.
    (app / "scripts").mkdir()
    (app / "scripts" / "stt_model.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    downloader = subprocess.Popen([sys.executable, str(app / "scripts" / "stt_model.py")])
    # Another program that only names the folder somewhere in its arguments.
    bystander = subprocess.Popen([sys.executable, str(tmp_path / "other.py"), str(app) + "/notes"])
    try:
        time.sleep(0.5)
        result = _uninstall(tmp_path, app, "--models", "--yes")
        assert result.returncode == 0, result.stderr
        for _ in range(20):
            if downloader.poll() is not None:
                break
            time.sleep(0.25)
        assert downloader.poll() is not None
        assert bystander.poll() is None
    finally:
        for process in (downloader, bystander):
            if process.poll() is None:
                os.kill(process.pid, signal.SIGKILL)
                process.wait()


def test_the_mac_uninstaller_counts_busy_tasks_as_the_launcher_does():
    """A task reads completed while its cut and note still run; a grep over
    the list's statuses missed them and waited only 3 seconds."""
    text = _read(ROOT / "launchers" / "macos" / "uninstall-local.sh")
    assert "count_active_jobs.py" in text
    assert "/jobs?limit" not in text and "grep -c" not in text


# --- the setup puts tools and models where the service looks ----------------
# Requirement (2026-11-26, decision 7.11): a data folder written as a relative
# path is placed from the code folder, as the service places it, whatever
# folder the setup is run from; one that cannot be placed for sure stops the
# setup before anything is installed, as updating and uninstalling do.

def _setup_code(folder: Path, env_line: str) -> Path:
    (folder / "backend").mkdir(parents=True)
    (folder / "backend" / "local_main.py").write_text("", encoding="utf-8")
    macos = folder / "launchers" / "macos"
    macos.mkdir(parents=True)
    for name in ("setup-local.sh", "fluentflow-env.sh"):
        shutil.copy2(ROOT / "launchers" / "macos" / name, macos / name)
    (folder / ".env").write_text(env_line + "\n", encoding="utf-8")
    return folder


def _setup(tmp_path: Path, folder: Path, cwd: Path) -> subprocess.CompletedProcess:
    # No download gets out: curl fails, so the run stops at fetching FFmpeg,
    # after the data folder's bin is made.
    fake = tmp_path / "fake-bin"
    fake.mkdir(exist_ok=True)
    (fake / "curl").write_text("#!/bin/bash\nexit 7\n", encoding="utf-8")
    (fake / "curl").chmod(0o755)
    env = _sandbox_env(tmp_path)
    env["PATH"] = f"{fake}:{env['PATH']}"
    cwd.mkdir(parents=True, exist_ok=True)
    return subprocess.run(["bash", str(folder / "launchers" / "macos" / "setup-local.sh"), "--skip-desktop", "--skip-model"],
                          capture_output=True, text=True, env=env, cwd=cwd, timeout=120)


@mac_only
def test_setup_places_a_relative_data_folder_from_the_code_folder(tmp_path):
    folder = _setup_code(tmp_path / "app", "FLUENTFLOW_DATA_DIR=data\nHF_HOME=./models")
    elsewhere = tmp_path / "elsewhere"
    run = _setup(tmp_path, folder, elsewhere)
    assert (folder / "data" / "bin").is_dir(), run.stdout + run.stderr
    assert not (elsewhere / "data").exists()


@mac_only
@pytest.mark.parametrize("env_line", ["FLUENTFLOW_DATA_DIR=$HOME/data", "FLUENTFLOW_DATA_DIR='unclosed"])
def test_setup_stops_on_a_data_folder_it_cannot_place(tmp_path, env_line):
    folder = _setup_code(tmp_path / "app", env_line)
    elsewhere = tmp_path / "elsewhere"
    run = _setup(tmp_path, folder, elsewhere)
    assert run.returncode != 0
    assert "看不准在哪" in run.stderr and "什么都还没装" in run.stderr
    assert "FFmpeg" not in run.stdout
    assert not any(elsewhere.iterdir()) and not (folder / "data").exists()


def test_the_windows_setup_stops_on_a_place_it_cannot_tell():
    helper = _read(ROOT / "launchers" / "windows" / "fluentflow-env.ps1")
    body = helper[helper.index("function Import-FluentFlowEnv"):]
    body = body[:body.index("\n}\n")]
    assert "Resolve-FluentFlowPlace $Repo $Value" in body and "return $Unplaced" in body
    setup = _read(ROOT / "launchers" / "windows" / "setup-local.ps1")
    assert "$Unplaced = @(Import-FluentFlowEnv $Repo)" in setup
    assert setup.index("$Unplaced.Count -gt 0") < setup.index("Get-FluentFlowDataDir $Repo")


# --- an update that was interrupted, and the install it belongs to ----------

def _mark_update_copy(folder: Path) -> None:
    (folder / ".fluentflow-update").write_text(
        "Set aside by a FluentFlow Local update; the next update or the uninstaller removes it.\n"
        f"{folder}\n", encoding="utf-8")


@mac_only
def test_an_update_interrupted_while_swapping_puts_the_old_version_back(tmp_path):
    """The old version was moved aside and the new one never arrived: <dir> is
    missing and <dir>.old is the update's. Run as a fresh install, the next run
    left the previous version, and the user's install, beside a new copy."""
    aside = _fake_code(tmp_path / "app.old")
    _mark_update_copy(aside)

    result = _install(tmp_path, tmp_path / "app", _fake_code(tmp_path / "release", version="0.1.0"))

    assert "上一次更新" in result.stdout + result.stderr, result.stdout + result.stderr
    assert (tmp_path / "app" / "backend" / "local_main.py").is_file()
    assert (tmp_path / "app" / "VERSION").read_text(encoding="utf-8").strip() == "0.1.0"
    assert not (tmp_path / "app" / ".fluentflow-update").exists()
    assert not aside.exists()


@mac_only
def test_a_half_written_update_copy_is_named_not_trusted(tmp_path):
    """The marker is written last, so a copy that stopped half way has none: the
    next update must stop and say what it is, never delete it as an update's."""
    app = _fake_code(tmp_path / "app")
    half = tmp_path / "app.new"
    half.mkdir()
    (half / "backend").mkdir()
    (half / "backend" / "local_main.py").write_text("", encoding="utf-8")

    result = _install(tmp_path, app, _fake_code(tmp_path / "release", version="9.0.0"))

    assert result.returncode != 0
    assert "上一次更新没做完" in result.stderr, result.stderr
    assert half.exists() and (app / "VERSION").read_text(encoding="utf-8").strip() == "0.1.0"


@mac_only
def test_a_data_part_named_in_env_stops_the_update(tmp_path):
    """Every setting that can place the user's files, not only the data folder:
    FLUENTFLOW_SOURCE_DIR inside the code folder goes with it."""
    app = _fake_code(tmp_path / "app")
    (app / ".env").write_text("FLUENTFLOW_SOURCE_DIR=sources-copy\n", encoding="utf-8")
    (app / "sources-copy").mkdir()
    (app / "sources-copy" / "a.mp4").write_bytes(b"video")

    result = _install(tmp_path, app, _fake_code(tmp_path / "release", version="9.0.0"))

    assert result.returncode != 0 and "代码目录" in result.stderr, result.stderr
    assert (app / "sources-copy" / "a.mp4").read_bytes() == b"video"
    assert not (tmp_path / "app.new").exists() and not (tmp_path / "app.old").exists()


@mac_only
def test_the_install_folder_is_the_one_the_running_service_reports(tmp_path):
    """Run again without a folder, the installer updated the copy it found in the
    default place: an install in a folder the user chose got a second copy."""
    import http.server
    import json
    import threading

    chosen = _fake_code(tmp_path / "chosen" / "app")
    # Compact, as the service answers: {"edition":"local","install":{"code_dir":"..."}}
    answer = json.dumps({"status": "ok", "edition": "local", "install": {"code_dir": str(chosen)}},
                        separators=(",", ":")).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(answer)))
            self.end_headers()
            self.wfile.write(answer)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = str(server.server_address[1])
        result = _install(tmp_path, tmp_path / "unused", _fake_code(tmp_path / "release", version="0.1.0"),
                          **{"FLUENTFLOW_INSTALL_DIR": "", "FLUENTFLOW_LOCAL_PORT": port})
    finally:
        server.shutdown()

    assert "正在运行的 FluentFlow 装在" in result.stdout, result.stdout + result.stderr
    assert (chosen / "VERSION").read_text(encoding="utf-8").strip() == "0.1.0"
    assert not (tmp_path / "home" / "fluentflow-local").exists()


@mac_only
def test_nothing_to_delete_still_names_what_it_left(tmp_path):
    """The data folder held only files FluentFlow does not know: the run found
    nothing to delete and exited without saying they were left there."""
    app = _fake_code(tmp_path / "app")
    (app / ".env").write_text("FLUENTFLOW_DATA_DIR=data\n", encoding="utf-8")
    (app / "data" / "bin").mkdir(parents=True)
    (app / "data" / "bin" / "my-tool").write_text("mine", encoding="utf-8")

    result = _uninstall(tmp_path, app, "--models", "--yes")

    assert result.returncode == 0, result.stderr
    assert "没有找到需要删除的东西" in result.stdout
    assert "认不出" in result.stdout and "bin" in result.stdout.split("认不出")[-1]
    assert (app / "data" / "bin" / "my-tool").exists()


@mac_only
def test_unregistering_uses_the_scope_and_codex_folder_it_was_registered_with(tmp_path):
    """`claude mcp get` found a project-level entry and remove used "user", so it
    stayed; and the Codex config was read from ~/.codex although CODEX_HOME sets
    another folder."""
    app = _fake_code(tmp_path / "app")
    calls = tmp_path / "calls"
    calls.mkdir()
    fake = tmp_path / "fake-bin"
    fake.mkdir()
    (fake / "claude").write_text(
        "#!/bin/bash\n"
        "if [[ \"$1 $2\" == \"mcp get\" ]]; then\n"
        f"  echo 'fluentflow-local:'; echo '  Scope: Project config'; echo '  Args: python3 {app}/scripts/fluentflow_mcp_server.py'\n"
        "  echo 'To remove this server, run: claude mcp remove fluentflow-local -s project'\n"
        "  exit 0\n"
        "fi\n"
        f"echo \"claude $*\" >> '{calls}/log'\nexit 0\n", encoding="utf-8")
    (fake / "claude").chmod(0o755)
    (fake / "codex").write_text(f"#!/bin/bash\necho \"codex $*\" >> '{calls}/log'\n", encoding="utf-8")
    (fake / "codex").chmod(0o755)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        f"[mcp_servers.fluentflow-local]\ncommand = 'python3'\nargs = ['{app}/scripts/fluentflow_mcp_server.py']\n",
        encoding="utf-8")

    result = _uninstall(tmp_path, app, "--yes", CODEX_HOME=str(codex_home),
                        PATH=f"{fake}:/usr/bin:/bin:/usr/sbin:/sbin")

    assert result.returncode == 0, result.stderr
    log = (calls / "log").read_text(encoding="utf-8")
    assert "claude mcp remove --scope project fluentflow-local" in log, log
    assert "codex mcp remove fluentflow-local" in log, log


@mac_only
def test_a_link_before_a_dotdot_lands_where_the_system_puts_it(tmp_path):
    """A data folder written as link/../../data: the system follows the link and
    then backs up from the link's target, so the service and the setup put files
    somewhere else than the text says when only the text is normalized."""
    folder = _setup_code(tmp_path / "app", "FLUENTFLOW_DATA_DIR=inner/../../data")
    (tmp_path / "outside" / "deep" / "target").mkdir(parents=True)
    (folder / "inner").symlink_to(tmp_path / "outside" / "deep" / "target")

    run = _setup(tmp_path, folder, tmp_path / "elsewhere")

    # inner -> outside/deep/target, two levels up -> outside, then data.
    assert (tmp_path / "outside" / "data" / "bin").is_dir(), run.stdout + run.stderr
    assert not (tmp_path / "data").exists()


# --- the Windows halves -------------------------------------------------------
# These run in the GitHub Windows check (there is no PowerShell on the Mac this
# is maintained on). They run the scripts' own functions against temporary
# folders; the real install/update/uninstall run is the workflow's.

# What Repair-FluentFlowHome itself calls.
WIN_HELPERS_FOR_REPAIR = ("Expand-FluentFlowReferences", "ConvertFrom-FluentFlowQuoted", "ConvertFrom-FluentFlowEnvValue",
                          "Get-FluentFlowEnvFileValue", "Get-FluentFlowRealPath", "Get-FluentFlowLinkTarget",
                          "Resolve-FluentFlowPlace", "Test-FluentFlowInside", "Add-FluentFlowEnvLine",
                          "Set-FluentFlowEnvLine", "Get-FluentFlowHomeRoot", "Repair-FluentFlowHome",
                          "Test-FluentFlowUpdateCopy", "Set-FluentFlowUpdateCopy")

WIN_FUNCTIONS = ("Expand-FluentFlowReferences", "ConvertFrom-FluentFlowQuoted", "ConvertFrom-FluentFlowEnvValue",
                 "Get-FluentFlowEnvFileValue", "Get-FluentFlowSetting", "Get-FluentFlowRealPath",
                 "Resolve-FluentFlowPlace", "Get-FluentFlowLinkTarget", "Test-FluentFlowInside", "Add-FluentFlowEnvLine",
                 "Set-FluentFlowEnvLine", "Get-FluentFlowHomeRoot", "Repair-FluentFlowHome",
                 "Get-FluentFlowDataDir", "Add-FluentFlowFolderLines", "Set-FluentFlowUpdateCopyAs",
                 "Test-FluentFlowUpdateCopy", "Set-FluentFlowUpdateCopy", "Get-FluentFlowUpdatingMarker",
                 "Start-FluentFlowUpdating", "Stop-FluentFlowUpdating", "Get-FluentFlowEntryScript")


def _win_helper_script(*needs: str) -> str:
    """The helper's functions, all of them or the ones named (with a test that
    runs one function alone, the others it does not need stay out)."""
    text = _read(WIN_HELPER)
    parts = []
    for name in (needs or WIN_FUNCTIONS):
        function = _powershell_function(text, name)
        assert function, f"{name} missing from fluentflow-env.ps1"
        parts.append(function)
    return "\n".join(parts)
@pytest.mark.skipif(not _POWERSHELL, reason="the Windows helpers, run by Windows PowerShell")
def test_a_moved_folder_points_its_env_at_itself_again(tmp_path):
    """The whole FluentFlow folder moved to another drive: .env still names the
    old place, and the service would start on an empty data folder. The folder
    the installer marked is taken as the one this code belongs to, and what
    named the old place is pointed here again; a value pointing elsewhere is
    the user's and stays."""
    root = tmp_path / "FluentFlow"
    app = root / "app"
    app.mkdir(parents=True)
    (root / ".fluentflow-home").write_text("FluentFlow Local lives here", encoding="ascii")
    old = tmp_path / "OldDrive" / "FluentFlow"
    (app / ".env").write_text(
        f"FLUENTFLOW_HOME='{old}'\r\n"
        f"FLUENTFLOW_DATA_DIR='{old}\\data'\r\n"
        "HF_HOME='${FLUENTFLOW_HOME}\\models'\r\n"
        f"MY_OWN_DIR='{tmp_path / 'elsewhere'}'\r\n", encoding="utf-8")
    script = _win_helper_script() + (
        f"\n[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
        f"$changed = @(Repair-FluentFlowHome '{app}')\n"
        f"'changed=' + ($changed -join ',')\n"
        f"'home=' + (Get-FluentFlowEnvFileValue '{app}' 'FLUENTFLOW_HOME')\n"
        f"'data=' + (Get-FluentFlowEnvFileValue '{app}' 'FLUENTFLOW_DATA_DIR')\n"
        f"'own=' + (Get-FluentFlowEnvFileValue '{app}' 'MY_OWN_DIR')\n"
        f"'models=' + (Get-FluentFlowEnvFileValue '{app}' 'HF_HOME')\n")

    result = _powershell(script)

    assert result.returncode == 0, result.stderr
    lines = dict(line.split("=", 1) for line in result.stdout.strip().splitlines() if "=" in line)
    # Windows paths differ in case between what was written and what the disk has.
    for name, expected in (("home", root), ("data", root / "data"), ("models", root / "models"),
                           ("own", tmp_path / "elsewhere")):
        assert lines[name].lower() == str(expected).lower(), (name, lines)
    assert "FLUENTFLOW_HOME" in lines["changed"] and "FLUENTFLOW_DATA_DIR" in lines["changed"]


@pytest.mark.skipif(not _POWERSHELL, reason="the Windows helpers, run by Windows PowerShell")
def test_a_folder_still_in_its_old_place_is_left_alone(tmp_path):
    """The old place still holds an install the user keeps: its data is not this
    copy's to take over."""
    root = tmp_path / "FluentFlow"
    app = root / "app"
    app.mkdir(parents=True)
    (root / ".fluentflow-home").write_text("FluentFlow Local lives here", encoding="ascii")
    old = tmp_path / "Other" / "FluentFlow"
    old.mkdir(parents=True)
    (app / ".env").write_text(f"FLUENTFLOW_HOME='{old}'\r\n", encoding="utf-8")
    script = _win_helper_script() + (
        f"\n[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
        f"'changed=' + (@(Repair-FluentFlowHome '{app}') -join ',')\n"
        f"'home=' + (Get-FluentFlowEnvFileValue '{app}' 'FLUENTFLOW_HOME')\n")

    result = _powershell(script)

    assert result.returncode == 0, result.stderr
    assert "changed=" in result.stdout and "FLUENTFLOW_HOME" not in result.stdout.split("changed=")[1].splitlines()[0]
    assert f"home={old}".lower() in result.stdout.lower()


@pytest.mark.skipif(not _POWERSHELL, reason="the Windows helpers, run by Windows PowerShell")
def test_the_folder_lines_follow_fluentflow_home(tmp_path):
    """A one-folder install writes its data, models and caches under
    ${FLUENTFLOW_HOME}, so the repair above moves them all; an older layout's
    caches go into its data folder, where uninstalling can remove them."""
    root = tmp_path / "FluentFlow"
    app = root / "app"
    app.mkdir(parents=True)
    script = _win_helper_script() + (
        f"\nAdd-FluentFlowFolderLines '{app}' '{root}' $true\n")
    result = _powershell(script)
    assert result.returncode == 0, result.stderr
    env_text = (app / ".env").read_text(encoding="utf-8")
    for name, part in (("FLUENTFLOW_DATA_DIR", "data"), ("HF_HOME", "models"),
                       ("FLUENTFLOW_LINK_OUTPUT_DIR", "downloads"),
                       ("PIP_CACHE_DIR", "cache\\pip"), ("UV_CACHE_DIR", "cache\\uv"),
                       ("npm_config_cache", "cache\\npm")):
        assert f"{name}='${{FLUENTFLOW_HOME}}\\{part}'" in env_text, (name, env_text)

    legacy = tmp_path / "old-layout"
    legacy.mkdir()
    (legacy / ".env").write_text("", encoding="utf-8")
    script = _win_helper_script() + (
        f"\n$env:APPDATA = '{tmp_path / 'Roaming'}'\n"
        f"Add-FluentFlowFolderLines '{legacy}' '{legacy}' $false\n")
    result = _powershell(script)
    assert result.returncode == 0, result.stderr
    legacy_text = (legacy / ".env").read_text(encoding="utf-8")
    assert str(tmp_path / "Roaming" / "FluentFlow" / "cache" / "uv") in legacy_text


@pytest.mark.skipif(not _POWERSHELL, reason="the Windows helpers, run by Windows PowerShell")
def test_a_junction_before_a_dotdot_lands_where_the_system_puts_it(tmp_path):
    """A data folder written as a junction, then .., then data: the system
    follows the junction and backs up from its target, so the setup must place
    files there and not where the text suggests."""
    root = tmp_path / "app"
    root.mkdir()
    (tmp_path / "outside" / "deep" / "target").mkdir(parents=True)
    make = subprocess.run([_POWERSHELL, "-NoProfile", "-NonInteractive", "-Command",
                           f"New-Item -ItemType Junction -Path '{root / 'inner'}' "
                           f"-Target '{tmp_path / 'outside' / 'deep' / 'target'}' | Out-Null"],
                          capture_output=True, text=True)
    assert make.returncode == 0, make.stderr
    script = _win_helper_script() + (
        f"\n$p = Resolve-FluentFlowPlace '{root}' 'inner/../../data'\n"
        f"'path=' + $p.Path\n")
    result = _powershell(script)
    assert result.returncode == 0, result.stderr
    assert f"path={tmp_path / 'outside' / 'data'}".lower() in result.stdout.lower(), result.stdout


@pytest.mark.skipif(not _POWERSHELL, reason="the Windows uninstaller, run by Windows PowerShell")
def test_the_uninstaller_asks_the_code_for_a_chinese_data_folder(tmp_path):
    """The uninstaller asks the code where the data lives. Python's output was
    read in the system code page, so a Chinese path came back wrong (or, on an
    English Windows, the print itself failed) and it fell back to the raw
    setting: it left data behind rather than delete too much (2026-11-26)."""
    app = tmp_path / "app"
    (app / "backend" / "core").mkdir(parents=True)
    (app / "backend" / "local_main.py").write_text("", encoding="utf-8")
    (app / "backend" / "core" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy2(ROOT / "backend" / "core" / "runtime_paths.py", app / "backend" / "core" / "runtime_paths.py")
    subprocess.run([sys.executable, "-m", "venv", str(app / ".venv")], check=True, timeout=300)
    windows = app / "launchers" / "windows"
    windows.mkdir(parents=True)
    for name in ("uninstall-local.ps1", "fluentflow-env.ps1"):
        shutil.copy2(ROOT / "launchers" / "windows" / name, windows / name)
    shutil.copy2(REGISTRY, app / "launchers" / REGISTRY.name)
    data = tmp_path / "任务数据"
    (data / "sources").mkdir(parents=True)
    (data / "fluentflow_jobs.sqlite").write_text("tasks", encoding="utf-8")
    (app / ".env").write_text(f"FLUENTFLOW_DATA_DIR='{data}'\r\n", encoding="utf-8")
    # Without the test run's own data folder and the other places it sets: here
    # the .env line is what says where the data is.
    base = {k: v for k, v in os.environ.items() if not k.upper().startswith("FLUENTFLOW_") and k.upper() != "HF_HOME"}
    run = subprocess.run([_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                          str(windows / "uninstall-local.ps1"), "-Data", "-DryRun", "-Yes"],
                         capture_output=True, text=True, encoding="utf-8", timeout=300, env=base)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "问不到代码里的数据目录" not in run.stdout, run.stdout
    assert "任务数据" in run.stdout, run.stdout


@mac_only
def test_a_copy_beside_the_code_folder_that_is_left_is_named(tmp_path):
    """A copy of the code the uninstaller does not recognise (its marker names
    where it was before the folder moved) is left, and the run says so instead
    of looking like everything went."""
    app = _fake_code(tmp_path / "app")
    copy = _fake_code(tmp_path / "app.failed")
    (copy / ".fluentflow-update").write_text(
        "Set aside by a FluentFlow Local update; the next update or the uninstaller removes it.\n"
        f"{tmp_path}/somewhere-else/app.failed\n", encoding="utf-8")

    result = _uninstall(tmp_path, app, "--yes")

    assert result.returncode == 0, result.stderr
    assert not app.exists()
    assert copy.exists()
    assert "app.failed" in result.stdout.split("还剩下")[-1]


# --- an install that is a git checkout, and an update's own markers ----------

@mac_only
def test_a_running_git_checkout_is_not_taken_over_as_the_install(tmp_path):
    """A developer's checkout answering on the port is theirs to update with
    git; installing the release over it, or running its setup, is not what the
    command was asked to do."""
    import http.server
    import json
    import threading

    checkout = _fake_code(tmp_path / "dev" / "app")
    (checkout / ".git").mkdir()
    answer = json.dumps({"status": "ok", "edition": "local", "install": {"code_dir": str(checkout)}},
                        separators=(",", ":")).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(answer)))
            self.end_headers()
            self.wfile.write(answer)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        result = _install(tmp_path, tmp_path / "unused", _fake_code(tmp_path / "release", version="0.1.0"),
                          **{"FLUENTFLOW_INSTALL_DIR": "", "FLUENTFLOW_LOCAL_PORT": str(server.server_address[1])})
    finally:
        server.shutdown()

    assert "git 检出" in result.stdout, result.stdout + result.stderr
    assert not (checkout / "VERSION").exists() or (checkout / "VERSION").read_text(encoding="utf-8").strip() == "0.1.0"
    # Installed into the default place instead.
    assert (tmp_path / "home" / "fluentflow-local" / "backend" / "local_main.py").is_file()


@mac_only
def test_an_update_writes_the_old_version_marker_before_moving_it(tmp_path):
    """Power lost between the two moves leaves the previous version beside a
    missing code folder. Without a marker naming where it was put, the next run
    takes that for a fresh install and writes a default .env (the access token
    and the data folder settings are lost)."""
    app = _fake_code(tmp_path / "app", version="0.1.0")
    (app / ".env").write_text(f"FLUENTFLOW_ACCESS_TOKEN=abc\nFLUENTFLOW_DATA_DIR='{tmp_path}/data'\n", encoding="utf-8")
    fake = tmp_path / "fake-bin"
    fake.mkdir()
    seen = tmp_path / "updating-marker-seen"
    (fake / "mv").write_text(
        "#!/bin/bash\n"
        f'if [[ "$1" == "{app}" && "$2" == "{app}.old" ]]; then\n'
        f'  [[ -e "{app}.fluentflow-updating" ]] && : >"{seen}"\n'
        '  /bin/mv "$@"\n'
        '  kill -9 $PPID\n'
        "  exit 0\n"
        "fi\n"
        'exec /bin/mv "$@"\n', encoding="utf-8")
    (fake / "mv").chmod(0o755)
    env = _sandbox_env(tmp_path)
    result = subprocess.run(["bash", str(ROOT / "install.sh"), "--skip-model"], capture_output=True, text=True,
                            cwd=tmp_path, timeout=120,
                            env={**env, "PATH": f"{fake}:{env['PATH']}", "FLUENTFLOW_INSTALL_DIR": str(app),
                                 "FLUENTFLOW_INSTALL_SOURCE": str(_fake_code(tmp_path / "release", version="9.0.0"))})

    assert result.returncode != 0
    assert (tmp_path / "app.old" / ".fluentflow-update").is_file(), result.stdout + result.stderr
    assert (tmp_path / "app.old" / ".fluentflow-update").read_text(encoding="utf-8").split("\n")[-2].strip() == str(tmp_path / "app.old")
    assert seen.exists(), "the in-progress marker was not there while the update was running"


@mac_only
def test_an_update_stopped_between_the_moves_keeps_the_old_settings(tmp_path):
    """After that interruption, the next run puts the previous version back
    instead of installing fresh with a default .env."""
    app = _fake_code(tmp_path / "app", version="0.1.0")
    (app / ".env").write_text("FLUENTFLOW_ACCESS_TOKEN=abc\nThe line an update keeps\n", encoding="utf-8")
    fake = tmp_path / "fake-bin"
    fake.mkdir()
    (fake / "mv").write_text(
        "#!/bin/bash\n"
        f'if [[ "$1" == "{app}" && "$2" == "{app}.old" ]]; then\n'
        '  /bin/mv "$@"\n'
        '  kill -9 $PPID\n'
        "  exit 0\n"
        "fi\n"
        'exec /bin/mv "$@"\n', encoding="utf-8")
    (fake / "mv").chmod(0o755)
    env = _sandbox_env(tmp_path)
    first = subprocess.run(["bash", str(ROOT / "install.sh"), "--skip-model"], capture_output=True, text=True,
                           cwd=tmp_path, timeout=120,
                           env={**env, "PATH": f"{fake}:{env['PATH']}", "FLUENTFLOW_INSTALL_DIR": str(app),
                                "FLUENTFLOW_INSTALL_SOURCE": str(_fake_code(tmp_path / "release", version="9.0.0"))})
    assert first.returncode != 0 and not app.exists()

    # The next run behaves as if nothing had happened to it: the previous version
    # is put back, .env and everything else with it.
    again = _install(tmp_path, app, tmp_path / "release")

    assert "上一次更新" in again.stdout + again.stderr, again.stdout + again.stderr
    assert (app / ".env").read_text(encoding="utf-8") == "FLUENTFLOW_ACCESS_TOKEN=abc\nThe line an update keeps\n"


@mac_only
def test_the_updating_marker_is_gone_after_an_update_that_failed(tmp_path):
    """The marker tells another agent's MCP server not to start the service from
    a half-built install; it must not outlive the run."""
    app = _fake_code(tmp_path / "app", version="0.1.0")
    result = _install(tmp_path, app, _fake_code(tmp_path / "release", version="9.0.0"))

    assert result.returncode != 0
    assert "已换回 0.1.0" in result.stderr, result.stderr
    assert not (tmp_path / "app.fluentflow-updating").exists()


@mac_only
def test_a_stale_updating_marker_is_cleared_by_the_next_run(tmp_path):
    app = _fake_code(tmp_path / "app", version="0.1.0")
    stale = tmp_path / "app.fluentflow-updating"
    stale.write_text("left by a killed run\n", encoding="utf-8")
    os.utime(stale, (0, 0))  # more than an hour ago

    result = _install(tmp_path, app, _fake_code(tmp_path / "release", version="9.0.0"))

    assert "上一次更新被中断" in result.stdout + result.stderr, result.stdout + result.stderr


@mac_only
def test_a_fresh_updating_marker_stops_a_second_update(tmp_path):
    app = _fake_code(tmp_path / "app", version="0.1.0")
    (tmp_path / "app.fluentflow-updating").write_text("being updated now\n", encoding="utf-8")

    result = _install(tmp_path, app, _fake_code(tmp_path / "release", version="9.0.0"))

    assert result.returncode != 0
    assert "另一个更新正在运行" in result.stderr, result.stderr
    assert (app / "VERSION").read_text(encoding="utf-8").strip() == "0.1.0"


@pytest.mark.skipif(not POSIX, reason="install.sh is macOS only, and its function is run by bash")
def test_the_mcp_entry_path_is_read_whole_when_a_folder_holds_spaces():
    """`claude mcp get` prints `<python> <script>`; a folder name with a space is
    cut by taking the last word, Test-Path then fails, and the working
    registration of another install is removed (2026-11-27)."""
    helper = _bash_function(_read(ROOT / "install.sh"), "claude_target_of")
    assert helper
    # The shape `claude mcp get fluentflow-local` really prints: the interpreter
    # on its own Command: line, Args: holding only the script path.
    cases = [
        ("  Command: python3\n  Args: /Users/a/app/scripts/fluentflow_mcp_server.py",
         "/Users/a/app/scripts/fluentflow_mcp_server.py"),
        ("  Command: python3\n  Args: /Users/John Smith/app/scripts/fluentflow_mcp_server.py",
         "/Users/John Smith/app/scripts/fluentflow_mcp_server.py"),
        ('  Command: python3\n  Args: "/Users/John Smith/app/scripts/fluentflow_mcp_server.py"',
         "/Users/John Smith/app/scripts/fluentflow_mcp_server.py"),
        # A hand-edited entry with the interpreter in Args is understood too.
        ("  Command: sh\n  Args: /Users/John Smith/.local/bin/python3 /Users/John Smith/app/scripts/fluentflow_mcp_server.py",
         "/Users/John Smith/app/scripts/fluentflow_mcp_server.py"),
        # Not the server script: nothing can be told, so nothing may be removed.
        ("  Command: python3\n  Args: /opt/stuff/other-server.py", ""),
    ]
    script = helper + '\nfor a in "$@"; do claude_target_of "$a"; printf "\\n"; done\n'
    payloads = ["fluentflow-local:\n  Scope: User config\n" + body + "\n\nTo remove this server, run: claude mcp remove fluentflow-local -s user\n"
                for body, _ in cases]
    out = subprocess.run(["bash", "-c", script, "-", *payloads], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split("\n")[:len(cases)] == [want for _, want in cases]


def _fake_claude(tmp_path, repo, scope, remove_project_exit=0, remove_user_exit=0):
    fake = tmp_path / "fake-claude-bin"
    fake.mkdir(exist_ok=True)
    calls = tmp_path / "claude-calls.log"
    (fake / "claude").write_text(
        "#!/bin/bash\n"
        f"echo \"$*\" >> '{calls}'\n"
        'if [[ "$1 $2" == "mcp get" ]]; then\n'
        "  echo 'fluentflow-local:'\n"
        "  echo '  Scope: Project config'\n"
        f"  echo '  Args: python3 {repo}/scripts/fluentflow_mcp_server.py'\n"
        f"  echo 'To remove this server, run: claude mcp remove fluentflow-local -s {scope}'\n"
        "  exit 0\n"
        "fi\n"
        f'if [[ "$*" == *"--scope project"* ]]; then exit {remove_project_exit}; fi\n'
        f'if [[ "$*" == *"--scope user"* ]]; then exit {remove_user_exit}; fi\n'
        "exit 0\n", encoding="utf-8")
    (fake / "claude").chmod(0o755)
    return fake, calls


@mac_only
def test_unregistering_does_not_fall_back_to_another_scope(tmp_path):
    """Removing at the found scope failed; retrying at the user scope could
    remove another install's registration (both installs register at user)."""
    app = _fake_code(tmp_path / "app")
    fake, calls = _fake_claude(tmp_path, app, "project", remove_project_exit=1)

    result = _uninstall(tmp_path, app, "--yes", PATH=f"{fake}:/usr/bin:/bin:/usr/sbin:/sbin")

    assert result.returncode == 0, result.stderr
    log = calls.read_text(encoding="utf-8")
    assert "mcp remove --scope project" in log
    assert "--scope user" not in log
    assert "claude mcp remove --scope project fluentflow-local" in result.stdout


@mac_only
def test_unregistering_uses_the_user_scope_when_that_is_where_it_was(tmp_path):
    app = _fake_code(tmp_path / "app")
    fake, calls = _fake_claude(tmp_path, app, "user")

    result = _uninstall(tmp_path, app, "--yes", PATH=f"{fake}:/usr/bin:/bin:/usr/sbin:/sbin")

    assert result.returncode == 0, result.stderr
    assert "mcp remove --scope user" in calls.read_text(encoding="utf-8")


@pytest.mark.skipif(not _POWERSHELL, reason="the Windows helpers, run by Windows PowerShell")
def test_a_folder_on_a_drive_that_is_not_there_is_not_a_move(tmp_path):
    """A folder can be missing because the disk or share holding it is
    unplugged. Repointing .env then would abandon the data on it the moment it
    comes back (2026-11-27)."""
    letters = [c for c in "QRSTUVWXYZ" if not (Path(f"{c}:/").exists())]
    if not letters:
        pytest.skip("every drive letter is in use here")
    letter = letters[0]
    root = tmp_path / "FluentFlow"
    app = root / "app"
    app.mkdir(parents=True)
    (root / ".fluentflow-home").write_text("FluentFlow Local lives here", encoding="ascii")
    env_file = app / ".env"
    env_file.write_text(f"FLUENTFLOW_HOME='{letter}:\\FluentFlow'\r\n", encoding="utf-8")
    before = env_file.read_text(encoding="utf-8")
    script = _win_helper_script(*WIN_HELPERS_FOR_REPAIR) + (
        f"\n[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
        f"'changed=' + (@(Repair-FluentFlowHome '{app}') -join ',')\n")
    result = _powershell(script)

    assert result.returncode == 0, result.stderr
    assert "changed=" == result.stdout.strip().splitlines()[-1].strip(), result.stdout
    assert env_file.read_text(encoding="utf-8") == before
    assert "is not available right now" in result.stdout


@pytest.mark.skipif(not _POWERSHELL, reason="the Windows helpers, run by Windows PowerShell")
def test_the_env_file_is_never_seen_half_written(tmp_path):
    """Set-FluentFlowEnvLine used to truncate the file and write it again: a
    launcher or a service starting at that moment read an empty .env and ran on
    the default data folder (2026-11-27)."""
    repo = tmp_path / "app"
    repo.mkdir()
    env_file = repo / ".env"
    filler = "".join(f"FILLER_{index}=0123456789abcdef0123456789abcdef\r\n" for index in range(20000))
    old = filler + "FLUENTFLOW_ACCESS_TOKEN=abc\r\n"
    # Bytes: writing text on Windows would turn each \n into \r\n and the
    # comparison below would never match what PowerShell writes.
    env_file.write_bytes(old.encode("utf-8"))
    new = filler + "FLUENTFLOW_ACCESS_TOKEN=abc\r\nMY_SETTING='D:\\FluentFlow\\data'\r\n"
    writer = tmp_path / "writer.ps1"
    writer.write_text(_win_helper_script("Add-FluentFlowEnvLine", "Set-FluentFlowEnvLine") + (
        f"\nfor ($i = 0; $i -lt 25; $i++) {{ Set-FluentFlowEnvLine '{repo}' 'MY_SETTING' 'D:\\FluentFlow\\data' }}\n"),
        encoding="utf-8")
    writer_log = tmp_path / "writer.log"
    with open(writer_log, "wb") as log:
        process = subprocess.Popen([_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(writer)],
                                   stdout=log, stderr=subprocess.STDOUT)
    partial = 0
    lengths = []
    first = None
    try:
        while process.poll() is None:
            try:
                with open(env_file, "rb") as handle:
                    seen = handle.read()
            except OSError:
                continue
            text = seen.decode("utf-8", "replace")
            lengths.append(len(seen))
            if text not in (old, new):
                partial += 1
                if first is None:
                    first = text[:80] + " ... " + text[-80:]
    finally:
        process.wait(timeout=120)

    assert partial == 0, (
        f"a reader saw a half-written .env; lengths {min(lengths or [0])}-{max(lengths or [0])}, "
        f"first: {first!r}, writer said: {writer_log.read_text(encoding='utf-8', errors='replace')[:600]!r}")
    assert not (repo / ".env.writing").exists()
    assert env_file.read_bytes().decode("utf-8") in (old, new)


@pytest.mark.skipif(not _POWERSHELL, reason="the Windows helpers, run by Windows PowerShell")
def test_the_old_version_marker_is_written_before_the_move(tmp_path):
    """Losing power between the two moves must leave a marker that names where
    the copy went, or the next run installs fresh over the settings."""
    root = tmp_path / "FluentFlow"
    app = root / "app"
    app.mkdir(parents=True)
    (root / ".fluentflow-home").write_text("FluentFlow Local lives here", encoding="ascii")
    script = _win_helper_script("Get-FluentFlowRealPath", "Get-FluentFlowLinkTarget", "Set-FluentFlowUpdateCopyAs", "Test-FluentFlowUpdateCopy") + (
        f"\nSet-FluentFlowUpdateCopyAs '{app}' '{app}.old'\n"
        f"'before=' + (Test-FluentFlowUpdateCopy '{app}')\n"
        f"Move-Item -LiteralPath '{app}' -Destination '{app}.old'\n"
        f"'after=' + (Test-FluentFlowUpdateCopy '{app}.old')\n")
    result = _powershell(script)

    assert result.returncode == 0, result.stderr
    assert "before=False" in result.stdout and "after=True" in result.stdout, result.stdout


@pytest.mark.skipif(not _POWERSHELL, reason="the Windows helpers, run by Windows PowerShell")
def test_the_updating_marker_is_created_and_removed(tmp_path):
    root = tmp_path / "FluentFlow"
    (root / "app").mkdir(parents=True)
    (root / ".fluentflow-home").write_text("FluentFlow Local lives here", encoding="ascii")
    script = _win_helper_script("Get-FluentFlowUpdatingMarker", "Start-FluentFlowUpdating", "Stop-FluentFlowUpdating") + f"""
$marker = Start-FluentFlowUpdating '{root}' 'to 9.9.9 from 0.1.0'
'exists=' + (Test-Path -LiteralPath $marker)
'path=' + $marker
'text=' + ((Get-Content -LiteralPath $marker) -join '|')
$second = ''
try {{ Start-FluentFlowUpdating '{root}' 'again' | Out-Null }} catch {{ $second = $_.Exception.Message }}
'second=' + [bool]$second
Stop-FluentFlowUpdating $marker
'after=' + (Test-Path -LiteralPath $marker)
"""
    result = _powershell(script)

    assert result.returncode == 0, result.stderr
    lines = dict(line.split("=", 1) for line in result.stdout.strip().splitlines() if "=" in line)
    assert lines["exists"] == "True"
    assert lines["path"].lower() == str(tmp_path / "FluentFlow.fluentflow-updating").lower()
    assert "FluentFlow Local is being updated" in lines["text"]
    assert lines["second"] == "True"
    assert lines["after"] == "False"


@pytest.mark.skipif(not POSIX, reason="install.sh is macOS only, and its function is run by bash")
def test_a_codex_entry_that_cannot_be_read_is_not_taken_for_a_dead_one():
    """`args` written in a form the installer cannot parse used to count as
    stale, and the entry (another install's, at that) was removed (2026-11-27)."""
    helper = _bash_function(_read(ROOT / "install.sh"), "codex_entry_is_stale")
    assert helper
    missing = "/Users/a/gone/scripts/fluentflow_mcp_server.py"
    present = "/Users/a/app/scripts/fluentflow_mcp_server.py"
    cases = [
        (f'[mcp_servers.fluentflow-local]\ncommand = "python3"\nargs = ["{missing}"]', True),
        (f'[mcp_servers.fluentflow-local]\ncommand = "python3"\nargs = ["{present}"]', False),
        ('[mcp_servers.fluentflow-local]\ncommand = "x"\nargs = [\n  "a",\n]', False),
        ('[mcp_servers.fluentflow-local]\ncommand = "x"', False),
    ]
    script = f'MCP_SCRIPT="{present}"\n' + helper + '\ncodex_entry_is_stale "$1" && echo stale || echo kept\n'
    for section, expected in cases:
        out = subprocess.run(["bash", "-c", script, "-", section], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        assert (out.stdout.strip() == "stale") == expected, (section, out.stdout)


# --- the update marker is claimed, not checked -------------------------------

def _claim_harness() -> str:
    """ff_claim_updating_marker with what it calls, and a fail_plain that just
    ends that process."""
    text = _read(ROOT / "install.sh")
    parts = []
    for name in ("ff_updating_marker", "ff_claim_updating_marker"):
        function = _bash_function(text, name)
        assert function, f"{name} missing from install.sh"
        parts.append(function)
    return "fail_plain() { exit 1; }\nLATEST=9.9.9\n" + "\n".join(parts)


@mac_only
def test_only_one_of_several_updates_gets_the_marker(tmp_path):
    """Checking the file and then writing it lets two updates in at once; the
    claim has to be one step (2026-11-27)."""
    script = _claim_harness() + f'\nff_claim_updating_marker "{tmp_path}/app" 9.9.9\n'
    script_file = tmp_path / "claim.sh"
    script_file.write_text(script, encoding="utf-8")
    winners = tmp_path / "winners"
    winners.mkdir()
    processes = [subprocess.Popen(["bash", str(script_file)], cwd=tmp_path,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(16)]
    codes = [process.wait(timeout=60) for process in processes]

    assert codes.count(0) == 1, codes
    assert (tmp_path / "app.fluentflow-updating").is_file()


@mac_only
def test_a_second_update_stops_before_touching_the_first_ones_staged_copy(tmp_path):
    """The marker is claimed before ${DIR}.new is prepared: a run that starts
    while another is staging its new code stops without deleting that code."""
    app = _fake_code(tmp_path / "app", version="0.1.0")
    release = _fake_code(tmp_path / "release", version="9.0.0")
    fake = tmp_path / "fake-bin"
    fake.mkdir()
    # Stands in for the update being killed right after it staged its new code:
    # the copy is complete, and the marker it claimed is still there.
    (fake / "cp").write_text(
        "#!/bin/bash\n"
        '/bin/cp "$@"\n'
        'kill -9 $PPID\n'
        "exit 0\n", encoding="utf-8")
    (fake / "cp").chmod(0o755)
    env = _sandbox_env(tmp_path)
    first = subprocess.run(["bash", str(ROOT / "install.sh"), "--skip-model"], capture_output=True, text=True,
                           cwd=tmp_path, timeout=120,
                           env={**env, "PATH": f"{fake}:{env['PATH']}", "FLUENTFLOW_INSTALL_DIR": str(app),
                                "FLUENTFLOW_INSTALL_SOURCE": str(release)})
    assert first.returncode != 0
    assert (tmp_path / "app.new" / "VERSION").read_text(encoding="utf-8").strip() == "9.0.0"
    assert (tmp_path / "app.fluentflow-updating").is_file()

    second = _install(tmp_path, app, release)

    assert second.returncode != 0
    assert "另一个更新正在运行" in second.stderr, second.stderr
    # The first run's staged copy is untouched.
    assert (tmp_path / "app.new" / "VERSION").read_text(encoding="utf-8").strip() == "9.0.0"
    assert (app / "VERSION").read_text(encoding="utf-8").strip() == "0.1.0"


@mac_only
def test_a_downloaded_update_leaves_no_temporary_folder(tmp_path):
    """The download branch installed a trap for its temp folder, and the marker
    installed another: the second replaced the first, so the folder stayed."""
    app = _fake_code(tmp_path / "app", version="0.1.0")
    release = tmp_path / "release-tree" / "fluentflow-local-9.9.9"
    _fake_code(release, version="9.9.9")
    archive = tmp_path / "release.zip"
    subprocess.run(["zip", "-qr", str(archive), release.name], cwd=release.parent, check=True)
    work = tmp_path / "tmpdir"
    work.mkdir()
    fake = tmp_path / "fake-bin"
    fake.mkdir()
    (fake / "curl").write_text(
        "#!/bin/bash\n"
        f'release_zip="{archive}"\n'
        'url=""; out=""\n'
        'while [[ $# -gt 0 ]]; do\n'
        '  case "$1" in\n'
        '    -o) out="$2"; shift 2 ;;\n'
        '    -w) shift 2 ;;\n'
        '    -*) shift ;;\n'
        '    *) url="$1"; shift ;;\n'
        '  esac\n'
        'done\n'
        'case "$url" in\n'
        '  */releases/latest) printf "HTTP/2 302\\r\\nLocation: https://github.com/x/y/releases/tag/v9.9.9\\r\\n" ;;\n'
        '  *archive/refs/tags/*.zip) cp "$release_zip" "$out" ;;\n'
        '  *frontend-dist-local.zip) exit 22 ;;\n'
        '  *registry.npmmirror.com*) exit 6 ;;\n'
        '  *) exit 7 ;;\n'
        'esac\n', encoding="utf-8")
    (fake / "curl").chmod(0o755)
    env = {**_sandbox_env(tmp_path), "PATH": f"{fake}:{_sandbox_env(tmp_path)['PATH']}", "TMPDIR": str(work),
           "FLUENTFLOW_INSTALL_DIR": str(app)}
    # Where this shell's mktemp puts things (macOS mktemp ignores TMPDIR): a
    # folder it left behind still holds the download.
    probe = subprocess.run(["bash", "-c", "d=$(mktemp -d); printf %s \"$d\"; rmdir \"$d\""],
                           capture_output=True, text=True, env=env)
    base = Path(probe.stdout.strip()).parent
    before = set(base.glob("tmp.*"))
    result = subprocess.run(["bash", str(ROOT / "install.sh"), "--skip-model"], capture_output=True, text=True,
                            cwd=tmp_path, timeout=180, env=env)
    leaked = [entry.name for entry in set(base.glob("tmp.*")) - before if (entry / "src.zip").exists()]
    left = [entry.name for entry in work.iterdir()]

    assert not leaked, (leaked, result.stdout + result.stderr)
    assert left == [], (left, result.stdout + result.stderr)


@pytest.mark.skipif(not _POWERSHELL, reason="the Windows helpers, run by Windows PowerShell")
def test_the_windows_parser_reads_the_real_entry_shape(tmp_path):
    """`claude mcp get` on Windows prints Command: with the interpreter and Args:
    with the script alone; a folder with a space must not be cut (2026-11-27)."""
    script = _win_helper_script("Get-FluentFlowEntryScript") + '''
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$cases = @(
  @("Command: python.exe`nArgs: C:\\Users\\John Smith\\app\\scripts\\fluentflow_mcp_server.py", "C:\\Users\\John Smith\\app\\scripts\\fluentflow_mcp_server.py"),
  @("Command: python.exe`nArgs: D:\\FluentFlow\\app\\scripts\\fluentflow_mcp_server.py", "D:\\FluentFlow\\app\\scripts\\fluentflow_mcp_server.py"),
  @("Command: python.exe`nArgs: ""D:\\FluentFlow\\app\\scripts\\fluentflow_mcp_server.py""", "D:\\FluentFlow\\app\\scripts\\fluentflow_mcp_server.py"),
  @("Command: sh`nArgs: C:\\Python312\\python.exe D:\\FluentFlow\\app\\scripts\\fluentflow_mcp_server.py", "D:\\FluentFlow\\app\\scripts\\fluentflow_mcp_server.py"),
  @("Command: python.exe`nArgs: D:\\other\\other-server.py", "")
)
foreach ($c in $cases) { "got=" + (Get-FluentFlowEntryScript $c[0]); "want=" + $c[1] }
'''
    result = _powershell(script)

    assert result.returncode == 0, result.stderr
    lines = [line.split("=", 1) for line in result.stdout.strip().splitlines() if "=" in line]
    pairs = [(value, lines[i + 1][1]) for i, (key, value) in enumerate(lines) if key == "got"]
    for got, want in pairs:
        assert got.lower() == want.lower(), (got, want, result.stdout)


@pytest.mark.skipif(not _POWERSHELL, reason="the Windows helpers, run by Windows PowerShell")
def test_only_one_of_several_updates_gets_the_marker_on_windows(tmp_path):
    root = tmp_path / "FluentFlow"
    (root / "app").mkdir(parents=True)
    (root / ".fluentflow-home").write_text("FluentFlow Local lives here", encoding="ascii")
    claimer = tmp_path / "claim.ps1"
    claimer.write_text(_win_helper_script("Get-FluentFlowUpdatingMarker", "Start-FluentFlowUpdating") + f'''
try {{
    Start-FluentFlowUpdating '{root}' 'to 9.9.9' | Out-Null
    exit 0
}} catch {{
    exit 3
}}
''', encoding="utf-8")
    processes = [subprocess.Popen([_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(claimer)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(8)]
    codes = [process.wait(timeout=120) for process in processes]

    assert codes.count(0) == 1, codes
    assert (tmp_path / "FluentFlow.fluentflow-updating").is_file()
