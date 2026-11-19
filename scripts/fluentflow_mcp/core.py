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
import sys
import threading
import time
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
_START_HINT = "请双击桌面上的「FluentFlow Local」启动它，等它把浏览器打开之后重试。"
# A 401 is almost always two copies of one token that drifted: the backend reads
# FLUENTFLOW_ACCESS_TOKEN from the repository .env, the MCP client sends whatever
# its own config says, and nobody can see both at once from inside a chat.
_UNAUTHORIZED_HINT = (
    "（后端拒绝了访问令牌。请核对仓库根目录 .env 里的 FLUENTFLOW_ACCESS_TOKEN "
    "和 MCP 客户端配置（例如 ~/.claude.json 或 Codex 的 config.toml）里的 "
    "FLUENTFLOW_ACCESS_TOKEN 是否完全一致；改过 .env 之后要重启 FluentFlow Local 才生效。）"
)


def _unreachable(api_base: str, reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": f"FluentFlow Local 没有在 {api_base} 应答（{reason}）。{_START_HINT}",
        "status": None,
        "payload": None,
    }


def _backend_edition(api_base: str) -> tuple[str, dict[str, Any] | None]:
    """The edition answering at ``api_base``, or the error payload explaining why not.

    Probed per call rather than cached: the case this exists for is a backend
    being swapped on a port, and a cached identity is wrong exactly then. One
    loopback GET is nothing beside submitting a media job.
    """
    try:
        health = api_request("GET", api_base, "/health", timeout=5)
    except FluentFlowApiError as exc:
        return "", _unreachable(api_base, str(exc))
    edition = str(health.get("edition") or "").strip().lower()
    if not edition:
        # A backend from before /health declared its edition: runtime.execution
        # was the only discriminator then, and only the local edition set it.
        runtime = health.get("runtime") if isinstance(health.get("runtime"), dict) else {}
        edition = "local" if str(runtime.get("execution") or "").strip().lower() == "local" else "hosted"
    return edition, None


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
                f"{_START_HINT}"
            ),
            "status": None,
            "payload": None,
        }
    return None


# What the backend says when a task id matches nothing, in either wording, and
# what an agent is told instead: the usual cause is an id copied short.
_MISSING_TASK = ("Job not found", "任务不存在")
_TASK_NOT_FOUND = "没有这个任务（task_id 要完整的 32 位）。用 list_tasks 按标题找到它的编号。"


def _agent_request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    api_base: str | None = None,
    client_id: str | None = None,
    timeout: float = 60,
) -> dict[str, Any]:
    base = normalize_api_base(api_base)
    try:
        return api_request(
            method,
            base,
            path,
            payload=payload,
            client_id=_client_id(client_id),
            access_token=os.environ.get("FLUENTFLOW_ACCESS_TOKEN"),
            timeout=timeout,
        )
    except FluentFlowApiError as exc:
        if exc.status is None:
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
HOLD_SECONDS = 240
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
