"""Requirements for registering the MCP server with the user's agents
(2026-11-27, found while testing the guided first use in a sandbox).

- Every agent the installer registers (Claude Code, Codex, OpenCode) is told the
  configured port when it is not 8000; an entry of this install that names
  another port is registered again, since the MCP server dials 8000 otherwise.
- OpenCode is registered when it is on the machine, by editing only the
  fluentflow-local entry of its JSON config: other servers and settings stay, a
  file that is not plain JSON is never rewritten, and the uninstallers remove
  only an entry that points at the install being removed.
- A Desktop icon that cannot be made is a warning: the install goes on to
  register the tools and start the service.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import opencode_mcp_config as oc  # noqa: E402
from tests.test_install_places import _bash_function, _fake_code, _read, _uninstall, mac_only  # noqa: E402

PY = "/app/.venv/bin/python"
SCRIPT = "/app/scripts/fluentflow_mcp_server.py"
POSIX = sys.platform != "win32"


def _other_servers() -> dict:
    return {"$schema": "https://opencode.ai/config.json", "providers": {"x": {"npm": "y"}},
            "mcp": {"timeout": {"startup": 5000},
                    "servers": {"github": {"type": "remote", "url": "https://example.com/mcp"},
                                "notes": {"type": "local", "command": ["notes-mcp"], "environment": {"A": "1"}}}}}


# --- merging into OpenCode's config ------------------------------------------

def test_registering_keeps_every_other_server_and_setting():
    config = _other_servers()
    before = json.loads(json.dumps(config))

    new, what = oc.merge_entry(config, PY, SCRIPT, "http://127.0.0.1:8019", "servers")

    assert what == "added"
    assert config == before, "the input is not modified in place"
    assert new["mcp"]["servers"]["fluentflow-local"] == {
        "type": "local", "command": [PY, SCRIPT], "environment": {"FLUENTFLOW_API_BASE": "http://127.0.0.1:8019"}}
    for key in ("github", "notes"):
        assert new["mcp"]["servers"][key] == before["mcp"]["servers"][key]
    assert new["mcp"]["timeout"] == before["mcp"]["timeout"]
    assert new["providers"] == before["providers"] and new["$schema"] == before["$schema"]


def test_the_default_port_carries_no_address_and_a_rerun_changes_nothing():
    new, what = oc.merge_entry({}, PY, SCRIPT, None, "servers")
    assert what == "added"
    assert "environment" not in new["mcp"]["servers"]["fluentflow-local"]

    again, what = oc.merge_entry(new, PY, SCRIPT, None, "servers")
    assert what == "unchanged" and again is new


def test_a_changed_port_updates_this_installs_entry_and_keeps_its_other_settings():
    config = {"mcp": {"servers": {"fluentflow-local": {
        "type": "local", "command": [PY, SCRIPT], "disabled": False,
        "environment": {"FLUENTFLOW_API_BASE": "http://127.0.0.1:8000", "OTHER": "kept"}}}}}

    new, what = oc.merge_entry(config, PY, SCRIPT, "http://127.0.0.1:8123", "servers")

    assert what == "updated"
    entry = new["mcp"]["servers"]["fluentflow-local"]
    assert entry["environment"] == {"FLUENTFLOW_API_BASE": "http://127.0.0.1:8123", "OTHER": "kept"}
    assert entry["disabled"] is False

    back, what = oc.merge_entry(new, PY, SCRIPT, None, "servers")
    assert what == "updated"
    assert back["mcp"]["servers"]["fluentflow-local"]["environment"] == {"OTHER": "kept"}


def test_another_install_that_still_exists_is_left_alone():
    other = {"type": "local", "command": ["/other/py", "/other/scripts/fluentflow_mcp_server.py"]}
    config = {"mcp": {"servers": {"fluentflow-local": other}}}

    new, what = oc.merge_entry(config, PY, SCRIPT, None, "servers", exists=lambda path: True)

    assert what == "other_install" and new is config


def test_an_entry_whose_install_is_gone_is_replaced():
    config = {"mcp": {"servers": {"fluentflow-local": {
        "type": "local", "command": ["/gone/py", "/gone/scripts/fluentflow_mcp_server.py"]}}}}

    new, what = oc.merge_entry(config, PY, SCRIPT, None, "servers", exists=lambda path: False)

    assert what == "updated"
    assert new["mcp"]["servers"]["fluentflow-local"]["command"] == [PY, SCRIPT]


def test_an_entry_that_does_not_say_what_it_starts_is_left_alone():
    config = {"mcp": {"servers": {"fluentflow-local": {"type": "local", "command": ["something-else"]}}}}

    new, what = oc.merge_entry(config, PY, SCRIPT, None, "servers")

    assert what == "unreadable_entry" and new is config


def test_the_old_layout_is_written_for_opencode_1_and_moved_for_2():
    legacy, _ = oc.merge_entry({"mcp": {"keep": {"type": "local", "command": ["k"]}}}, PY, SCRIPT, None, "legacy")
    assert legacy["mcp"]["fluentflow-local"]["command"] == [PY, SCRIPT]
    assert "servers" not in legacy["mcp"]

    moved, what = oc.merge_entry(legacy, PY, SCRIPT, None, "servers")
    assert what == "updated"
    assert "fluentflow-local" not in moved["mcp"]
    assert moved["mcp"]["servers"]["fluentflow-local"]["command"] == [PY, SCRIPT]
    assert moved["mcp"]["keep"] == {"type": "local", "command": ["k"]}


@pytest.mark.parametrize(("version", "config", "layout"), [
    ("opencode v2.4.1\n", {}, "servers"),
    ("1.0.150", {}, "legacy"),
    (None, {}, "servers"),
    ("1.0.150", {"mcp": {"servers": {}}}, "servers"),
])
def test_the_layout_follows_the_installed_version(version, config, layout):
    assert oc.layout_for(version, config) == layout


def test_removing_takes_only_this_installs_entry():
    config = _other_servers()
    config["mcp"]["servers"]["fluentflow-local"] = {"type": "local", "command": [PY, SCRIPT]}

    new, removed = oc.remove_entry(config, "/app")

    assert removed
    assert set(new["mcp"]["servers"]) == {"github", "notes"}

    elsewhere = {"mcp": {"servers": {"fluentflow-local": {"type": "local", "command": [PY, SCRIPT]}}}}
    same, removed = oc.remove_entry(elsewhere, "/app-backup")
    assert not removed and same is elsewhere


# --- the script against a real file ------------------------------------------

def _fake_opencode(tmp_path: Path, version: str = "opencode v2.4.1") -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    if POSIX:
        exe = bin_dir / "opencode"
        exe.write_text(f"#!/bin/sh\necho '{version}'\n", encoding="utf-8")
        exe.chmod(0o755)
    else:
        (bin_dir / "opencode.cmd").write_text(f"@echo {version}\r\n", encoding="utf-8")
    return bin_dir


def _run(tmp_path: Path, *args: str, path: str | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path / "config"), "HOME": str(tmp_path / "home"),
           "USERPROFILE": str(tmp_path / "home"), "PATH": path if path is not None else os.environ.get("PATH", "")}
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "opencode_mcp_config.py"), *args],
                          capture_output=True, text=True, env=env, timeout=60)


def test_the_script_writes_the_entry_beside_what_is_there(tmp_path):
    folder = tmp_path / "config" / "opencode"
    folder.mkdir(parents=True)
    (folder / "opencode.json").write_text(json.dumps(_other_servers()), encoding="utf-8")
    path = str(_fake_opencode(tmp_path))

    result = _run(tmp_path, "register", "--python", PY, "--script", SCRIPT, "--port", "8019", path=path)

    assert result.returncode == 0, result.stdout + result.stderr
    written = json.loads((folder / "opencode.json").read_text(encoding="utf-8"))
    assert written["mcp"]["servers"]["fluentflow-local"]["environment"] == {"FLUENTFLOW_API_BASE": "http://127.0.0.1:8019"}
    assert set(written["mcp"]["servers"]) == {"github", "notes", "fluentflow-local"}
    assert not [p.name for p in folder.iterdir() if p.name.startswith(".opencode.")], "no temporary file left"


def test_a_file_that_is_not_plain_json_is_not_rewritten(tmp_path):
    folder = tmp_path / "config" / "opencode"
    folder.mkdir(parents=True)
    text = '{\n  // my servers\n  "mcp": {"servers": {}}\n}\n'
    (folder / "opencode.json").write_text(text, encoding="utf-8")

    result = _run(tmp_path, "register", "--python", PY, "--script", SCRIPT, path=str(_fake_opencode(tmp_path)))

    assert result.returncode == 2
    assert (folder / "opencode.json").read_text(encoding="utf-8") == text
    assert SCRIPT in result.stdout, "the lines to add are printed instead"


def test_nothing_is_written_when_opencode_is_not_installed(tmp_path):
    empty = tmp_path / "empty-bin"
    empty.mkdir()

    result = _run(tmp_path, "register", "--python", PY, "--script", SCRIPT, path=str(empty))

    assert result.returncode == 3
    assert not (tmp_path / "config").exists()


# --- the port in the installers ----------------------------------------------

def test_every_registration_in_install_sh_carries_the_port():
    text = _read(ROOT / "install.sh")
    claude = [line for line in text.splitlines() if re.search(r"\bclaude mcp add\b", line) and "echo" not in line]
    codex = [line for line in text.splitlines() if re.search(r"\bcodex mcp add\b", line) and not line.lstrip().startswith("#")]
    assert claude and codex
    assert all("MCP_ENV[@]" in line for line in claude), claude
    assert all("CODEX_ENV[@]" in line for line in codex), codex
    assert re.search(r'opencode_mcp_config\.py" register .*--port "\$PORT"', text)


def test_every_registration_in_install_ps1_carries_the_port():
    text = _read(ROOT / "install.ps1")
    claude = [line for line in text.splitlines() if '@("mcp", "add", "--scope", "user"' in line]
    codex = [line for line in text.splitlines() if '@("mcp", "add", "fluentflow-local")' in line]
    assert claude and codex
    assert all("$ClaudeEnv" in line for line in claude), claude
    assert all("$CodexEnv" in line for line in codex), codex
    assert re.search(r"opencode_mcp_config\.py\"\) register .*--port \$Port", text)


def _bash_helpers(*names: str) -> str:
    text = _read(ROOT / "install.sh")
    found = [_bash_function(text, name) for name in names]
    assert all(found), names
    return "\n".join(found)


@pytest.mark.skipif(not POSIX, reason="install.sh is the macOS installer")
@pytest.mark.parametrize(("toml", "port", "differs"), [
    # What `codex mcp add --env` writes (checked with codex-cli 0.172.0).
    ('[mcp_servers.fluentflow-local]\ncommand = "/py"\nargs = ["/s"]\n\n[mcp_servers.fluentflow-local.env]\n'
     'FLUENTFLOW_API_BASE = "http://127.0.0.1:8019"\n', "8019", False),
    ('[mcp_servers.fluentflow-local]\ncommand = "/py"\nargs = ["/s"]\n\n[mcp_servers.fluentflow-local.env]\n'
     'FLUENTFLOW_API_BASE = "http://127.0.0.1:8019"\n', "8000", True),
    ('[mcp_servers.fluentflow-local]\ncommand = "/py"\nargs = ["/s"]\n', "8019", True),
    ('[mcp_servers.fluentflow-local]\ncommand = "/py"\nargs = ["/s"]\n', "8000", False),
    ("[mcp_servers.fluentflow-local]\ncommand = '/py'\nargs = ['/s']\nenv = { FLUENTFLOW_API_BASE = 'http://127.0.0.1:8019' }\n"
     "[mcp_servers.other.env]\nFLUENTFLOW_API_BASE = \"http://127.0.0.1:1\"\n", "8019", False),
])
def test_install_sh_reads_the_port_a_codex_entry_names(tmp_path, toml, port, differs):
    config = tmp_path / "config.toml"
    config.write_text(toml, encoding="utf-8")
    script = (_bash_helpers("api_base_differs", "codex_api_base_of") +
              f'\nEXPECTED_API_BASE=""\n[[ "{port}" != 8000 ]] && EXPECTED_API_BASE="http://127.0.0.1:{port}"\n'
              f'api_base_differs "$(codex_api_base_of "{config}")" && echo differs || echo same\n')
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    assert result.stdout.strip() == ("differs" if differs else "same"), result.stderr


@pytest.mark.skipif(not POSIX, reason="install.sh is the macOS installer")
def test_install_sh_reads_the_port_a_claude_entry_names():
    # What `claude mcp get` prints (Claude Code 2.1.332).
    described = ("fluentflow-local:\n  Scope: User config (available in all your projects)\n  Type: stdio\n"
                 "  Command: /py\n  Args: /s\n  Environment:\n    FLUENTFLOW_API_BASE=http://127.0.0.1:8019\n\n"
                 "To remove this server, run: claude mcp remove fluentflow-local -s user\n")
    script = _bash_helpers("claude_api_base_of") + '\nclaude_api_base_of "$1"\n'
    result = subprocess.run(["bash", "-c", script, "x", described], capture_output=True, text=True, timeout=30)
    assert result.stdout.strip() == "http://127.0.0.1:8019"


# --- the Desktop icon ---------------------------------------------------------

@pytest.mark.skipif(not POSIX, reason="the macOS setup script")
def test_a_desktop_icon_that_cannot_be_made_does_not_stop_the_install(tmp_path):
    text = _read(ROOT / "launchers" / "macos" / "setup-local.sh")
    tail = text[text.index('step "生成桌面启动器"'):]
    (tmp_path / "install-local-to-desktop.sh").write_text(
        "echo 'osacompile: could not write' >&2\nexit 1\n", encoding="utf-8")
    script = f'set -euo pipefail\nstep() {{ echo "== $1 =="; }}\nSCRIPT_DIR="{tmp_path}"\n' + tail + '\necho "never reached"\n'

    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, result.stderr
    assert "没能在桌面生成" in result.stdout
    assert "安装完成（没有桌面图标）" in result.stdout


def test_the_windows_desktop_icon_failure_is_a_warning():
    text = _read(ROOT / "launchers" / "windows" / "setup-local.ps1")
    call = 'Invoke-Checked (Join-Path $ScriptDir "install-local-to-desktop.ps1") @()'
    at = text.index(call)
    before = text[:at]
    assert before.rstrip().endswith("try {"), "the icon is made inside try"
    assert "Write-Warning" in text[at:text.index("}", text.index("catch", at)) + 1]


# --- uninstalling -------------------------------------------------------------

@mac_only
def test_the_mac_uninstaller_removes_only_this_installs_opencode_entry(tmp_path):
    app = _fake_code(tmp_path / "app")
    (app / "scripts").mkdir()
    (app / "scripts" / "opencode_mcp_config.py").write_text(_read(ROOT / "scripts" / "opencode_mcp_config.py"), encoding="utf-8")
    (app / ".venv" / "bin").mkdir(parents=True)
    (app / ".venv" / "bin" / "python").symlink_to(sys.executable)
    folder = tmp_path / "home" / ".config" / "opencode"
    folder.mkdir(parents=True)
    config = _other_servers()
    config["mcp"]["servers"]["fluentflow-local"] = {
        "type": "local", "command": [f"{app}/.venv/bin/python", f"{app}/scripts/fluentflow_mcp_server.py"]}
    (folder / "opencode.json").write_text(json.dumps(config), encoding="utf-8")

    dry = _uninstall(tmp_path, app, "--dry-run")
    assert "注销 OpenCode 里的 fluentflow-local" in dry.stdout, dry.stdout + dry.stderr
    assert "fluentflow-local" in (folder / "opencode.json").read_text(encoding="utf-8")

    result = _uninstall(tmp_path, app, "--yes")

    assert result.returncode == 0, result.stdout + result.stderr
    left = json.loads((folder / "opencode.json").read_text(encoding="utf-8"))
    assert set(left["mcp"]["servers"]) == {"github", "notes"}
    assert left["providers"] == config["providers"]


@mac_only
def test_the_mac_uninstaller_leaves_another_installs_opencode_entry(tmp_path):
    app = _fake_code(tmp_path / "app")
    (app / "scripts").mkdir()
    (app / "scripts" / "opencode_mcp_config.py").write_text(_read(ROOT / "scripts" / "opencode_mcp_config.py"), encoding="utf-8")
    (app / ".venv" / "bin").mkdir(parents=True)
    (app / ".venv" / "bin" / "python").symlink_to(sys.executable)
    folder = tmp_path / "home" / ".config" / "opencode"
    folder.mkdir(parents=True)
    config = {"mcp": {"servers": {"fluentflow-local": {
        "type": "local", "command": ["/elsewhere/py", "/elsewhere/scripts/fluentflow_mcp_server.py"]}}}}
    (folder / "opencode.json").write_text(json.dumps(config), encoding="utf-8")

    result = _uninstall(tmp_path, app, "--yes")

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((folder / "opencode.json").read_text(encoding="utf-8")) == config
    assert "指向别的位置" in result.stdout
