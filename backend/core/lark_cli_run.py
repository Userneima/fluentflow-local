"""Running the local ``lark-cli`` and reading what it prints.

Finding the binary, the environment it runs in, turning its JSON envelope into
data or a :class:`LarkCliError`, and whether it is signed in. Every lark-cli
call of the Feishu export goes through ``_run`` here, looked up on this module
at call time (``lark_cli_run._run``), so a test that replaces ``_run`` or
``_resolve_lark_cli_bin`` replaces it here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, List, Optional

# Marker the diagnostics layer keys on; keep it stable.
LARK_CLI_NOT_FOUND = "lark-cli not found"

Runner = Callable[..., subprocess.CompletedProcess]


class LarkCliError(RuntimeError):
    """A lark-cli command failed; ``str()`` carries what the user should read."""


def _resolve_lark_cli_bin(explicit: Optional[str] = None) -> Optional[str]:
    if explicit and explicit.strip():
        p = explicit.strip()
        if os.path.isfile(p):
            return p
        return shutil.which(p)
    env_bin = (os.environ.get("FLUENTFLOW_LARK_CLI_BIN") or "").strip()
    if env_bin:
        if os.path.isfile(env_bin):
            return env_bin
        return shutil.which(env_bin)
    return shutil.which("lark-cli")


def _cli_env() -> dict[str, str]:
    env = os.environ.copy()
    # Keep update / skill notices out of the JSON we parse.
    env.setdefault("LARKSUITE_CLI_NO_UPDATE_NOTIFIER", "1")
    env.setdefault("LARKSUITE_CLI_NO_SKILLS_NOTIFIER", "1")
    return env


def _first_json_object(text: str) -> Optional[dict[str, Any]]:
    """The first JSON object in ``text``, skipping progress lines before it."""
    raw = text or ""
    decoder = json.JSONDecoder()
    start = raw.find("{")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(raw[start:])
        except ValueError:
            start = raw.find("{", start + 1)
            continue
        if isinstance(value, dict):
            return value
        start = raw.find("{", start + 1)
    return None


def _tail(text: str, limit: int = 400) -> str:
    cleaned = (text or "").strip()
    return cleaned if len(cleaned) <= limit else "…" + cleaned[-limit:]


def parse_lark_cli_output(proc: subprocess.CompletedProcess) -> dict[str, Any]:
    """Return the success envelope's ``data``, or raise :class:`LarkCliError`.

    lark-cli writes the success envelope to stdout and the error envelope to
    stderr, and may print progress lines before either. Success is decided by
    the envelope's own ``ok`` (or, without one, the exit code) — never by
    whether this parser coped with the text around it.
    """
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    payload = _first_json_object(stdout)
    if payload is None or (payload.get("ok") is not True and proc.returncode != 0):
        payload = _first_json_object(stderr) or payload
    if isinstance(payload, dict) and payload.get("ok") is True:
        data = payload.get("data")
        return data if isinstance(data, dict) else {}
    if isinstance(payload, dict) and payload.get("ok") is False:
        raise LarkCliError(_describe_cli_error(payload.get("error")))
    raw_tail = _tail(stderr or stdout)
    if proc.returncode == 0:
        raise LarkCliError(
            "lark-cli 执行完成，但返回内容里没有可读的结果"
            f"（输出末尾：{raw_tail or '空'}）"
        )
    raise LarkCliError(f"lark-cli 失败（退出码 {proc.returncode}）：{raw_tail or '没有输出'}")


def _describe_cli_error(error: Any) -> str:
    """One line that keeps lark-cli's own words plus the fields diagnostics need."""
    if not isinstance(error, dict):
        return f"lark-cli 失败：{error}"
    message = str(error.get("message") or "").strip()
    tags: List[str] = []
    for key in ("type", "subtype", "code"):
        value = error.get(key)
        if value not in (None, ""):
            tags.append(f"{key}={value}")
    scopes = error.get("missing_scopes")
    if isinstance(scopes, list) and scopes:
        tags.append("missing_scopes=" + ",".join(str(s) for s in scopes))
    hint = str(error.get("hint") or "").strip()
    text = f"lark-cli 失败 [{' '.join(tags)}]：{message or '没有说明'}"
    if hint:
        text += f"（hint: {hint}）"
    return text


def _run(
    runner: Runner,
    cmd: List[str],
    *,
    timeout: int,
    input_text: Optional[str] = None,
    cwd: Optional[Path] = None,
) -> dict[str, Any]:
    try:
        proc = runner(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_cli_env(),
            cwd=str(cwd) if cwd else None,
        )
    except subprocess.TimeoutExpired as exc:
        raise LarkCliError(f"lark-cli 超过 {timeout} 秒没有返回（{' '.join(cmd[1:3])}）") from exc
    except FileNotFoundError as exc:
        raise LarkCliError(f"{LARK_CLI_NOT_FOUND}: {exc}") from exc
    return parse_lark_cli_output(proc)


def lark_cli_ready(lark_cli_bin: Optional[str] = None, *, runner: Runner = subprocess.run) -> bool:
    """True when lark-cli is installed and has a usable user login."""
    bin_path = _resolve_lark_cli_bin(lark_cli_bin)
    if not bin_path:
        return False
    try:
        proc = runner(
            [bin_path, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=15,
            env=_cli_env(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    # ``auth status`` prints its report without the ok/data envelope.
    status = _first_json_object(proc.stdout or "") or {}
    if isinstance(status.get("data"), dict):
        status = status["data"]
    identities = status.get("identities") if isinstance(status.get("identities"), dict) else {}
    user = identities.get("user") if isinstance(identities.get("user"), dict) else {}
    return bool(user.get("available"))
