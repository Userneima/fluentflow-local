"""The MCP tools start the local service themselves when nothing answers.

Requirement (2026-11-26, decision 1.20): the agent flow is the default way to
get a note ("处理视频默认使用用户的 agent 交互"), but the tools only work while
the service runs, and the service used to be started only by opening the
desktop app — which also opened a browser page. So a tool call that finds
nothing answering on this machine starts the launcher in the background, with
the page suppressed, waits for the local edition and carries on with the call.

What must stay true:

- Only a loopback address with nothing on its port is started, and only when the
  port the launcher would use (its own rule: environment, then .env, then 8000)
  is the port this client was told to use. A second service on the same code and
  data would re-queue the first one's running tasks and process a recording twice.
- An address on another machine, an IPv6 loopback (the launcher binds 127.0.0.1),
  or a port something already holds (the hosted edition, another program, a
  FluentFlow whose health check is failing) is left alone.
- Two calls arriving together start one service, not two.
- A call is repeated only when it certainly never went out (connection refused);
  a submit whose connection dropped after it was sent answers "result unknown"
  instead of making a second task.
- ``FLUENTFLOW_MCP_AUTOSTART=0`` turns it off for people who run it themselves.
- A service that does not come up says it was tried, where the log is, and the
  launcher's last lines.
- The desktop app still opens the page; the background start does not.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
from pathlib import Path

import pytest

from scripts import fluentflow_mcp_server as mcp
from scripts.fluentflow_mcp import core
from scripts.local_agent_client import FluentFlowApiError

ROOT = core.PROJECT_ROOT
LAUNCHER = ROOT / "launchers" / "macos" / "FluentFlowLocal.command"
API = "http://127.0.0.1:8123"
LOCAL_HEALTH = {"status": "ok", "edition": "local", "runtime": {"execution": "local"}}
HOSTED_HEALTH = {"status": "ok", "edition": "hosted", "runtime": {}}
# Running the launcher's own bash function needs a POSIX shell; on Windows the
# PowerShell switch is checked statically instead (the test below).
posix_shell = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None, reason="needs a POSIX shell"
)


def _refused(message: str = "Cannot reach FluentFlow backend: Connection refused") -> FluentFlowApiError:
    """What `api_request` raises when the connect itself is refused."""
    error = FluentFlowApiError(message)
    error.__cause__ = urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
    return error


def _reset(message: str = "Cannot reach FluentFlow backend: Connection reset by peer") -> FluentFlowApiError:
    """What `api_request` raises when the connection drops after the request."""
    error = FluentFlowApiError(message)
    error.__cause__ = urllib.error.URLError(ConnectionResetError(54, "Connection reset by peer"))
    return error


class _Process:
    """The launcher as this process sees it: still running, or already exited."""

    def __init__(self, code: int | None = None) -> None:
        self.returncode = code

    def poll(self) -> int | None:
        return self.returncode


def _allow_autostart(monkeypatch, tmp_path: Path, launcher) -> list[int]:
    """Turn autostart on for one test, with the launcher itself replaced."""
    monkeypatch.setenv("FLUENTFLOW_MCP_AUTOSTART", "1")
    monkeypatch.setenv("FLUENTFLOW_LOCAL_PORT", "8123")
    monkeypatch.setattr(core, "_launcher_path", lambda: LAUNCHER)
    monkeypatch.setattr(core, "_log_dir", lambda: tmp_path)
    monkeypatch.setattr(core, "_port_answers", lambda host, port: False)
    monkeypatch.setattr(core, "AUTOSTART_POLL_SECONDS", 0.01)
    monkeypatch.setattr(core, "LAUNCHER_EXIT_GRACE_SECONDS", 0.05)
    starts: list[int] = []
    monkeypatch.setattr(core, "_run_launcher", launcher)
    return starts


def _answers(monkeypatch, health, *, result=None):
    """Answer every request as the service does; while ``health`` is an
    exception (or a callable returning one) nothing answers at all."""

    def fake_api_request(method, api_base, path, **kwargs):
        answer = health() if callable(health) else health
        if isinstance(answer, Exception):
            raise answer
        if path == "/health":
            return answer
        return result if result is not None else {"ok": True}

    monkeypatch.setattr(core, "api_request", fake_api_request)


def test_a_call_with_nothing_answering_starts_the_service_and_carries_on(monkeypatch, tmp_path):
    ready = threading.Event()

    def fake_launcher(log_path):
        starts.append(1)
        ready.set()
        return _Process()

    _answers(monkeypatch, lambda: LOCAL_HEALTH if ready.is_set() else _refused())
    starts = _allow_autostart(monkeypatch, tmp_path, fake_launcher)

    result = core._agent_request("POST", "/agent/v1/tasks", payload={"path": "/x.mov"}, api_base=API)

    assert result == {"ok": True}
    assert starts == [1], "the service was started once for the port the call addresses"


def test_the_launcher_is_told_not_to_open_the_page_and_keeps_its_own_port_rule():
    env = core._launcher_environment()
    assert env["FLUENTFLOW_OPEN_PAGE"] == "0"
    assert env["FLUENTFLOW_REPO"] == str(ROOT)
    # Forcing this client's port here is how a second service would start beside
    # the one .env points at; the launcher reads the port itself.
    assert env.get("FLUENTFLOW_LOCAL_PORT") == os.environ.get("FLUENTFLOW_LOCAL_PORT")


def test_two_calls_at_once_start_it_only_once(monkeypatch, tmp_path):
    ready = threading.Event()

    def fake_launcher(log_path):
        starts.append(1)
        time.sleep(0.2)  # a start in progress, as a real one takes seconds
        ready.set()
        return _Process()

    _answers(monkeypatch, lambda: LOCAL_HEALTH if ready.is_set() else _refused())
    starts = _allow_autostart(monkeypatch, tmp_path, fake_launcher)

    answers: list[dict] = []

    def call() -> None:
        answers.append(core._agent_request("POST", "/agent/v1/tasks", payload={}, api_base=API))

    threads = [threading.Thread(target=call) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)

    assert [answer.get("ok") for answer in answers] == [True, True], answers
    assert starts == [1], "the second caller waits for the first and finds the service up"


def test_a_heartbeat_never_starts_the_service(monkeypatch, tmp_path):
    """A beat is this process's own bookkeeping, not a tool call: a service the
    user stopped on purpose must stay stopped."""

    def fake_launcher(log_path):
        raise AssertionError("a heartbeat must not start the service")

    _answers(monkeypatch, _refused())
    _allow_autostart(monkeypatch, tmp_path, fake_launcher)
    monkeypatch.setitem(core._watched, "t1", (API, None))

    core._heartbeat_once()

    assert "t1" in core._watched  # nothing answered: the task stays watched


def test_a_backend_that_answers_is_never_started_over(monkeypatch, tmp_path):
    def fake_launcher(log_path):
        raise AssertionError("nothing may be started while a backend answers")

    _answers(monkeypatch, HOSTED_HEALTH, result={"ok": True, "task_id": "t1"})
    _allow_autostart(monkeypatch, tmp_path, fake_launcher)

    result = mcp.submit_local_media("/Users/me/meeting.mov", api_base=API)

    assert result["ok"] is False
    assert "FluentFlow Hosted" in result["error"]


def test_a_port_something_else_holds_is_left_alone(monkeypatch, tmp_path):
    def fake_launcher(log_path):
        raise AssertionError("a port that answers is not this tool's to take")

    _answers(monkeypatch, FluentFlowApiError("Backend returned non-JSON response: b'<html>'"))
    _allow_autostart(monkeypatch, tmp_path, fake_launcher)
    monkeypatch.setattr(core, "_port_answers", lambda host, port: True)

    result = core._agent_request("GET", "/agent/v1/status", api_base=API)

    assert result["ok"] is False
    assert "启动" in result["error"]


def test_the_switch_off_leaves_starting_to_the_person(monkeypatch, tmp_path):
    def fake_launcher(log_path):
        raise AssertionError("FLUENTFLOW_MCP_AUTOSTART=0 means do not start it")

    _answers(monkeypatch, _refused())
    _allow_autostart(monkeypatch, tmp_path, fake_launcher)
    monkeypatch.setenv("FLUENTFLOW_MCP_AUTOSTART", "0")

    result = core._agent_request("GET", "/agent/v1/status", api_base=API)

    assert result["ok"] is False
    assert "请双击桌面上的「FluentFlow Local」" in result["error"]


def test_an_address_on_another_machine_is_never_started(monkeypatch, tmp_path):
    def fake_launcher(log_path):
        raise AssertionError("only this machine's loopback address is started")

    _answers(monkeypatch, _refused())
    _allow_autostart(monkeypatch, tmp_path, fake_launcher)

    result = core._agent_request("GET", "/agent/v1/status", api_base="http://192.168.1.20:8000")

    assert result["ok"] is False
    assert "192.168.1.20" in result["error"]


def test_the_ipv6_loopback_is_not_started(monkeypatch, tmp_path):
    """The launchers bind 127.0.0.1; starting one for [::1] only burns the wait."""

    def fake_launcher(log_path):
        raise AssertionError("the launcher does not listen on ::1")

    _answers(monkeypatch, _refused())
    _allow_autostart(monkeypatch, tmp_path, fake_launcher)

    result = core._agent_request("GET", "/agent/v1/status", api_base="http://[::1]:8123")

    assert result["ok"] is False
    assert core._local_target("http://[::1]:8123") is None


def test_the_configured_port_follows_the_launchers_rule(monkeypatch, tmp_path):
    monkeypatch.delenv("FLUENTFLOW_LOCAL_PORT", raising=False)
    monkeypatch.setattr(core, "PROJECT_ROOT", tmp_path)
    assert core._configured_port() == 8000, "no setting anywhere means the launcher's default"
    (tmp_path / ".env").write_text(
        "FLUENTFLOW_LOCAL_PORT=8000\nFLUENTFLOW_LOCAL_PORT='8001'  # moved\n", encoding="utf-8"
    )
    assert core._configured_port() == 8001, "the last line wins, as the backend reads it"
    monkeypatch.setenv("FLUENTFLOW_LOCAL_PORT", "8123")
    assert core._configured_port() == 8123, "the environment wins over .env, as in the launcher"
    # Not a number in the environment: 8000, not the .env line. The Windows
    # launcher takes the environment's value as it is and only falls back to 8000
    # when it is not a number; looking in .env here would name a port no launcher
    # starts on, and the port check would then start a second service.
    for junk in ("not-a-port", " 8123", "8123 "):
        monkeypatch.setenv("FLUENTFLOW_LOCAL_PORT", junk)
        assert core._configured_port() == 8000, junk


@pytest.mark.parametrize("age_minutes,refused", [(0, True), (59, True), (61, False)])
def test_the_marker_refuses_until_it_is_an_hour_old(monkeypatch, tmp_path, age_minutes, refused):
    ready = threading.Event()

    def fake_launcher(log_path):
        starts.append(1)
        ready.set()
        return _Process()

    _answers(monkeypatch, lambda: LOCAL_HEALTH if ready.is_set() else _refused())
    starts = _allow_autostart(monkeypatch, tmp_path, fake_launcher)
    install = tmp_path / "fluentflow-local"
    install.mkdir()
    monkeypatch.setattr(core, "_install_folder", lambda: install)
    marker = tmp_path / "fluentflow-local.fluentflow-updating"
    marker.write_text("FluentFlow Local is being updated; do not start the service.\n", encoding="utf-8")
    when = time.time() - age_minutes * 60
    os.utime(marker, (when, when))

    result = core._agent_request("GET", "/agent/v1/status", api_base=API)

    if refused:
        assert result["ok"] is False and result["code"] == "updating"
        assert str(marker) in result["error"] and "再调一次" in result["error"]
        assert starts == [], "nothing is started from a tree that is being updated"
    else:
        assert result == {"ok": True}
        assert starts == [1], "an hour on, the marker is the one a killed run left: start"


def test_the_marker_of_a_one_folder_install_sits_beside_its_folder(monkeypatch, tmp_path):
    """A Windows one-folder install keeps the code in <folder>\\app; the marker is
    beside that folder, not inside app (release_check.install_folder)."""
    from backend.core import release_check

    folder = tmp_path / "FluentFlow"
    code = folder / "app"
    code.mkdir(parents=True)
    (folder / ".fluentflow-home").write_text("", encoding="utf-8")
    monkeypatch.setattr(release_check, "PROJECT_ROOT", code)
    marker = tmp_path / "FluentFlow.fluentflow-updating"
    marker.write_text("being updated\n", encoding="utf-8")

    assert core._updating_marker() == marker


@posix_shell
@pytest.mark.parametrize(
    "env_value,env_file,expected",
    [
        ("8123", None, 8123),
        ("", "FLUENTFLOW_LOCAL_PORT=8124\n", 8124),
        ("", None, 8000),
        ("not-a-port", "FLUENTFLOW_LOCAL_PORT=8124\n", 8000),
        (" 8123", None, 8000),
    ],
)
def test_the_mac_launcher_and_the_client_compute_the_same_port(tmp_path, monkeypatch, env_value, env_file, expected):
    """Both sides read the same environment and .env and must land on one port:
    otherwise the client refuses to start (or starts a second service)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    if env_file:
        (repo / ".env").write_text(env_file, encoding="utf-8")
    text = LAUNCHER.read_text(encoding="utf-8")
    script = tmp_path / "port.sh"
    script.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        f"REPO={repo}\n" + _function(text, "env_port") + _function(text, "configured_port") + "configured_port\n",
        encoding="utf-8",
    )
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    if env_value:
        env["FLUENTFLOW_LOCAL_PORT"] = env_value
    done = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env, timeout=30)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == str(expected), done.stderr

    monkeypatch.setattr(core, "PROJECT_ROOT", repo)
    if env_value:
        monkeypatch.setenv("FLUENTFLOW_LOCAL_PORT", env_value)
    else:
        monkeypatch.delenv("FLUENTFLOW_LOCAL_PORT", raising=False)
    assert core._configured_port() == expected


def test_a_different_configured_port_is_not_started_over(monkeypatch, tmp_path):
    """The user moved the service to another port in .env but the MCP
    registration still says 8000: starting the launcher would put a second
    service on the same data, and each would re-queue the other's tasks."""

    def fake_launcher(log_path):
        raise AssertionError("never start a second service beside the one .env points at")

    _answers(monkeypatch, _refused())
    starts = _allow_autostart(monkeypatch, tmp_path, fake_launcher)
    monkeypatch.setenv("FLUENTFLOW_LOCAL_PORT", "8001")

    result = core._agent_request("GET", "/agent/v1/status", api_base="http://127.0.0.1:8000")

    assert result["ok"] is False
    assert starts == []
    assert "8001" in result["error"] and "8000" in result["error"]
    assert "FLUENTFLOW_API_BASE" in result["error"] and "FLUENTFLOW_LOCAL_PORT" in result["error"]


def test_a_service_that_does_not_come_up_says_it_tried_the_log_and_the_last_lines(monkeypatch, tmp_path):
    def fake_launcher(log_path):
        log_path.write_text(
            "== FluentFlow Local 启动前检查 ==\n✗ 找不到 Python 虚拟环境。\n", encoding="utf-8"
        )
        return _Process(code=1)

    _answers(monkeypatch, _refused())
    _allow_autostart(monkeypatch, tmp_path, fake_launcher)

    result = core._agent_request("GET", "/agent/v1/status", api_base=API)

    assert result["ok"] is False
    assert "试着在后台启动" in result["error"]
    assert str(tmp_path / "mcp-autostart.log") in result["error"]
    assert "找不到 Python 虚拟环境" in result["error"]


def test_a_service_that_stays_silent_until_the_deadline_says_so(monkeypatch, tmp_path):
    def fake_launcher(log_path):
        return _Process()  # still running, never answers

    _answers(monkeypatch, _refused())
    _allow_autostart(monkeypatch, tmp_path, fake_launcher)
    monkeypatch.setattr(core, "AUTOSTART_SECONDS", 0.05)

    result = core._agent_request("GET", "/agent/v1/status", api_base=API)

    assert result["ok"] is False
    assert "秒服务仍没有应答" in result["error"]


def test_a_launcher_that_cannot_run_at_all_says_so(monkeypatch, tmp_path):
    def fake_launcher(log_path):
        raise OSError("Permission denied")

    _answers(monkeypatch, _refused())
    _allow_autostart(monkeypatch, tmp_path, fake_launcher)

    result = core._agent_request("GET", "/agent/v1/status", api_base=API)

    assert result["ok"] is False
    assert "启动器没能跑起来" in result["error"] and "Permission denied" in result["error"]


def test_a_lost_connection_is_not_sent_again(monkeypatch, tmp_path):
    """The submit went out and the connection dropped: it may have made a task,
    so it is not repeated — the agent is told to look before retrying."""
    ready = threading.Event()
    posts: list[str] = []

    def fake_api_request(method, api_base, path, **kwargs):
        if path == "/health":
            if ready.is_set():
                return LOCAL_HEALTH
            raise _refused()
        posts.append(path)
        raise _reset()

    monkeypatch.setattr(core, "api_request", fake_api_request)

    def fake_launcher(log_path):
        starts.append(1)
        ready.set()
        return _Process()

    starts = _allow_autostart(monkeypatch, tmp_path, fake_launcher)

    result = core._agent_request("POST", "/agent/v1/tasks", payload={"path": "/x.mov"}, api_base=API)

    assert starts == [1], "the service was still started"
    assert posts == ["/agent/v1/tasks"], "the submit was sent once, not twice"
    assert result["ok"] is False and result["code"] == "result_unknown"
    assert "list_tasks" in result["error"]


def test_a_refused_connection_is_sent_again(monkeypatch, tmp_path):
    """Connection refused means no byte of the call arrived: repeating it cannot
    make a second task, and that is the ordinary "service was not running" case."""
    ready = threading.Event()
    posts: list[str] = []

    def fake_api_request(method, api_base, path, **kwargs):
        if path == "/health":
            if ready.is_set():
                return LOCAL_HEALTH
            raise _refused()
        if not ready.is_set():
            raise _refused()
        posts.append(path)
        return {"ok": True, "task_id": "t1"}

    monkeypatch.setattr(core, "api_request", fake_api_request)

    def fake_launcher(log_path):
        ready.set()
        return _Process()

    _allow_autostart(monkeypatch, tmp_path, fake_launcher)

    result = core._agent_request("POST", "/agent/v1/tasks", payload={"path": "/x.mov"}, api_base=API)

    assert result == {"ok": True, "task_id": "t1"}
    assert posts == ["/agent/v1/tasks"]


def _function(text: str, name: str) -> str:
    """One top-level bash function, as the launcher itself runs it."""
    start = text.index(f"\n{name}() {{") + 1
    end = text.index("\n}\n", start) + 3
    return text[start:end]


def _run_open_app(tmp_path: Path, page: bool) -> str:
    """Run the launcher's own ``open_app`` with a stub ``open`` that records."""
    bin_dir = tmp_path / ("bin-page" if page else "bin-quiet")
    bin_dir.mkdir()
    opened = tmp_path / ("opened-page" if page else "opened-quiet")
    (bin_dir / "open").write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$@" >> "{opened}"\n', encoding="utf-8")
    (bin_dir / "pgrep").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    script = tmp_path / ("page.sh" if page else "quiet.sh")
    script.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        "APP_URL=http://127.0.0.1:8123/\nPORT=8123\n" + _function(LAUNCHER.read_text(encoding="utf-8"), "open_app") + "\nopen_app\n",
        encoding="utf-8",
    )
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    if not page:
        env["FLUENTFLOW_OPEN_PAGE"] = "0"
    done = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env, timeout=30)
    assert done.returncode == 0, done.stderr
    return opened.read_text(encoding="utf-8") if opened.exists() else ""


@posix_shell
def test_the_background_start_does_not_open_the_page_and_the_desktop_app_does(tmp_path):
    assert _run_open_app(tmp_path, page=False) == ""
    assert "http://127.0.0.1:8123/" in _run_open_app(tmp_path, page=True)


def test_the_windows_launcher_opens_the_page_only_through_its_switch():
    text = (ROOT / "launchers/windows/FluentFlowLocal.ps1").read_text(encoding="utf-8-sig")
    assert "function Open-AppPage" in text
    assert '$env:FLUENTFLOW_OPEN_PAGE -ne "0"' in text
    # Every page open goes through that one place, so the switch cannot be
    # missed as the launcher grows.
    assert text.count("Start-Process $AppUrl") == 1
    guarded = text[text.index("function Open-AppPage"):]
    assert "Start-Process $AppUrl" in guarded[: guarded.index("\n}\n")]


def test_the_launcher_does_not_wait_for_a_key_when_nobody_is_there():
    """The background start has no terminal; a "press Enter" would only be a
    line in the log (the Mac launcher) or a failed read (Windows)."""
    mac = (ROOT / "launchers/macos/FluentFlowLocal.command").read_text(encoding="utf-8")
    fail = _function(mac, "fail")
    assert fail.index("[[ -t 0 ]]") < fail.index("read -r -p")
    windows = (ROOT / "launchers/windows/FluentFlowLocal.ps1").read_text(encoding="utf-8-sig")
    start = windows.index("function Fail")
    fail_block = windows[start: windows.index("\n}\n", start)]
    guard = fail_block.index("IsInputRedirected")
    assert fail_block.index("Read-Host", guard) > guard


def test_the_tool_result_is_still_json_safe(monkeypatch, tmp_path):
    """The failure text carries launcher output; it must reach the agent as a
    normal tool result, not break the stdio answer."""
    def fake_launcher(log_path):
        log_path.write_text("✗ 没有终端\n", encoding="utf-8")
        return _Process(code=1)

    _answers(monkeypatch, _refused())
    _allow_autostart(monkeypatch, tmp_path, fake_launcher)

    answer = core._agent_request("GET", "/agent/v1/status", api_base=API)
    json.dumps(answer, ensure_ascii=False)


def test_the_hint_matches_what_the_tools_do(monkeypatch):
    monkeypatch.delenv("FLUENTFLOW_MCP_AUTOSTART", raising=False)
    monkeypatch.setattr(core, "_launcher_path", lambda: LAUNCHER)
    assert "自己在后台启动" in core._start_hint()
    monkeypatch.setenv("FLUENTFLOW_MCP_AUTOSTART", "0")
    assert core._start_hint() == core._START_MANUALLY


def test_the_ported_switch_is_in_this_machine_s_launcher():
    """The switch is only useful where the MCP server starts a launcher; a
    silent rename of the file would turn autostart off without saying so."""
    assert LAUNCHER.is_file()
    assert re.search(r'FLUENTFLOW_OPEN_PAGE', LAUNCHER.read_text(encoding="utf-8"))


# ── a real launcher and a real service ───────────────────────────────────────
#
# Everything above replaces the launcher and the client, which proves the
# decisions but not that a spawned launcher is seen at all: this one runs a real
# script through ``_run_launcher`` against a real socket.

FAKE_SERVICE = '''
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1])
PID_FILE = sys.argv[2]
LOCAL = {"status": "ok", "edition": "local", "runtime": {"execution": "local"}}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = LOCAL if self.path == "/health" else {"ok": True}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


with open(PID_FILE, "w") as handle:
    handle.write(str(os.getpid()))
HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
'''


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _stop(pid_file: Path) -> None:
    """Stop the fake service, waiting for the pid file it may still be writing."""
    pid = 0
    for _ in range(50):
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            break
        except (OSError, ValueError):
            time.sleep(0.1)
    if not pid:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except OSError:
            return
        for _ in range(30):
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(0.1)


@posix_shell
@pytest.mark.parametrize("launcher_returns", [True, False], ids=["returns-after-starting", "keeps-running"])
def test_a_real_launcher_starting_a_real_service_is_waited_for(monkeypatch, tmp_path, launcher_returns):
    port = _free_port()
    server = tmp_path / "service.py"
    server.write_text(FAKE_SERVICE, encoding="utf-8")
    pid_file = tmp_path / "service.pid"
    launcher = tmp_path / "fake-launcher.sh"
    start = f'"{sys.executable}" "{server}" {port} "{pid_file}"'
    if launcher_returns:
        # Like a launcher that starts the service in the background and exits.
        launcher.write_text(
            "#!/bin/bash\nnohup " + start + f' >>"{tmp_path / "service.log"}" 2>&1 </dev/null &\nexit 0\n',
            encoding="utf-8",
        )
    else:
        # Like the Mac launcher, which becomes the service.
        launcher.write_text("#!/bin/bash\nexec " + start + "\n", encoding="utf-8")
    launcher.chmod(0o755)

    monkeypatch.setenv("FLUENTFLOW_MCP_AUTOSTART", "1")
    monkeypatch.setenv("FLUENTFLOW_LOCAL_PORT", str(port))
    monkeypatch.setattr(core, "_launcher_path", lambda: launcher)
    monkeypatch.setattr(core, "_log_dir", lambda: tmp_path)
    monkeypatch.setattr(core, "AUTOSTART_POLL_SECONDS", 0.05)
    try:
        result = core._agent_request("GET", "/agent/v1/status", api_base=f"http://127.0.0.1:{port}")
        assert result == {"ok": True}
        assert pid_file.is_file(), "the launcher's service was reached and is still there"
    finally:
        _stop(pid_file)
