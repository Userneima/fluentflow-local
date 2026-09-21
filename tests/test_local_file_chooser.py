"""Asking the machine's own file dialog which recording to work on.

Why this exists at all: the browser's picker hands the page bytes, a name, a size
and a type — never the folder. So "save the cut file next to the original" has no
answer for an upload, because there is no "next to". The system dialog returns a
real absolute path, which is the one fact the whole feature needs.

The dialog itself was verified by hand on macOS before this was built on (it opens
and blocks until someone chooses). What is pinned here is everything around it,
because each of these is a way a user could be left stuck or confused:

- cancelling is a normal answer, not an error;
- a dialog nobody answers is killed rather than holding a request open;
- a chosen file still goes through the same validation as a typed path, and an
  unusable choice is reported per file instead of failing the whole thing;
- on anything other than macOS it says so and the ordinary upload stays.

Every test injects the runner, so no window opens on anybody's screen.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import backend.core.local_file_chooser as fc


def _runner(*, stdout: str = "", stderr: str = "", returncode: int = 0, record: dict | None = None):
    def run(command, **kwargs):
        if record is not None:
            record["command"] = list(command)
            record["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(args=command, returncode=returncode, stdout=stdout, stderr=stderr)

    return run


@pytest.fixture(autouse=True)
def on_macos(monkeypatch):
    monkeypatch.setattr(fc.sys, "platform", "darwin")
    monkeypatch.setattr(fc, "osascript_path", lambda: "/usr/bin/osascript")
    monkeypatch.delenv(fc.TIMEOUT_ENV, raising=False)


# ── what comes back ────────────────────────────────────────────────────────

def test_a_chosen_file_comes_back_as_an_absolute_path():
    outcome = fc.choose_media_files(runner=_runner(stdout="/Users/me/Movies/talk.mov\n"))

    assert outcome.chose_something is True
    assert outcome.paths == [Path("/Users/me/Movies/talk.mov")]


def test_several_files_come_back_in_order():
    outcome = fc.choose_media_files(
        runner=_runner(stdout="/Users/me/a.mov\n/Users/me/b.mp4\n")
    )

    assert [path.name for path in outcome.paths] == ["a.mov", "b.mp4"]


def test_the_prompt_reaches_the_dialog_and_a_timeout_is_set():
    seen: dict = {}
    fc.choose_media_files(prompt="选择要做笔记的录像", runner=_runner(stdout="/Users/me/a.mov\n", record=seen))

    assert seen["command"][0] == "/usr/bin/osascript"
    assert "选择要做笔记的录像" in seen["command"][2]
    assert seen["timeout"] == fc.DEFAULT_TIMEOUT_SECONDS, "a forgotten dialog must not hold a request"


def test_a_quote_in_the_prompt_cannot_break_the_script():
    seen: dict = {}
    fc.choose_media_files(prompt='选 "这个" 文件', runner=_runner(stdout="/a/b.mov\n", record=seen))

    assert '"这个"' not in seen["command"][2]


# ── the answers that are not files ─────────────────────────────────────────

def test_cancelling_is_a_normal_answer():
    outcome = fc.choose_media_files(
        runner=_runner(returncode=1, stderr="execution error: User canceled. (-128)")
    )

    assert outcome.cancelled is True
    assert outcome.paths == []
    assert outcome.chose_something is False


def test_an_empty_answer_is_treated_as_cancelled():
    assert fc.choose_media_files(runner=_runner(stdout="\n  \n")).cancelled is True


def test_a_dialog_nobody_answers_is_killed_rather_than_held_open():
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout") or 1)

    outcome = fc.choose_media_files(runner=timeout)

    assert outcome.cancelled is True, "the user walked away; the request must not wait forever"


def test_a_real_failure_is_reported_with_its_reason():
    with pytest.raises(fc.FileChooserError, match="返回了错误"):
        fc.choose_media_files(runner=_runner(returncode=1, stderr="something else went wrong"))


def test_the_timeout_is_configurable(monkeypatch):
    monkeypatch.setenv(fc.TIMEOUT_ENV, "45")
    seen: dict = {}
    fc.choose_media_files(runner=_runner(stdout="/a/b.mov\n", record=seen))

    assert seen["timeout"] == 45


def test_a_nonsense_timeout_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv(fc.TIMEOUT_ENV, "not-a-number")
    assert fc.timeout_seconds() == fc.DEFAULT_TIMEOUT_SECONDS


# ── where it is not available ──────────────────────────────────────────────

def test_off_macos_it_says_so_and_leaves_the_upload_alone(monkeypatch):
    monkeypatch.setattr(fc.sys, "platform", "win32")

    reason = fc.unavailable_reason()

    assert reason and "macOS" in reason
    assert "本地上传" in reason, "the ordinary way in has to be named"
    with pytest.raises(fc.FileChooserError):
        fc.choose_media_files(runner=_runner(stdout="/a/b.mov\n"))


def test_without_osascript_it_says_what_is_missing(monkeypatch):
    monkeypatch.setattr(fc, "osascript_path", lambda: None)

    assert "osascript" in (fc.unavailable_reason() or "")


@pytest.mark.skipif(sys.platform != "darwin", reason="the dialog only exists on macOS")
def test_on_this_machine_the_dialog_is_actually_reachable():
    """No window opens: this only asserts the program the dialog needs is there."""
    assert fc.unavailable_reason() is None
    assert fc.osascript_path()
