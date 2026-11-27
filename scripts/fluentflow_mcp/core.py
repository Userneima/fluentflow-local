"""The connection to FluentFlow and what every tool shares.

Holds the call to the backend (``_agent_request``, the access token, the
client id, which edition answers), the hold that answers a long call "still
working" (``_held``), the heartbeat that tells FluentFlow which tasks this
conversation watches (``_watch``), the result-length helper, and the ``tool``
decorator every tool module registers with. Tool modules call these through
this module (``core._agent_request(...)``) so a test that replaces one here
replaces it for every tool.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.local_agent_client import (  # noqa: E402
    DEFAULT_CLIENT_ID,
    FluentFlowApiError,
    api_request,
    normalize_api_base,
)


def _token_from_project_env() -> None:
    """Use the access token the installer wrote into the repository .env.

    The MCP client's own configuration may pass it, and then that value wins; when
    it does not, the token comes from the same file the backend reads, so the two
    sides cannot disagree and registering the server needs no secret.
    """
    if (os.environ.get("FLUENTFLOW_ACCESS_TOKEN") or "").strip():
        return
    try:
        lines = (PROJECT_ROOT / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        key, _, value = line.strip().partition("=")
        if key.strip() == "FLUENTFLOW_ACCESS_TOKEN" and value.strip():
            os.environ["FLUENTFLOW_ACCESS_TOKEN"] = value.strip().strip("'\"")
            return


_token_from_project_env()


def _app_version() -> str:
    try:
        return (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


SERVER_INFO = {"name": "fluentflow-local", "version": _app_version()}


def _client_id(value: str | None = None) -> str:
    return (value or os.environ.get("FLUENTFLOW_CLIENT_ID") or DEFAULT_CLIENT_ID).strip() or DEFAULT_CLIENT_ID


# Which FluentFlow is answering, asked rather than assumed.
#
# Two editions serve the same ``/agent/v1`` routes with different intake, and for
# a while both answered on 127.0.0.1:8000. The rejection a path submission gets
# from the hosted edition lists the inputs that one accepts and never says which
# backend replied, so on 2026-09-15 it was read as "the MCP tool is newer than
# this backend" and a job this edition could have done fell back to standalone
# scripts. ``/health`` now declares the edition; only the tools that actually
# need one ask, so pointing this client at either backend still works for the
# ten tools both editions serve.
_START_MANUALLY = "请双击桌面上的「FluentFlow Local」启动它，等它把浏览器打开之后重试。"
# A 401 is almost always two copies of one token that drifted: the backend reads
# FLUENTFLOW_ACCESS_TOKEN from the repository .env, the MCP client sends whatever
# its own config says, and nobody can see both at once from inside a chat.
_UNAUTHORIZED_HINT = (
    "（后端拒绝了访问令牌。请核对仓库根目录 .env 里的 FLUENTFLOW_ACCESS_TOKEN "
    "和 MCP 客户端配置（例如 ~/.claude.json 或 Codex 的 config.toml）里的 "
    "FLUENTFLOW_ACCESS_TOKEN 是否完全一致；改过 .env 之后要重启 FluentFlow Local 才生效。）"
)


def _start_hint() -> str:
    if _autostart_allowed() and _launcher_path() is not None:
        return (
            "FluentFlow Local 没在运行时，工具会自己在后台启动它、不打开浏览器；"
            f"启动不成功时再{_START_MANUALLY}"
        )
    return _START_MANUALLY


def _unreachable(api_base: str, reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": f"FluentFlow Local 没有在 {api_base} 应答（{reason}）。{_start_hint()}",
        "status": None,
        "payload": None,
    }


def _edition_of(health: dict[str, Any]) -> str:
    edition = str(health.get("edition") or "").strip().lower()
    if not edition:
        # A backend from before /health declared its edition: runtime.execution
        # was the only discriminator then, and only the local edition set it.
        runtime = health.get("runtime") if isinstance(health.get("runtime"), dict) else {}
        edition = "local" if str(runtime.get("execution") or "").strip().lower() == "local" else "hosted"
    return edition


def _local_health(api_base: str, timeout: float = 3) -> dict[str, Any] | None:
    """The health answer, when the local edition gives one."""
    try:
        health = api_request("GET", api_base, "/health", timeout=timeout)
    except FluentFlowApiError:
        return None
    return health if _edition_of(health) == "local" else None


def _health(api_base: str, timeout: float = 5) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """``/health``'s answer, or the error payload to return instead.

    Nothing answering on this machine is the one case where the service is
    started first (decision 1.20); every other failure keeps its own message.
    """
    try:
        return api_request("GET", api_base, "/health", timeout=timeout), None
    except FluentFlowApiError as exc:
        if exc.status is None:
            started = _start_local_service(api_base)
            if started is not None:
                if started.get("ok") is False:
                    return None, started
                try:
                    return api_request("GET", api_base, "/health", timeout=timeout), None
                except FluentFlowApiError as again:
                    return None, _unreachable(api_base, str(again))
        return None, _unreachable(api_base, str(exc))


def _backend_edition(api_base: str) -> tuple[str, dict[str, Any] | None]:
    """The edition answering at ``api_base``, or the error payload explaining why not.

    Probed per call rather than cached: the case this exists for is a backend
    being swapped on a port, and a cached identity is wrong exactly then. One
    loopback GET is nothing beside submitting a media job.
    """
    health, failure = _health(api_base)
    if failure is not None:
        return "", failure
    return _edition_of(health or {}), None


def _require_local_edition(api_base: str) -> dict[str, Any] | None:
    """Why this tool cannot run against ``api_base``, or None when it can."""
    edition, failure = _backend_edition(api_base)
    if failure is not None:
        return failure
    if edition != "local":
        return {
            "ok": False,
            "error": (
                f"{api_base} 上应答的是 FluentFlow Hosted，这个工具要的是 FluentFlow Local。"
                "托管版按设计不收本机文件路径，它拒绝提交并不代表本地处理这个功能不存在。"
                f"{_START_MANUALLY}"
            ),
            "status": None,
            "payload": None,
        }
    return None


# ── starting the service ─────────────────────────────────────────────────────
#
# The MCP tools answer only while the service runs, and the service used to be
# started only by opening the desktop app, which also opened a browser page: an
# agent asked for a note had to send the user off to click something first
# (2026-11-26, decision 1.20). When a tool is called and nothing answers on this
# machine, this process starts the launcher itself, detached, with the page
# suppressed. The desktop app still opens the page: clicking it is the person
# saying they want to see FluentFlow.
AUTOSTART_SECONDS = 90.0
AUTOSTART_POLL_SECONDS = 0.5
# How long the service gets to answer after the launcher itself has returned:
# the Windows launcher waits for /health before exiting, a launcher that put the
# service in the background may exit at once.
LAUNCHER_EXIT_GRACE_SECONDS = 5.0
# An install being updated writes this beside its install folder before it stops
# anything, and removes it on every way out (docs/04-维护手册.md「更新进行中的
# 标记」): stopped service, code folder being swapped, virtual environment being
# rebuilt. A service started from that tree is half-built.
UPDATING_MARKER_SUFFIX = ".fluentflow-updating"
# An hour, the same bound the installers use: one older than that was left by a
# killed run, and they clear it on their next run and go ahead. Refusing it
# forever would leave an agent with no way to get the service started.
UPDATING_MARKER_STALE_SECONDS = 3600.0
_NO_START_VALUES = ("0", "false", "no", "off")


def _autostart_allowed() -> bool:
    """``FLUENTFLOW_MCP_AUTOSTART=0`` for people who start the service themselves."""
    return (os.environ.get("FLUENTFLOW_MCP_AUTOSTART") or "").strip().lower() not in _NO_START_VALUES


def _log_dir() -> Path:
    """The launchers' own log folder; the autostart log and its lock live here."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "FluentFlow"
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "FluentFlow" / "Logs"
    return Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")) / "fluentflow" / "log"


def _local_target(api_base: str) -> tuple[str, int] | None:
    """Where to start the service, when ``api_base`` is this machine.

    ``[::1]`` is not accepted: the launchers bind 127.0.0.1 only, so starting one
    for an IPv6 address would wait out the whole 90 seconds and fail.
    """
    try:
        parts = urllib.parse.urlsplit(api_base)
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme != "http" or host not in ("127.0.0.1", "localhost"):
        return None
    return host, port or 80


# The port a launcher would use, by the launchers' own rule
# (`FluentFlowLocal.command` configured_port, `Get-FluentFlowPort`): the
# environment first, then the last matching line of the repository .env, then
# 8000. An environment value that is not a plain number is 8000 — not a reason to
# look in .env: the Windows launcher takes whatever the environment says and only
# falls back to 8000 when it is not a number, so falling through here would
# compute a port the launcher never uses.
_DEFAULT_PORT = 8000
_PORT_LINE = re.compile(r"^\s*(?:export\s+)?FLUENTFLOW_LOCAL_PORT\s*=(.*)$")


def _configured_port() -> int:
    raw = os.environ.get("FLUENTFLOW_LOCAL_PORT") or ""
    if raw:
        return int(raw) if re.fullmatch(r"[0-9]+", raw) else _DEFAULT_PORT
    try:
        text = (PROJECT_ROOT / ".env").read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return _DEFAULT_PORT
    found = None
    for line in text.splitlines():
        match = _PORT_LINE.match(line)
        if match:
            found = match.group(1)
    if found is None:
        return _DEFAULT_PORT
    value = re.split(r"\s+#", found.strip(), maxsplit=1)[0].strip().strip("'\"")
    return int(value) if value.isdigit() else _DEFAULT_PORT


def _port_answers(host: str, port: int) -> bool:
    """Whether anything answers on this port here, FluentFlow or not."""
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _launcher_path() -> Path | None:
    """The launcher this machine starts the service with, when it has one."""
    if sys.platform == "darwin":
        script = PROJECT_ROOT / "launchers" / "macos" / "FluentFlowLocal.command"
    elif os.name == "nt":
        script = PROJECT_ROOT / "launchers" / "windows" / "FluentFlowLocal.ps1"
    else:
        return None
    return script if script.is_file() else None


def _launcher_command(script: Path) -> list[str]:
    if os.name == "nt":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    return [str(script)]


def _launcher_environment() -> dict[str, str]:
    """What the desktop app would hand the launcher, minus the page.

    The port is not passed: the launcher reads it by its own rule, and
    ``_start_local_service`` only starts anything when that rule agrees with the
    address this client was told to use. Forcing this client's port here is how
    a second service on the same data would have been started beside one the
    user moved to another port in .env.
    """
    env = {**os.environ}
    env["FLUENTFLOW_OPEN_PAGE"] = "0"
    env["FLUENTFLOW_REPO"] = str(PROJECT_ROOT)
    return env


def _run_launcher(log_path: Path) -> subprocess.Popen | None:
    """Start the launcher detached, its output in ``log_path``.

    Detached so it outlives this process (the conversation it belongs to may end
    while the service keeps working), stdin from nowhere so a launcher that asks
    something reads EOF instead of a conversation, and on Windows without a
    console window.
    """
    script = _launcher_path()
    if script is None:
        return None
    log_path.parent.mkdir(parents=True, exist_ok=True)
    output = open(log_path, "a", encoding="utf-8", errors="replace")
    try:
        options: dict[str, Any] = {}
        if os.name == "nt":
            options["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        else:
            options["start_new_session"] = True
        return subprocess.Popen(
            _launcher_command(script),
            cwd=str(PROJECT_ROOT),
            env=_launcher_environment(),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            **options,
        )
    finally:
        output.close()


# Two tool calls, or two agent sessions, can arrive at a dead service at the
# same moment; only one of them may start one. The second waits here and then
# finds the service answering instead of starting its own.
_starter_handle: Any = None


def _acquire_starter_lock(deadline: float) -> bool:
    global _starter_handle
    try:
        path = _log_dir() / "mcp-autostart.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a+", encoding="utf-8")
    except OSError:
        return True  # no lock file to make here: starting is still better than not
    while True:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            _starter_handle = handle
            return True
        except OSError:
            if time.monotonic() >= deadline:
                handle.close()
                return False
            time.sleep(0.2)


def _release_starter_lock() -> None:
    global _starter_handle
    handle, _starter_handle = _starter_handle, None
    if handle is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    handle.close()


def _log_tail(path: Path, lines: int = 12, chars: int = 1500) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "（启动日志还没写出来）"
    return "\n".join(text.splitlines()[-lines:])[-chars:] or "（启动日志还是空的）"


def _start_failed(api_base: str, log_path: Path, reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": (
            f"FluentFlow Local 没有在 {api_base} 应答。已经试着在后台启动它，但{reason}。\n"
            f"启动日志：{log_path}\n最后几行：\n{_log_tail(log_path)}\n"
            "再调一次会重新启动；也可以双击桌面上的「FluentFlow Local」看看它报什么。"
        ),
        "status": None,
        "payload": None,
    }


def _install_folder() -> Path:
    """The folder the user installed into.

    The rule lives in ``release_check.install_folder`` (a Windows one-folder
    install keeps the code in ``<folder>\\app``); it is imported here rather than
    at the top because the MCP server starts with every conversation and this is
    only asked when a start is being considered.
    """
    from backend.core.release_check import install_folder

    return install_folder()


def _updating_marker() -> Path | None:
    """The marker of an update running right now, or None.

    One left by a killed run is stale after an hour, the same bound the
    installers use, and is ignored: they clear it and go ahead on their next run,
    and refusing for ever would leave the agent unable to start FluentFlow.
    """
    marker = Path(f"{_install_folder()}{UPDATING_MARKER_SUFFIX}")
    try:
        age = time.time() - marker.stat().st_mtime
    except OSError:
        return None
    return marker if age < UPDATING_MARKER_STALE_SECONDS else None


def _updating(marker: Path) -> dict[str, Any]:
    return {
        "ok": False,
        "code": "updating",
        "error": (
            f"FluentFlow Local 正在更新（安装文件夹旁边的 {marker} 还在），这会儿从这份代码起服务会拿到装了一半的一份，所以没有起。"
            "更新结束那个文件会被删掉，等几分钟再调一次就行。"
            "确认没有更新在跑时（例如这是被强杀的那次留下的），删掉那个文件再试。"
        ),
        "status": None,
        "payload": None,
    }


def _port_disagreement(api_base: str, configured: int, asked: int) -> dict[str, Any]:
    """The launcher would use another port than the one this client asks.

    Starting it would put a second service on the same code and data beside the
    one already there: each startup treats the other's running tasks as stranded
    by a restart and re-queues them, so both would process the same recording.
    """
    running = _local_health(f"http://127.0.0.1:{configured}") is not None
    where = f"服务现在就在 {configured} 端口上跑着" if running else f"启动器会把服务起在 {configured} 端口"
    return {
        "ok": False,
        "error": (
            f"MCP 工具连的是 {api_base}，但 .env 里的 FLUENTFLOW_LOCAL_PORT 是 {configured}，{where}。"
            f"没有为了凑 {asked} 端口再起一个：同一个代码和数据上两个服务会把对方的任务当成重启中断、重复处理。\n"
            f"两种改法：把 MCP 注册里的 FLUENTFLOW_API_BASE 改成 http://127.0.0.1:{configured}"
            "（Claude Code 用 claude mcp remove/add 重新注册，Codex 改 config.toml），"
            f"或把 .env 里的 FLUENTFLOW_LOCAL_PORT 改回 {asked} 后重启 FluentFlow Local。"
        ),
        "status": None,
        "payload": None,
    }


def _start_local_service(api_base: str) -> dict[str, Any] | None:
    """Start the local service when nothing answers here.

    None means it was not this tool's to start — an address on another machine,
    the switch turned off, no launcher on this platform, something already
    holding the port (another program, the hosted edition, a FluentFlow whose
    health check is failing), or a .env port that disagrees with this client's.
    Nothing is started or stopped then, and the caller keeps its own message
    (except for the port, where the caller has none). An install that is being
    updated right now is a refusal of its own: the tree is half-built.
    """
    target = _local_target(api_base)
    if target is None or not _autostart_allowed() or _launcher_path() is None:
        return None
    updating = _updating_marker()
    if updating is not None:
        return _updating(updating)
    host, port = target
    if _port_answers(host, port):
        return None
    configured = _configured_port()
    if configured != port:
        return _port_disagreement(api_base, configured, port)
    log_path = _log_dir() / "mcp-autostart.log"
    deadline = time.monotonic() + AUTOSTART_SECONDS
    locked = _acquire_starter_lock(deadline)
    try:
        if _local_health(api_base) is not None:
            return {"ok": True}  # another caller started it while this one waited
        if not locked:
            return _start_failed(api_base, log_path, "另一个 FluentFlow 启动还没有结束，服务仍没有应答")
        try:
            process = _run_launcher(log_path)
        except OSError as exc:
            return _start_failed(api_base, log_path, f"启动器没能跑起来：{exc}")
        if process is None:
            return None
        exited_at: float | None = None
        while True:
            if _local_health(api_base) is not None:
                return {"ok": True}
            if process.poll() is not None:
                # A launcher may start the service and return (the Windows one
                # waits for /health itself, a detached one may not): give the
                # service a moment before calling the exit a failure.
                exited_at = exited_at or time.monotonic()
                if time.monotonic() - exited_at > LAUNCHER_EXIT_GRACE_SECONDS:
                    return _start_failed(api_base, log_path, f"启动器退出了（退出码 {process.returncode}）")
            if time.monotonic() >= deadline:
                return _start_failed(
                    api_base, log_path, f"等了 {int(AUTOSTART_SECONDS)} 秒服务仍没有应答"
                )
            time.sleep(AUTOSTART_POLL_SECONDS)
    finally:
        _release_starter_lock()


# What the backend says when a task id matches nothing, in either wording, and
# what an agent is told instead: the usual cause is an id copied short.
_MISSING_TASK = ("Job not found", "任务不存在")
_TASK_NOT_FOUND = "没有这个任务（task_id 要完整的 32 位）。用 list_tasks 按标题找到它的编号。"


def _send(
    method: str,
    base: str,
    path: str,
    *,
    payload: dict[str, Any] | None,
    client_id: str | None,
    timeout: float,
) -> dict[str, Any]:
    return api_request(
        method,
        base,
        path,
        payload=payload,
        client_id=_client_id(client_id),
        access_token=os.environ.get("FLUENTFLOW_ACCESS_TOKEN"),
        timeout=timeout,
    )


def _never_sent(exc: FluentFlowApiError) -> bool:
    """Whether the request certainly never reached the service.

    Connection refused (and a name that does not resolve) is the connect itself
    failing, so no byte of the call was delivered and the same call can go out
    again. Anything else without a status — a reset or a timeout after the
    request was written — may have been carried out.
    """
    reason = getattr(exc.__cause__, "reason", None)
    return isinstance(reason, (ConnectionRefusedError, socket.gaierror))


def _may_have_arrived(exc: FluentFlowApiError) -> bool:
    """The request went out and then the connection was lost (status is None)."""
    return isinstance(exc.__cause__, urllib.error.URLError) and not _never_sent(exc)


def _outcome_unknown(api_base: str, method: str, exc: FluentFlowApiError) -> dict[str, Any]:
    return {
        "ok": False,
        "code": "result_unknown",
        "error": (
            f"调用 {method} {api_base} 时请求已经发出去，之后连接断了（{exc}），"
            "这一次有没有在 FluentFlow 里生效不确定。先确认现状再决定要不要重试（例如 list_tasks 看有没有多出来的任务，"
            "get_task 看这一条到哪一步了）；不确认就重试，提交类的调用会建出第二条任务。"
        ),
        "status": None,
        "payload": None,
    }


def _agent_request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    api_base: str | None = None,
    client_id: str | None = None,
    timeout: float = 60,
    autostart: bool = True,
) -> dict[str, Any]:
    base = normalize_api_base(api_base)
    sent = {"payload": payload, "client_id": client_id, "timeout": timeout}
    try:
        return _send(method, base, path, **sent)
    except FluentFlowApiError as exc:
        if exc.status is None and autostart:
            # Nothing answered: the service may simply not be running. Start it
            # (decision 1.20), and repeat the call only when it certainly never
            # went out: a submit sent once and lost would otherwise make a
            # second task.
            started = _start_local_service(base)
            if started is not None and started.get("ok") is False:
                return started
            if started is not None and _never_sent(exc):
                try:
                    return _send(method, base, path, **sent)
                except FluentFlowApiError as again:
                    exc = again
        if exc.status is None:
            if _may_have_arrived(exc):
                return _outcome_unknown(base, method, exc)
            # No HTTP status means nothing answered. Say how to start it instead of
            # handing the caller a bare "Connection refused" to interpret.
            return _unreachable(base, str(exc))
        message = str(exc)
        if exc.status == 401:
            message += _UNAUTHORIZED_HINT
        answer: dict[str, Any] = {"ok": False, "error": message, "status": exc.status}
        # The backend's detail is the message above; say it once, and lift what
        # else it carries (code, existing_task_id, ...) to where agents look.
        detail = exc.payload.get("detail") if isinstance(exc.payload, dict) else None
        if isinstance(detail, dict):
            answer.update({k: v for k, v in detail.items() if k not in ("message", "detail") and k not in answer})
        if exc.status == 404 and message in _MISSING_TASK and "/tasks/" in path:
            answer.update(code="task_not_found", error=_TASK_NOT_FOUND)
        return answer


def _options(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value not in (None, "")}


# A call that can take longer than a host waits for one tool call. Codex stops
# waiting after 300 seconds unless its config raises tool_timeout_sec (measured
# 2026-10-27, decision 5.40) while FluentFlow goes on working; writing a review
# note takes up to 45 minutes. So such a call is answered "still working" after
# HOLD_SECONDS, and calling the same tool again with the same arguments waits
# on the same run instead of starting a second one. The run lives on a daemon
# thread of this process: when the conversation ends, so does the waiting.
HOLD_SECONDS: float | None = 240  # None: wait until done (the command line, one call per process)
# A finished run nobody came back for is dropped after this, so asking for the
# same thing much later starts afresh instead of returning an old answer.
UNCLAIMED_SECONDS = 1800


class _Run:
    def __init__(self, work: Any) -> None:
        self.done = threading.Event()
        self.answer: dict[str, Any] | None = None
        self.finished_at = 0.0
        self.thread = threading.Thread(target=self._go, args=(work,), name="fluentflow-long-call", daemon=True)

    def _go(self, work: Any) -> None:
        try:
            self.answer = work()
        except Exception as exc:  # noqa: BLE001 - said to the agent, not raised on a thread nobody reads
            self.answer = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.finished_at = time.monotonic()
        self.done.set()


_runs: dict[str, _Run] = {}
_runs_lock = threading.Lock()


def _held(tool_name: str, arguments: dict[str, Any], work: Any) -> dict[str, Any]:
    """``work()``'s answer if it comes within HOLD_SECONDS, else "still
    working"; the same tool with the same arguments picks the run up again."""
    key = tool_name + ":" + hashlib.sha256(
        json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    with _runs_lock:
        now = time.monotonic()
        for stale in [k for k, r in _runs.items() if r.done.is_set() and now - r.finished_at > UNCLAIMED_SECONDS]:
            del _runs[stale]
        run = _runs.get(key)
        if run is None:
            run = _runs[key] = _Run(work)
            run.thread.start()
    if not run.done.wait(HOLD_SECONDS):
        return {
            "ok": True,
            "code": "still_working",
            "message": "FluentFlow 还在做这一步，没有出错。",
            "next": (f"Call {tool_name} again with exactly the same arguments: it keeps waiting on this run and "
                     "does not start another. Say nothing to the user in between."),
        }
    with _runs_lock:
        if _runs.get(key) is run:
            del _runs[key]
    return run.answer or {"ok": False, "error": "no answer"}


# Which tasks this conversation is responsible for, reported to FluentFlow
# every half minute while this process lives. Every agent conversation starts
# its own MCP process and the process ends with the conversation, so when the
# reports stop, a task waiting for this agent to judge its cut stops waiting
# (``backend/core/agent_cut.py``).
HEARTBEAT_SECONDS = 30.0
_watched: dict[str, tuple[str | None, str | None]] = {}
_watch_lock = threading.Lock()
_heart: threading.Thread | None = None
# Only a real MCP process (``main``) reports; imported for tests it does not.
_heartbeat_enabled = False


def _watch(task_id: Any, api_base: str | None, client_id: str | None) -> None:
    global _heart
    if not task_id:
        return
    with _watch_lock:
        _watched[str(task_id)] = (api_base, client_id)
        if _heartbeat_enabled and _heart is None:
            _heart = threading.Thread(target=_heartbeat_loop, name="fluentflow-heartbeat", daemon=True)
            _heart.start()


def _heartbeat_once() -> None:
    with _watch_lock:
        groups: dict[tuple[str | None, str | None], list[str]] = {}
        for task_id, where in _watched.items():
            groups.setdefault(where, []).append(task_id)
    for (api_base, client_id), ids in groups.items():
        answer = _agent_request(
            "POST", "/agent/v1/agent-cut/heartbeat", api_base=api_base, client_id=client_id,
            payload={"task_ids": ids}, timeout=10,
            # A beat is this process's own bookkeeping, not a tool call: it must
            # not start a service the user just stopped.
            autostart=False,
        )
        if not isinstance(answer, dict) or answer.get("ok") is False or not isinstance(answer.get("watching"), list):
            continue  # FluentFlow not answering (restarting): keep them all
        keep = {str(item) for item in answer["watching"]}
        with _watch_lock:
            for task_id in ids:
                if task_id not in keep:
                    _watched.pop(task_id, None)


def _heartbeat_loop() -> None:
    global _heart
    while True:
        try:
            _heartbeat_once()
        except Exception:  # noqa: BLE001 - a missed beat is retried
            pass
        with _watch_lock:
            if not _watched:
                # Cleared under the lock: a task watched once this lock is
                # released starts a new loop, not one that is about to end.
                _heart = None
                return
        time.sleep(HEARTBEAT_SECONDS)


def _watching(answer: dict[str, Any], task_id: Any, api_base: str | None, client_id: str | None) -> dict[str, Any]:
    if isinstance(answer, dict) and answer.get("ok") is not False:
        _watch(task_id or answer.get("task_id"), api_base, client_id)
    return answer


# Codex cuts a tool result at about 10,000 tokens whatever the tool declares
# (``anthropic/maxResultSizeChars`` is read by Claude Code only): in its
# records of 2026-10 the note material of a 15-minute recording was cut. These
# two fields go first, the part a cut keeps, so the agent knows how long the
# whole answer is and how to read the rest.
_CUT_OFF_HINT = (
    "If your host shortened this result (a truncation notice, or fewer characters than result_chars), "
    "read the rest from the saved or stored full result in slices before you judge or write, or call "
    "again with small_parts=true where the tool takes it."
)


def _with_length(answer: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(answer, dict) or answer.get("ok") is False:
        return answer
    rest = {key: value for key, value in answer.items() if key not in {"result_chars", "if_cut_off"}}
    shaped = {"result_chars": 0, "if_cut_off": _CUT_OFF_HINT, **rest}
    for _ in range(3):  # the count's own digits change the count; settles at once
        shaped["result_chars"] = len(json.dumps(shaped, ensure_ascii=False))
    return shaped


# ── tools ────────────────────────────────────────────────────────────────────
#
# What an agent is told about a tool is written once, on the tool: the
# ``tool`` decorator right above each function holds its description and its
# input schema, and the lists the server answers with are made from them.

_TOOLS: dict[str, tuple[Any, dict[str, Any]]] = {}


def tool(*, description: str, input_schema: dict[str, Any], meta: dict[str, Any] | None = None):
    """Register the function below as a tool, described here and nowhere else."""
    def register(func):
        # api_base stays a keyword of every function (tests, another port), but
        # an agent never needs it: FLUENTFLOW_API_BASE picks the backend.
        properties = {k: v for k, v in (input_schema.get("properties") or {}).items() if k != "api_base"}
        schema = {**input_schema, "properties": properties} if "properties" in input_schema else input_schema
        definition = {"name": func.__name__, "description": description, "inputSchema": schema}
        if meta:
            definition["_meta"] = meta
        _TOOLS[func.__name__] = (func, definition)
        return func
    return register
