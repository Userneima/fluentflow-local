"""The MCP tools start the local service themselves when nothing answers.

Requirement (2026-11-26, decision 1.20): the agent flow is the default way to
get a note ("处理视频默认使用用户的 agent 交互"), but the tools only work while
the service runs, and the service used to be started only by opening the
desktop app — which also opened a browser page. So a tool call that finds
nothing answering on this machine starts the launcher in the background, with
the page suppressed, waits for the local edition and carries on with the call.

What must stay true:

- Only a loopback address with nothing on its port is started. An address on
  another machine, or a port something already holds (the hosted edition,
  another program, a FluentFlow whose health check is failing), is left alone:
  nothing is started and nothing is stopped.
- Two calls arriving together start one service, not two.
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
import subprocess
import threading
import time
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
    os.name == "nt" or shutil.which("bash") is None, reason="runs a bash function from the Mac launcher"
)


class _Process:
    """The launcher as this process sees it: still running, or already exited."""

    def __init__(self, code: int | None = None) -> None:
        self.returncode = code

    def poll(self) -> int | None:
        return self.returncode


def _allow_autostart(monkeypatch, tmp_path: Path, launcher) -> list[int]:
    """Turn autostart on for one test, with the launcher itself replaced."""
    monkeypatch.setenv("FLUENTFLOW_MCP_AUTOSTART", "1")
    monkeypatch.setattr(core, "_launcher_path", lambda: LAUNCHER)
    monkeypatch.setattr(core, "_log_dir", lambda: tmp_path)
    monkeypatch.setattr(core, "_port_answers", lambda host, port: False)
    monkeypatch.setattr(core, "AUTOSTART_POLL_SECONDS", 0.01)
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

    def fake_launcher(port, log_path):
        starts.append(port)
        ready.set()
        return _Process()

    _answers(monkeypatch, lambda: LOCAL_HEALTH if ready.is_set() else FluentFlowApiError("Connection refused"))
    starts = _allow_autostart(monkeypatch, tmp_path, fake_launcher)

    result = core._agent_request("POST", "/agent/v1/tasks", payload={"path": "/x.mov"}, api_base=API)

    assert result == {"ok": True}
    assert starts == [8123], "the service was started once, on the port the call addresses"


def test_the_launcher_is_told_the_port_and_not_to_open_the_page():
    env = core._launcher_environment(8123)
    assert env["FLUENTFLOW_OPEN_PAGE"] == "0"
    assert env["FLUENTFLOW_LOCAL_PORT"] == "8123"
    assert env["FLUENTFLOW_REPO"] == str(ROOT)


def test_a_heartbeat_never_starts_the_service(monkeypatch, tmp_path):
    """A beat is this process's own bookkeeping, not a tool call: a service the
    user stopped on purpose must stay stopped."""

    def fake_launcher(port, log_path):
        raise AssertionError("a heartbeat must not start the service")

    _answers(monkeypatch, FluentFlowApiError("Connection refused"))
    _allow_autostart(monkeypatch, tmp_path, fake_launcher)
    monkeypatch.setitem(core._watched, "t1", (API, None))

    core._heartbeat_once()

    assert "t1" in core._watched  # nothing answered: the task stays watched


def test_two_calls_at_once_start_it_only_once(monkeypatch, tmp_path):
    ready = threading.Event()

    def fake_launcher(port, log_path):
        starts.append(port)
        time.sleep(0.2)  # a start in progress, as a real one takes seconds
        ready.set()
        return _Process()

    _answers(monkeypatch, lambda: LOCAL_HEALTH if ready.is_set() else FluentFlowApiError("Connection refused"))
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
    assert starts == [8123], "the second caller waits for the first and finds the service up"


def test_a_backend_that_answers_is_never_started_over(monkeypatch, tmp_path):
    def fake_launcher(port, log_path):
        raise AssertionError("nothing may be started while a backend answers")

    _answers(monkeypatch, HOSTED_HEALTH, result={"ok": True, "task_id": "t1"})
    _allow_autostart(monkeypatch, tmp_path, fake_launcher)

    result = mcp.submit_local_media("/Users/me/meeting.mov", api_base=API)

    assert result["ok"] is False
    assert "FluentFlow Hosted" in result["error"]


def test_a_port_something_else_holds_is_left_alone(monkeypatch, tmp_path):
    def fake_launcher(port, log_path):
        raise AssertionError("a port that answers is not this tool's to take")

    _answers(monkeypatch, FluentFlowApiError("Backend returned non-JSON response: b'<html>'"))
    _allow_autostart(monkeypatch, tmp_path, fake_launcher)
    monkeypatch.setattr(core, "_port_answers", lambda host, port: True)

    result = core._agent_request("GET", "/agent/v1/status", api_base=API)

    assert result["ok"] is False
    assert "启动" in result["error"]


def test_the_switch_off_leaves_starting_to_the_person(monkeypatch, tmp_path):
    def fake_launcher(port, log_path):
        raise AssertionError("FLUENTFLOW_MCP_AUTOSTART=0 means do not start it")

    _answers(monkeypatch, FluentFlowApiError("Connection refused"))
    _allow_autostart(monkeypatch, tmp_path, fake_launcher)
    monkeypatch.setenv("FLUENTFLOW_MCP_AUTOSTART", "0")

    result = core._agent_request("GET", "/agent/v1/status", api_base=API)

    assert result["ok"] is False
    assert "请双击桌面上的「FluentFlow Local」" in result["error"]


def test_an_address_on_another_machine_is_never_started(monkeypatch, tmp_path):
    def fake_launcher(port, log_path):
        raise AssertionError("only this machine's loopback address is started")

    _answers(monkeypatch, FluentFlowApiError("Connection refused"))
    _allow_autostart(monkeypatch, tmp_path, fake_launcher)

    result = core._agent_request("GET", "/agent/v1/status", api_base="http://192.168.1.20:8000")

    assert result["ok"] is False
    assert "192.168.1.20" in result["error"]


def test_a_service_that_does_not_come_up_says_it_tried_the_log_and_the_last_lines(monkeypatch, tmp_path):
    def fake_launcher(port, log_path):
        log_path.write_text(
            "== FluentFlow Local 启动前检查 ==\n✗ 找不到 Python 虚拟环境。\n", encoding="utf-8"
        )
        return _Process(code=1)

    _answers(monkeypatch, FluentFlowApiError("Connection refused"))
    _allow_autostart(monkeypatch, tmp_path, fake_launcher)

    result = core._agent_request("GET", "/agent/v1/status", api_base=API)

    assert result["ok"] is False
    assert "试着在后台启动" in result["error"]
    assert str(tmp_path / "mcp-autostart.log") in result["error"]
    assert "找不到 Python 虚拟环境" in result["error"]


def test_a_service_that_stays_silent_until_the_deadline_says_so(monkeypatch, tmp_path):
    def fake_launcher(port, log_path):
        return _Process()  # still running, never answers

    _answers(monkeypatch, FluentFlowApiError("Connection refused"))
    _allow_autostart(monkeypatch, tmp_path, fake_launcher)
    monkeypatch.setattr(core, "AUTOSTART_SECONDS", 0.05)

    result = core._agent_request("GET", "/agent/v1/status", api_base=API)

    assert result["ok"] is False
    assert "秒服务仍没有应答" in result["error"]


def test_a_launcher_that_cannot_run_at_all_says_so(monkeypatch, tmp_path):
    def fake_launcher(port, log_path):
        raise OSError("Permission denied")

    _answers(monkeypatch, FluentFlowApiError("Connection refused"))
    _allow_autostart(monkeypatch, tmp_path, fake_launcher)

    result = core._agent_request("GET", "/agent/v1/status", api_base=API)

    assert result["ok"] is False
    assert "启动器没能跑起来" in result["error"] and "Permission denied" in result["error"]


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
    def fake_launcher(port, log_path):
        log_path.write_text("✗ 没有终端\n", encoding="utf-8")
        return _Process(code=1)

    _answers(monkeypatch, FluentFlowApiError("Connection refused"))
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
