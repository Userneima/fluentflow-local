"""The visual note written through this machine's own Claude Code login.

The point of this channel is that the user configures nothing: the credential is
the login they already made in their terminal, and FluentFlow never sees it.
That makes two things worth pinning down, because both are invisible from the
outside and both would be easy to break without noticing.

First, what the subprocess is allowed to do. It gets one tool, no shell, no
network fetching, none of this machine's Claude Code configuration, and an empty
working directory with only the frame directory added. A note-writing errand
that could read the user's repository or run commands is a different and much
larger thing than what was agreed to.

Second, which credential pays. An Anthropic API key in the environment outranks
the subscription login inside Claude Code, so leaving one set would move the
bill somewhere the preview did not say. The key is stripped for this subprocess
and the choice of channel is written into the note.

Nothing here runs the real program. The runner is injected, so these tests pass
on a machine with no Claude Code, no login, and no network.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import backend.core.claude_code_note as ccn
import backend.core.visual_note_channel as vnc
from backend.core.claude_vision import ClaudeVisionError, FrameInput


@pytest.fixture(autouse=True)
def a_signed_in_machine(monkeypatch, tmp_path):
    """Pretend the CLI is installed, without depending on the host having it."""
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    monkeypatch.setenv("FLUENTFLOW_CLAUDE_CLI", str(binary))
    monkeypatch.delenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", raising=False)
    monkeypatch.delenv("FLUENTFLOW_VISUAL_NOTE_MODEL", raising=False)
    monkeypatch.delenv("FLUENTFLOW_VISUAL_NOTE_TIMEOUT", raising=False)
    return binary


@pytest.fixture()
def frames(tmp_path):
    directory = tmp_path / "frames"
    directory.mkdir()
    made = []
    for index, seconds in ((1, 12.0), (2, 240.0)):
        name = f"note_{index:04d}.jpg"
        path = directory / name
        path.write_bytes(b"\xff\xd8\xff" + b"jpeg" * 4)
        made.append(FrameInput(filename=name, path=path, timestamp_seconds=seconds))
    return made


def _reply(note="# 笔记\n\n![黑板上写着最小二乘法](note_0001.jpg)\n", basis="画面看了两张",
           opened=("note_0001.jpg",), **extra):
    """One stdout, in the streamed shape the CLI is now asked for.

    The frames the agent opened are tool calls in the stream, not a field in the
    answer, so a fake that only produced the answer would let a regression in
    reading them through unnoticed.
    """
    lines = [json.dumps({"type": "system", "subtype": "init"})]
    for name in opened:
        lines.append(json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": f"/frames/{name}"}},
            ]},
        }))
    lines.append(json.dumps({
        "type": "result",
        "is_error": False,
        "result": json.dumps({"note_markdown": note, "basis_note": basis}, ensure_ascii=False),
        **extra,
    }, ensure_ascii=False))
    return "\n".join(lines)



def _writing_text(prompt: str) -> str:
    """Every text block of the second pass's message, joined.

    The pictures ride in that message now, so its stdin is a JSON user message
    rather than a string. Base64 is skipped: nothing is asserted about it beyond
    the count, which comes from the blocks themselves.
    """
    message = json.loads(prompt)["message"]
    return "\n".join(
        block["text"] for block in message["content"] if block.get("type") == "text"
    )


def _image_count(prompt: str) -> int:
    message = json.loads(prompt)["message"]
    return sum(1 for block in message["content"] if block.get("type") == "image")


def _pick(filenames=("note_0001.jpg", "note_0002.jpg"), reason="都有幻灯片"):
    """The first pass's answer: which frames are worth opening."""
    return "\n".join([
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({
            "type": "result",
            "is_error": False,
            "result": json.dumps({"filenames": list(filenames), "reason": reason}, ensure_ascii=False),
        }, ensure_ascii=False),
    ])


def _recorder(stdout="", returncode=0, stderr=""):
    """Records every CLI call. One pass: the pictures are attached to it."""
    calls: list[dict] = []

    def run(command, **kwargs):
        calls.append({"command": list(command), **kwargs})
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    return run, calls


# ── the note comes back ────────────────────────────────────────────────────

def test_it_returns_the_note_the_local_claude_wrote(frames):
    run, calls = _recorder(_reply())

    draft = ccn.write_visual_note("[00:12] 今天讲最小二乘法", frames, runner=run)

    assert "最小二乘法" in draft.markdown
    assert draft.basis_note == "画面看了两张"
    assert draft.frames_sent == frames
    assert draft.transcript_chars == len("[00:12] 今天讲最小二乘法")
    assert len(calls) == 1, "one request, carrying the transcript and every picture"


def test_the_prompt_names_every_frame_and_carries_the_transcript(frames):
    run, calls = _recorder(_reply())

    ccn.write_visual_note("[00:12] 今天讲最小二乘法", frames, runner=run)

    body = _writing_text(calls[0]["input"])
    assert "今天讲最小二乘法" in body
    assert _image_count(calls[0]["input"]) == len(frames), "the pictures ride in the message"
    for frame in frames:
        assert frame.filename in body, "each attachment is named so a citation can resolve"
    assert "00:12" in body and "04:00" in body


def test_a_fenced_answer_is_still_read(frames):
    """--json-schema should prevent this; a note that arrived is worth keeping."""
    fenced = json.dumps({
        "type": "result",
        "is_error": False,
        "result": "```json\n" + json.dumps({"note_markdown": "# 笔记", "basis_note": "ok"}) + "\n```",
    })
    run, _ = _recorder(fenced)

    draft = ccn.write_visual_note("[00:00] 讲课", frames, runner=run)

    assert draft.markdown == "# 笔记"


# ── what the note cost ─────────────────────────────────────────────────────
#
# The CLI reports the price of the call it just made. Dropping it means paying a
# second time to answer "what does a note cost", which is a question this
# product has to answer about itself.

def test_what_the_note_cost_is_kept(frames):
    run, _ = _recorder(_reply(
        total_cost_usd=0.4137,
        num_turns=29,
        duration_ms=184_512,
        duration_api_ms=170_004,
        usage={"input_tokens": 41_233, "output_tokens": 6_118,
               "cache_read_input_tokens": 1_750_000},
    ))

    draft = ccn.write_visual_note("[00:00] 讲课", frames, runner=run)

    assert draft.usage["total_cost_usd"] == pytest.approx(0.4137)
    assert draft.usage["num_turns"] == 29
    assert draft.usage["duration_ms"] == 184_512
    assert draft.usage["duration_api_ms"] == 170_004
    assert draft.usage["tokens"]["cache_read_input_tokens"] == 1_750_000


def test_a_cli_that_reports_nothing_still_writes_the_note(frames):
    """Absent, not zero — a recorded 0.0 would read as "this note was free"."""
    run, _ = _recorder(_reply())

    draft = ccn.write_visual_note("[00:00] 讲课", frames, runner=run)

    assert draft.markdown
    assert "total_cost_usd" not in draft.usage, "absent, not a recorded zero"
    assert draft.usage == {}, "absent, not a recorded zero"


def test_a_cost_that_is_not_a_number_is_left_out_rather_than_coerced(frames):
    run, _ = _recorder(_reply(total_cost_usd="0.41", num_turns=True, usage=[1, 2]))

    draft = ccn.write_visual_note("[00:00] 讲课", frames, runner=run)

    assert "total_cost_usd" not in draft.usage, "a string price is not a measurement"
    assert "num_turns" not in draft.usage, "and a bool is not a turn count"


# ── what the subprocess may do ─────────────────────────────────────────────

def test_the_agent_gets_no_tools_and_none_of_this_machines_configuration(frames):
    """It used to get exactly one tool, for reading frames off disk. The frames
    are attached now, so it gets none — the strongest version of that rule."""
    run, calls = _recorder(_reply())

    ccn.write_visual_note("[00:00] 讲课", frames, runner=run)

    command = calls[0]["command"]
    assert command[command.index("--allowed-tools") + 1] == ""
    denied = command[command.index("--disallowed-tools") + 1]
    assert "Read" in denied, "nothing left to read"
    for tool in ("Bash", "Write", "Edit", "WebFetch", "WebSearch"):
        assert tool in denied
    assert "--safe-mode" in command, "no CLAUDE.md, skills, plugins, hooks, or agents"
    assert "--strict-mcp-config" in command, "no MCP servers from this machine"


def test_the_writing_pass_reaches_no_directory_at_all(frames):
    """The errand stopped needing the filesystem, so it stopped being given it.

    While the pictures were fetched with a file tool, this run had to hand the
    subprocess the frame directory with --add-dir and rely on an empty working
    directory to keep the rest of the machine out of reach. Attaching the images
    removes the tool, and removing the tool removes the reason to open any
    directory to it: the strongest version of "one tool, nothing else" is none.
    """
    seen: dict = {}

    def run(command, **kwargs):
        # Checked while the process would be running: the scratch directory is
        # removed on the way out, so afterwards there is nothing left to look at.
        cwd = Path(kwargs["cwd"])
        seen.setdefault("cwds", []).append(cwd.is_dir() and not list(cwd.iterdir()))
        seen.setdefault("commands", []).append(list(command))
        return subprocess.CompletedProcess(command, 0, _reply(), "")

    ccn.write_visual_note("[00:00] 讲课", frames, runner=run)

    for command in seen["commands"]:
        assert "--add-dir" not in command, "nothing on this machine is opened to it"
    assert all(seen["cwds"]), "and it still runs in an empty working directory"


def test_an_api_key_in_the_environment_does_not_pay_for_this_run(frames, monkeypatch):
    """Claude Code ranks a key above the login, so it would silently take over."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-be-used")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "should-not-be-used")
    run, calls = _recorder(_reply())

    ccn.write_visual_note("[00:00] 讲课", frames, runner=run)

    env = calls[0]["env"]
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert "PATH" in env, "the rest of the environment is still inherited"


def test_the_credential_is_never_read_or_passed(frames):
    run, calls = _recorder(_reply())

    ccn.write_visual_note("[00:00] 讲课", frames, api_key="sk-ant-caller-supplied", runner=run)

    rendered = " ".join(calls[0]["command"]) + calls[0]["input"]
    assert "sk-ant-caller-supplied" not in rendered
    assert all("sk-ant-caller-supplied" not in json.dumps(call["env"]) for call in calls)


# ── failures a user can act on ─────────────────────────────────────────────

def test_a_logged_out_machine_is_told_to_log_in_not_to_get_a_key(frames):
    run, _ = _recorder(json.dumps({"type": "result", "is_error": True, "result": "Not logged in · Please run /login"}))

    with pytest.raises(ClaudeVisionError) as caught:
        ccn.write_visual_note("[00:00] 讲课", frames, runner=run)

    assert caught.value.actionable is True
    assert "claude" in str(caught.value)
    assert "API Key" in str(caught.value) and "不需要" in str(caught.value)


def test_an_expired_login_says_the_same_thing(frames):
    run, _ = _recorder(json.dumps({"type": "result", "is_error": True, "result": "Login expired · Please run /login"}))

    with pytest.raises(ClaudeVisionError) as caught:
        ccn.write_visual_note("[00:00] 讲课", frames, runner=run)

    assert caught.value.actionable is True


def test_a_used_up_subscription_says_so_and_does_not_substitute(frames):
    run, _ = _recorder(json.dumps({"type": "result", "is_error": True, "result": "Claude usage limit reached"}))

    with pytest.raises(ClaudeVisionError) as caught:
        ccn.write_visual_note("[00:00] 讲课", frames, runner=run)

    assert "上限" in str(caught.value)
    assert "没有换用别的模型" in str(caught.value)


def test_a_hung_run_is_cut_off_rather_than_holding_the_slot(frames):
    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 1))

    with pytest.raises(ClaudeVisionError) as caught:
        ccn.write_visual_note("[00:00] 讲课", frames, runner=run)

    assert "分钟" in str(caught.value)


def test_an_uninstalled_cli_names_the_thing_to_install(frames, monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_CLAUDE_CLI", "/nonexistent/claude")

    assert ccn.cli_available() is False
    with pytest.raises(ClaudeVisionError) as caught:
        ccn.write_visual_note("[00:00] 讲课", frames)

    assert caught.value.actionable is True
    assert "Claude Code" in str(caught.value)


def test_an_empty_note_is_a_failure_not_an_empty_note(frames):
    run, _ = _recorder(_reply(note="   "))

    with pytest.raises(ClaudeVisionError):
        ccn.write_visual_note("[00:00] 讲课", frames, runner=run)


# ── which channel, and who is told ─────────────────────────────────────────

def test_the_key_is_the_default_even_on_a_signed_in_machine(monkeypatch):
    """A build that goes out to other people must not spend a claude.ai login.

    This is the opposite of what this module did while FluentFlow Local was one
    person's private tool, and the reversal is the whole point: the subscription
    channel is still here, but reaching it is now a decision someone makes on
    their own machine rather than something a release does by itself.
    """
    monkeypatch.delenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-configured")

    channel = vnc.resolve_channel("sk-ant-configured")

    assert channel.name == vnc.CHANNEL_ANTHROPIC_KEY
    assert channel.available is True


def test_the_subscription_runs_when_it_is_asked_for_by_name(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", "subscription")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    channel = vnc.resolve_channel(None)

    assert channel.name == vnc.CHANNEL_SUBSCRIPTION
    assert channel.available is True
    assert "订阅" in channel.label
    assert channel.write is ccn.write_visual_note


def test_a_missing_key_never_points_at_the_subscription(monkeypatch):
    """Publicly the product asks for the user's own key; the subscription is opt-in by name only."""
    monkeypatch.delenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    signed_in = vnc.resolve_channel(None)
    assert signed_in.available is False
    assert "subscription" not in (signed_in.unavailable_reason or "")
    assert "Claude Code" not in (signed_in.unavailable_reason or "")


def test_the_key_can_be_forced_even_on_a_signed_in_machine(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", "api_key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-configured")

    channel = vnc.resolve_channel()

    assert channel.name == vnc.CHANNEL_ANTHROPIC_KEY


def test_neither_channel_available_still_refuses(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_CLAUDE_CLI", "/nonexistent/claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    channel = vnc.resolve_channel()

    assert channel.available is False
    assert channel.unavailable_reason


def test_what_reached_the_model_is_what_was_attached(frames):
    """"Sent" and "seen" are one thing again, and that is a real change.

    While the frames were fetched with a file tool, ``frames_opened`` came out of
    the agent's own Read calls — behaviour rather than its account of itself.
    Attaching them removes those calls and the distinction with them: this side of
    the wire put the pictures in the message, so every one of them was in context.
    What still measures the claim is the citation, counted from the finished note.
    """
    run, _ = _recorder(_reply())

    draft = ccn.write_visual_note("[00:12] 今天讲最小二乘法", frames, runner=run)

    assert draft.frames_opened == ["note_0001.jpg", "note_0002.jpg"]
    assert [frame.filename for frame in draft.frames_sent] == ["note_0001.jpg", "note_0002.jpg"]


def test_the_writing_pass_has_no_tools_at_all(frames):
    """With the pictures in the message there is nothing left to read."""
    run, calls = _recorder(_reply())

    ccn.write_visual_note("[00:12] 今天讲最小二乘法", frames, runner=run)

    writing = calls[-1]["command"]
    assert writing[writing.index("--allowed-tools") + 1] == ""
    assert "Read" in writing[writing.index("--disallowed-tools") + 1]
    assert "stream-json" in writing[writing.index("--input-format") + 1]


def test_every_frame_is_offered_not_one_requests_worth(tmp_path):
    """The regression that made the index a no-op for one run.

    This channel opens files for itself, so the twenty-frame limit belongs to the
    other one. Truncating here offered the model a *prefix* — on a 103-minute
    lecture, the opening seventeen minutes — and the note's pictures stopped at
    the first section while its own basis_note said "我从 20 张截图里挑开了 9 张".
    """
    directory = tmp_path / "many"
    directory.mkdir()
    many = []
    for index in range(1, 61):
        path = directory / f"note_{index:04d}.jpg"
        path.write_bytes(b"\xff\xd8\xff" + b"jpeg" * 4)
        many.append(FrameInput(filename=path.name, path=path, timestamp_seconds=index * 60.0))
    run, calls = _recorder(_reply())

    draft = ccn.write_visual_note("[00:12] 今天讲最小二乘法", many, runner=run)

    assert len(draft.frames_sent) == 60
    assert _image_count(calls[0]["input"]) == 60, "both ends of the recording included"
    body = _writing_text(calls[0]["input"])
    assert many[0].filename in body and many[-1].filename in body


