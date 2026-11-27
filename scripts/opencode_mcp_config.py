#!/usr/bin/env python3
"""Register or unregister FluentFlow's MCP server in OpenCode's global config.

Run by install.sh / install.ps1 and the two uninstallers; stdlib only, so the
uninstallers can run it with whatever Python is left.

OpenCode keeps its global config in ``$XDG_CONFIG_HOME/opencode/opencode.json``
(``~/.config/opencode`` when that is unset, on Windows too). Where a server goes
depends on the version (checked 2026-11-27 against https://opencode.ai/config.json
and ``opencode mcp add --global`` of 2.4.1):

- 2.x: ``{"mcp": {"servers": {"<name>": {"type": "local", "command": [...],
  "environment": {...}}}}}``
- 1.x: ``{"mcp": {"<name>": {...same...}}}``, which 2.x still reads but marks
  deprecated.

Only the ``fluentflow-local`` entry is ever written or removed; everything else in
the file is kept as it was. A file that is not plain JSON (comments, a typo) is
never rewritten: the lines to add are printed instead.

Exit codes: 0 done (or nothing to do), 2 left alone on purpose (another install's
entry, an entry or file that cannot be read), 3 OpenCode is not on this machine;
``unregister --check``: 0 there is an entry of this install to remove, 1 there is none.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

NAME = "fluentflow-local"
SCRIPT_NAME = "fluentflow_mcp_server.py"
API_BASE = "FLUENTFLOW_API_BASE"


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "opencode"


def config_file(folder: Path) -> Path:
    """opencode.json, or an existing opencode.jsonc when only that one is there."""
    plain = folder / "opencode.json"
    commented = folder / "opencode.jsonc"
    if not plain.exists() and commented.exists():
        return commented
    return plain


def installed() -> bool:
    return bool(shutil.which("opencode")) or config_dir().is_dir()


def _servers_container(mcp: Any) -> dict[str, Any] | None:
    """mcp.servers when it is the 2.x container; a 1.x server that happens to be
    named "servers" (it has a type or command) is not one."""
    if not isinstance(mcp, dict):
        return None
    servers = mcp.get("servers")
    if isinstance(servers, dict) and "type" not in servers and "command" not in servers:
        return servers
    return None


def layout_for(version_text: str | None, config: dict[str, Any]) -> str:
    """"servers" (2.x) or "legacy" (1.x). A file already using mcp.servers keeps it;
    a known 2.x gets it; an unknown version gets the old form, because 2.x still
    reads mcp.<name> while early 1.x refuses a file with mcp.servers."""
    if _servers_container(config.get("mcp")) is not None:
        return "servers"
    match = re.search(r"(\d+)\.\d+", version_text or "")
    if match and int(match.group(1)) >= 2:
        return "servers"
    return "legacy"


def unsupported_shape(config: dict[str, Any]) -> bool:
    """mcp, or mcp.servers in a file using it, holds something we would have to
    overwrite to add our entry: leave the file alone."""
    if "mcp" not in config:
        return False
    mcp = config["mcp"]
    if not isinstance(mcp, dict):
        return True
    if NAME in mcp and not isinstance(mcp[NAME], dict):
        return True
    servers = mcp.get("servers")
    if servers is not None and not isinstance(servers, dict):
        return True
    container = _servers_container(mcp)
    return container is not None and NAME in container and not isinstance(container[NAME], dict)


def opencode_version() -> str | None:
    exe = shutil.which("opencode")
    if not exe:
        return None
    try:
        return subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def _entries(config: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Where a fluentflow-local entry sits now: ("servers"|"legacy", entry)."""
    found = []
    mcp = config.get("mcp")
    if isinstance(mcp, dict):
        servers = _servers_container(mcp)
        if servers is not None and isinstance(servers.get(NAME), dict):
            found.append(("servers", servers[NAME]))
        if isinstance(mcp.get(NAME), dict):
            found.append(("legacy", mcp[NAME]))
    return found


def entry_script(entry: dict[str, Any]) -> str | None:
    command = entry.get("command")
    if not isinstance(command, list):
        return None
    scripts = [part for part in command if isinstance(part, str) and part.endswith(SCRIPT_NAME)]
    return scripts[-1] if scripts else None


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def merge_entry(config: dict[str, Any], python: str, script: str, api_base: str | None, layout: str,
                exists: Callable[[str], bool] = os.path.exists) -> tuple[dict[str, Any], str]:
    """The config with this install's entry in place, and what happened:
    "added", "updated", "unchanged", "other_install", "unreadable_entry" or
    "unsupported_shape" (mcp holds something we would have to overwrite).

    An entry that starts another install's script that is still there, or whose
    script cannot be read, is left alone (as install.sh does for Claude Code and
    Codex); one whose script is gone is replaced. ``api_base`` None means the
    default port, so no FLUENTFLOW_API_BASE."""
    if unsupported_shape(config):
        return config, "unsupported_shape"
    if layout == "servers" and isinstance(config.get("mcp"), dict) and "servers" in config["mcp"] \
            and _servers_container(config["mcp"]) is None:
        return config, "unsupported_shape"  # a 1.x server named "servers" holds that key
    new = copy.deepcopy(config)
    found = _entries(new)
    old: dict[str, Any] = {}
    replacing = False
    for _where, entry in found:
        target = entry_script(entry)
        if target is None:
            return config, "unreadable_entry"
        if _same_path(target, script):
            old = entry
        elif exists(target):
            return config, "other_install"
        else:
            replacing = True
    entry = copy.deepcopy(old)
    entry["type"] = "local"
    entry["command"] = [python, script]
    environment = dict(entry.get("environment") or {}) if isinstance(entry.get("environment"), dict) else {}
    if api_base:
        environment[API_BASE] = api_base
    else:
        environment.pop(API_BASE, None)
    if environment:
        entry["environment"] = environment
    else:
        entry.pop("environment", None)

    # 1.x writes enabled: false, the 2.x servers form disabled: true; carry the
    # user's switch across when the entry moves between the two.
    if layout == "servers" and entry.get("enabled") is False:
        entry.pop("enabled")
        entry["disabled"] = True
    elif layout != "servers" and entry.get("disabled") is True:
        entry.pop("disabled")
        entry["enabled"] = False
    mcp = new.setdefault("mcp", {})
    servers = _servers_container(mcp)
    if isinstance(mcp.get(NAME), dict) and entry_script(mcp[NAME]) is not None:
        del mcp[NAME]
    if servers is not None:
        servers.pop(NAME, None)
        if not servers and layout != "servers":
            del mcp["servers"]
    if layout == "servers":
        mcp.setdefault("servers", {})[NAME] = entry
    else:
        mcp[NAME] = entry
    if new == config:
        return config, "unchanged"
    if replacing:
        return new, "updated"
    return new, "updated" if found else "added"


def remove_entry(config: dict[str, Any], repo: str) -> tuple[dict[str, Any], bool]:
    """The config without a fluentflow-local entry that starts a script inside
    ``repo``; entries pointing elsewhere (another install) stay."""
    new = copy.deepcopy(config)
    removed = False
    prefix = os.path.normcase(os.path.normpath(repo)) + os.sep
    mcp = new.get("mcp")
    if not isinstance(mcp, dict):
        return config, False
    holders = [mcp]
    servers = _servers_container(mcp)
    if servers is not None:
        holders.append(servers)
    for holder in holders:
        entry = holder.get(NAME)
        if not isinstance(entry, dict):
            continue
        target = entry_script(entry)
        if target and os.path.normcase(os.path.normpath(target)).startswith(prefix):
            del holder[NAME]
            removed = True
    return (new, True) if removed else (config, False)


def read_config(path: Path) -> dict[str, Any] | None:
    """The parsed file, {} when it is not there, None when it is not a JSON object."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_config(path: Path, config: dict[str, Any]) -> None:
    """Write next to the file and swap it in, keeping the file's permissions."""
    path = path.resolve()  # a symlinked config (dotfile managers) keeps its link
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else None
    fd, temp = tempfile.mkstemp(prefix=".opencode.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
        if mode is not None:
            os.chmod(temp, mode)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _snippet(python: str, script: str, api_base: str | None, layout: str) -> str:
    entry: dict[str, Any] = {"type": "local", "command": [python, script]}
    if api_base:
        entry["environment"] = {API_BASE: api_base}
    body = {"mcp": {"servers": {NAME: entry}}} if layout == "servers" else {"mcp": {NAME: entry}}
    return json.dumps(body, ensure_ascii=False, indent=2)


def register(python: str, script: str, api_base: str | None) -> int:
    if not installed():
        return 3
    path = config_file(config_dir())
    config = read_config(path)
    version = opencode_version()
    if config is None:
        print(f"OpenCode：{path} 不是能直接改的 JSON（可能带注释），没有动它。把下面这段合进去：")
        print(_snippet(python, script, api_base, layout_for(version, {})))
        return 2
    layout = layout_for(version, config)
    new, what = merge_entry(config, python, script, api_base, layout)
    if what == "unsupported_shape":
        print(f"OpenCode：{path} 里的 mcp 不是常见的写法，没有动它。把下面这段合进去：")
        print(_snippet(python, script, api_base, layout))
        return 2
    messages = {
        "added": f"OpenCode：已注册 {NAME}（{path}）。",
        "updated": f"OpenCode：已更新 {NAME}（{path}）。",
        "unchanged": f"OpenCode：已注册过 {NAME}（这一份），保持不动。",
        "other_install": f"OpenCode：{NAME} 已注册到另一份还在的安装，不动它。卸载那份后重新运行本命令，就会注册到这一份。",
        "unreadable_entry": f"OpenCode：{NAME} 已注册，但认不出它启动的是哪个脚本，不动它。",
    }
    if new is not config:
        write_config(path, new)
    print(messages[what])
    return 2 if what in ("other_install", "unreadable_entry") else 0


def unregister(repo: str, check: bool) -> int:
    """Remove this install's entry. With ``check``, only say whether there is one
    to remove (exit 0) or not (exit 1), changing nothing."""
    path = config_file(config_dir())
    config = read_config(path)
    if config is None:
        if path.exists() and NAME in path.read_text(encoding="utf-8", errors="replace"):
            print(f"OpenCode：{path} 不是能直接改的 JSON，没有动它；请手动删掉里面的 {NAME}。")
            return 2
        return 1 if check else 0
    new, removed = remove_entry(config, repo)
    if not removed:
        if check and _entries(config):
            print(f"OpenCode 里的 {NAME} 指向别的位置，不动它。")
        return 1 if check else 0
    if check:
        return 0
    write_config(path, new)
    print(f"已注销：OpenCode 里的 {NAME}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="action", required=True)
    add = sub.add_parser("register")
    add.add_argument("--python", required=True)
    add.add_argument("--script", required=True)
    add.add_argument("--port", default="8000")
    drop = sub.add_parser("unregister")
    drop.add_argument("--repo", required=True)
    drop.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    # A Windows console in another code page cannot print every character of a
    # path: never fail the install over a message.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if args.action == "register":
        api_base = None if str(args.port) == "8000" else f"http://127.0.0.1:{args.port}"
        return register(args.python, args.script, api_base)
    return unregister(args.repo, args.check)


if __name__ == "__main__":
    sys.exit(main())
