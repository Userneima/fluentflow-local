"""An expired Claude login is noticed before a recording is transcribed.

Requirements, from what the user should experience:

- When this machine's Claude is logged out, the start page says so, and a task
  submitted anyway still gets the text note from the configured model instead
  of ending with no note at all.
- When it is logged in, nothing changes.
- When the login cannot be read (old CLI, timeout, odd output), nothing is
  refused: the note is attempted as before and reports its own failure.
- Checking does not freeze the server: on the event loop it never waits for the
  program.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import time
from pathlib import Path

import pytest

from backend.core import claude_code_note, local_intake_flow, visual_note_channel

pytestmark = pytest.mark.real_login_check


def _fake_claude(tmp_path: Path, body: str, *, delay: float = 0.0) -> Path:
    script = tmp_path / "claude"
    script.write_text(
        "#!/bin/sh\n"
        f"sleep {delay}\n"
        f"cat <<'OUT'\n{body}\nOUT\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@pytest.fixture(autouse=True)
def _subscription(monkeypatch):
    claude_code_note.reset_login_cache()
    monkeypatch.setenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", "subscription")
    yield
    claude_code_note.reset_login_cache()


def _use(monkeypatch, path: Path) -> None:
    monkeypatch.setenv("FLUENTFLOW_CLAUDE_CLI", str(path))


def test_a_logged_out_machine_cannot_write_the_frame_note_and_says_why(tmp_path, monkeypatch):
    _use(monkeypatch, _fake_claude(tmp_path, json.dumps({"loggedIn": False, "authMethod": "none"})))

    channel = visual_note_channel.resolve_channel(None)

    assert not channel.available
    assert "登录" in channel.unavailable_reason
    assert "claude" in channel.unavailable_reason


def test_a_logged_out_machine_falls_back_to_the_text_note(tmp_path, monkeypatch):
    _use(monkeypatch, _fake_claude(tmp_path, json.dumps({"loggedIn": False})))
    monkeypatch.delenv("FLUENTFLOW_LOCAL_AUTO_NOTE", raising=False)

    # False here is what makes the pipeline write its own text note.
    assert local_intake_flow.auto_note_will_run() is False


def test_a_logged_in_machine_is_unchanged(tmp_path, monkeypatch):
    _use(monkeypatch, _fake_claude(tmp_path, json.dumps({"loggedIn": True, "authMethod": "claude.ai"})))

    assert visual_note_channel.resolve_channel(None).available


@pytest.mark.parametrize("body", ["not json at all", json.dumps({"something": "else"}), ""])
def test_an_unreadable_answer_refuses_nothing(tmp_path, monkeypatch, body):
    _use(monkeypatch, _fake_claude(tmp_path, body))

    assert claude_code_note.login_state() is None
    assert visual_note_channel.resolve_channel(None).available


def test_a_progress_line_before_the_json_is_tolerated(tmp_path, monkeypatch):
    _use(monkeypatch, _fake_claude(tmp_path, "checking...\n" + json.dumps({"loggedIn": False})))

    assert claude_code_note.login_state() is False


def test_a_hanging_program_is_given_up_on(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_code_note, "LOGIN_CHECK_TIMEOUT_SECONDS", 0.5)
    _use(monkeypatch, _fake_claude(tmp_path, json.dumps({"loggedIn": True}), delay=5))

    started = time.monotonic()
    assert claude_code_note.login_state() is None
    assert time.monotonic() - started < 3


def test_the_answer_is_reused_for_a_minute(tmp_path, monkeypatch):
    calls = tmp_path / "calls"
    script = tmp_path / "claude"
    script.write_text(f"#!/bin/sh\necho x >> {calls}\necho '{json.dumps({'loggedIn': True})}'\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    _use(monkeypatch, script)

    assert claude_code_note.login_state(now=1000.0) is True
    assert claude_code_note.login_state(now=1030.0) is True
    assert len(calls.read_text().splitlines()) == 1
    claude_code_note.login_state(now=1000.0 + claude_code_note.LOGIN_CHECK_TTL_SECONDS + 1)
    assert len(calls.read_text().splitlines()) == 2


def test_asking_on_the_event_loop_never_waits_for_the_program(tmp_path, monkeypatch):
    _use(monkeypatch, _fake_claude(tmp_path, json.dumps({"loggedIn": False}), delay=2))

    async def ask() -> tuple[bool | None, float]:
        started = time.monotonic()
        answer = claude_code_note.login_state()
        return answer, time.monotonic() - started

    answer, waited = asyncio.run(ask())
    assert answer is None, "nothing known yet, so nothing refused"
    assert waited < 0.5

    for _ in range(60):
        time.sleep(0.1)
        if claude_code_note.login_state() is False:
            break
    assert claude_code_note.login_state() is False, "the background check filled the answer in"


def test_the_start_page_is_told_the_login_expired(tmp_path, monkeypatch):
    from backend.routers import local_system

    _use(monkeypatch, _fake_claude(tmp_path, json.dumps({"loggedIn": False})))
    status = local_system.get_credentials_status()
    assert status["visual_note_available"] is False
    assert status["visual_note_login_expired"] is True


def test_an_api_key_setup_never_mentions_the_login(tmp_path, monkeypatch):
    from backend.routers import local_system

    # Named outright rather than unset: importing the app reloads the
    # developer's .env, which would put a removed value back.
    monkeypatch.setenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", "api_key")
    _use(monkeypatch, _fake_claude(tmp_path, json.dumps({"loggedIn": False})))
    assert local_system.get_credentials_status()["visual_note_login_expired"] is False


def test_the_login_check_does_not_hand_other_keys_to_the_program(tmp_path, monkeypatch):
    seen = tmp_path / "env"
    script = tmp_path / "claude"
    script.write_text(f"#!/bin/sh\nenv > {seen}\necho '{json.dumps({'loggedIn': True})}'\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    _use(monkeypatch, script)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-should-not-leak")

    claude_code_note.login_state()
    assert "sk-should-not-leak" not in seen.read_text()
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-should-not-leak"
