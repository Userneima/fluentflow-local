"""Write the visual note through the Claude Code login this machine already has.

The same job as ``claude_vision``, reached a different way. There, the product
holds an Anthropic API key and calls the Messages API itself. Here it runs the
``claude`` command-line program that is already installed and already signed in,
hands it the transcript and the frame paths, and takes back the note. The user
never creates, pastes, or manages a key, because the credential is the login
they made once in their own terminal and this module never touches it: it is
Claude Code's, it lives in the OS keychain, and nothing here reads, copies, or
forwards it. The only thing this module knows how to do is start the program.

Scope, recorded because it decides whether this file is allowed to exist:
FluentFlow Local is a private tool for the one person who maintains it, running
on their own machine against their own subscription. Anthropic's Agent SDK
documentation says third-party developers may not offer claude.ai login or
subscription rate limits *in their products* without prior approval, so the
moment this is distributed to anyone else, this channel has to go back to a
user-supplied API key. `docs/claude_agent_sdk_deferred_plan.md` holds the
evidence and the alternatives; the boundary is repeated at the top of the
settings the run uses, not left in a document nobody opens.

Two properties are enforced here rather than trusted to the caller:

- The subprocess gets the parent's environment *minus* any Anthropic key. A key
  in the environment silently outranks the subscription login in Claude Code's
  own precedence, and the run would then bill somewhere the user was not told
  about. This channel's entire claim is which credential paid for the note.
- The agent runs with one tool. It reads image files and nothing else: no shell,
  no writing, no network fetches, no MCP servers, no skills or hooks or project
  instructions from this machine. A note-writing errand has no business
  inheriting the maintainer's Claude Code configuration.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from backend.core.claude_vision import (
    FRAME_ATTACH_MAX,
    image_block,
    spread_across,
    MAX_TRANSCRIPT_CHARS,
    transcript_for_request,
    TranscriptPart,
    part_instruction,
    NOTE_SCHEMA,
    SYSTEM_PROMPT,
    ClaudeVisionError,
    FrameInput,
    VisualNoteDraft,
    configured_model,
)

logger = logging.getLogger(__name__)

CHANNEL_NAME = "claude_code_subscription"

# A lecture note is minutes of work, not seconds, and the failure this guards
# against is a hung subprocess holding the job's only slot forever — not a slow
# but healthy run.
#
# Raised from 900s while frames were fetched one per turn, when 40 of them took
# 460 seconds of model time. Attaching them instead collapsed that to two
# requests, so the generous ceiling is no longer load-bearing — it is kept
# because a single request carrying fifty pictures and a long transcript is still
# minutes of work, and the failure this guards against is a hung subprocess
# holding the job's only slot, not a slow but healthy run.
DEFAULT_TIMEOUT_SECONDS = 2400

# Everything the agent is allowed to do. Reading the frames is the errand.
_ALLOWED_TOOLS = "Read"
_DISALLOWED_TOOLS = "Bash,Write,Edit,NotebookEdit,WebFetch,WebSearch,Task"

# Claude Code resolves these ahead of the subscription login, so leaving one set
# would move the bill without telling anyone. Dropped for this subprocess only.
_CREDENTIAL_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")

_LOGIN_MARKERS = ("not logged in", "login expired", "please run /login", "oauth token has expired")
_LIMIT_MARKERS = ("usage limit", "rate limit", "too many requests", "quota")

_LOGIN_MESSAGE = (
    "这台机器上的 Claude 还没有登录，或者登录已经过期。"
    "打开终端运行 `claude` 并按提示用你的 Claude 账号登录一次，然后回到这里重试。"
    "不需要申请或填写 API Key。"
)


def cli_path() -> str | None:
    """Where the ``claude`` program is, or ``None`` if it is not installed.

    ``FLUENTFLOW_CLAUDE_CLI`` wins so a launcher that starts the service outside
    a login shell can name the path outright. That matters more than it looks:
    the login this channel depends on resolves per process, and a service
    started with a stripped environment has been observed reporting "not logged
    in" for a machine that is signed in perfectly well.
    """
    override = (os.environ.get("FLUENTFLOW_CLAUDE_CLI") or "").strip()
    if override:
        return override if Path(override).exists() else None
    return shutil.which("claude")


def cli_available() -> bool:
    return cli_path() is not None


def unavailable_reason() -> str | None:
    """Why this channel cannot run, phrased as something to go and do."""
    if not cli_available():
        return (
            "这台机器上找不到 Claude Code 命令行程序，没法用本机的 Claude 订阅写笔记。"
            "装好 Claude Code 并在终端运行一次 `claude` 登录后重启本地服务即可。"
        )
    return None


def timeout_seconds() -> int:
    raw = (os.environ.get("FLUENTFLOW_VISUAL_NOTE_TIMEOUT") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


def _clock(seconds: float | None) -> str:
    if seconds is None:
        return "未知时间"
    total = max(int(seconds), 0)
    return f"{total // 60:02d}:{total % 60:02d}"


def _index_row(frame: FrameInput, *, path: bool = True) -> str:
    """One line of the index: when, what file, and what is new on it.

    The text is the row's substance when a machine could read it — specifically
    the text no earlier frame carried, because whole text is dominated by the
    same sidebar on every frame of a screen recording and would rank furniture
    as highly as content. It is a rough read and labelled as one.

    Where nothing could be read — no OCR on this machine, or a picture that
    genuinely has no text, like a diagram or a demonstration — the row falls back
    to the two proxies it used before: ``字量`` is edge contrast, roughly how much
    is written here; ``变化`` is how far this picture is from the last one kept,
    which separates one more bullet on the same slide from a new subject.
    """
    # The whole path on every row, not a directory stated once and joined a
    # hundred times. It costs about 25 tokens a row — under two frames' worth
    # across a full index — and it removes the failure where the agent builds a
    # path that does not exist and reports the picture as unreadable.
    parts = [f"{_clock(frame.timestamp_seconds)}", str(frame.path) if path else frame.filename]
    if frame.change_percent is not None:
        parts.append(f"变化 {frame.change_percent:.0f}%")
    if frame.source in {"floor", "cue"}:
        parts.append("采样")
    if frame.new_text:
        parts.append(f"新增文字：{frame.new_text}")
    elif frame.new_text == "":
        parts.append("没读到新文字（可能是图表、演示或与上一张同文）")
    elif frame.detail is not None:
        parts.append(f"字量 {frame.detail:.0f}")
    return "- " + "　".join(parts)


def build_prompt(
    transcript: str,
    frames: list[FrameInput],
    *,
    open_budget: int = FRAME_ATTACH_MAX,
    selected: bool = False,
) -> str:
    """The errand, as one message: which pictures to open, and the note to write.

    ``selected`` says a first pass already chose these from the full index, which
    changes what this message is. Then it is a short list to open and write from,
    with no index in it at all — and keeping the index out is the entire point,
    because it was in context for every turn of the loop that opened the frames.

    Without it — a single-frame recording, or a selection pass that failed — this
    falls back to offering the whole index here and letting the model choose while
    it reads. That path works and is what the two-pass split replaced; it is kept
    because a broken selection should cost the note its cheapness, not its
    pictures.
    """
    lines = [f"以下是这段录像的转录文字（带时间点）：\n\n{transcript}\n"]
    if not frames:
        lines.append("这次没有可用的截图，只能根据转录文字写笔记，请在 basis_note 里说明。")
        return "\n".join(lines)
    if selected:
        lines.append(
            f"下面是你刚才从整份清单里挑出的 {len(frames)} 张截图，按时间先后排列。"
            f"请用 Read 把它们打开看过再动笔。"
            f"如果看完发现某一张其实没有可用信息，不引用它就行，不必勉强。\n"
        )
    else:
        budget = max(1, min(open_budget, len(frames)))
        lines.append(
            f"下面是从同一段录像里抽出的全部 {len(frames)} 张截图，按时间先后排列，已经去过重。"
            f"它们都在磁盘上，用 Read 工具按路径打开。\n\n"
            f"**这次最多打开 {budget} 张，由你决定打开哪些。**没必要为了用满而打开——"
            f"打开的每一张都在花钱，打开三张就够写清楚的录像，就只打开三张。\n\n"
            f"每行给了时间点、路径，以及本机算出来的几个线索：\n"
            f"- **新增文字**：本机 OCR 粗读这一帧、再去掉前面帧已经出现过的行之后剩下的，"
            f"原样给出、没有裁剪也没有重排。至于哪些字有用哪些是界面装饰，由你判断。\n"
            f"- 「变化」是它跟上一张保留画面的像素差，标「采样」的是没检测到画面变化、"
            f"按时间补看的一张。\n"
            f"- 写着「没读到新文字」的**不代表没内容**：图表、界面演示、实物演示都读不出字。\n"
        )
    lines.extend(_index_row(frame) for frame in frames)
    lines.append(
        "\n看完之后，按系统提示的要求写这份笔记，并把结果作为 JSON 返回。"
        "只引用你**实际打开看过**的截图；引用时用文件名（路径最后那一段），不要用完整路径，"
        "也不要编造没有给你的文件名。"
        "在 basis_note 里说明你实际打开了哪些、有没有哪张打开后发现没用。"
    )
    return "\n".join(lines)


def _command(
    model: str,
    frame_dirs: list[str],
    *,
    system_prompt: str = SYSTEM_PROMPT,
    schema: dict[str, Any] | None = None,
    inline: bool = False,
) -> list[str]:
    binary = cli_path()
    if not binary:  # pragma: no cover - callers check unavailable_reason first
        raise ClaudeVisionError(str(unavailable_reason()), actionable=True)
    command = [
        binary,
        "-p",
        # Streamed rather than a single JSON object, because the plain `json`
        # format reports only the final answer. The errand now lets the model
        # choose which frames to open, and a note claiming to be written from
        # pictures nobody can prove it opened is exactly what this whole path
        # exists to avoid — the tool calls are the proof, and they are only in
        # the stream.
        "--output-format", "stream-json",
        "--verbose",
        "--json-schema", json.dumps(schema if schema is not None else NOTE_SCHEMA, ensure_ascii=False),
        "--system-prompt", system_prompt,
        # With the pictures already in the message there is nothing left to read,
        # so the one tool this errand ever had is taken away too. An agent with no
        # tools cannot wander, and there is no loop for it to wander in.
        "--allowed-tools", "" if inline else _ALLOWED_TOOLS,
        "--disallowed-tools", (_DISALLOWED_TOOLS + ",Read") if inline else _DISALLOWED_TOOLS,
        # No CLAUDE.md, skills, plugins, hooks, commands, or agents from this
        # machine. This errand is not the maintainer's coding session.
        "--safe-mode",
        "--strict-mcp-config",
        "--model", model,
    ]
    if inline:
        # Images cannot travel in a text prompt. This input format takes a whole
        # user message as JSON, content blocks and all, the same shape the
        # Messages API takes — which is how every chosen frame reaches the model
        # in one request instead of one per turn.
        command += ["--input-format", "stream-json"]
    for directory in frame_dirs:
        command += ["--add-dir", directory]
    return command


def _subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    for name in _CREDENTIAL_ENV:
        env.pop(name, None)
    return env


def _friendly_failure(text: str) -> ClaudeVisionError:
    lowered = (text or "").lower()
    if any(marker in lowered for marker in _LOGIN_MARKERS):
        return ClaudeVisionError(_LOGIN_MESSAGE, actionable=True)
    if any(marker in lowered for marker in _LIMIT_MARKERS):
        return ClaudeVisionError(
            "这个 Claude 账号的用量已经到上限了，等额度恢复后再试。笔记没有生成，也没有换用别的模型。",
            actionable=True,
        )
    detail = (text or "").strip().splitlines()
    first = detail[0][:200] if detail else ""
    return ClaudeVisionError(f"调用本机 Claude 失败{('：' + first) if first else ''}")


def _payload_text(payload: dict[str, Any]) -> str:
    """The final answer, whatever shape the CLI put it in."""
    result = payload.get("result")
    if isinstance(result, (dict, list)):
        return json.dumps(result, ensure_ascii=False)
    return str(result or "").strip()


def _parse_note(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        # A fenced block is not what --json-schema should produce, but a note
        # that arrived is worth reading rather than throwing away over a fence.
        body = text.split("\n", 1)[1] if "\n" in text else ""
        text = body.rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ClaudeVisionError("Claude 返回的内容无法解析，笔记未生成") from exc
    if not isinstance(parsed, dict):
        raise ClaudeVisionError("Claude 返回的内容不是笔记，笔记未生成")
    return parsed


def build_inline_message(transcript: str, frames: list[FrameInput]) -> str:
    """The whole errand as one user message, pictures included.

    Measured, which is the only reason this exists: opening frames through the
    file tool costs two model turns each — 40 frames became 84 turns, 460 seconds
    and 2,195k tokens of re-read context, because every turn re-reads everything
    that came before it. Attached, the same 40 pictures are one turn and are sent
    once.

    The trade is stated rather than buried. ``frames_opened`` used to be read out
    of the agent's own Read calls, which was evidence it had looked; there are no
    tool calls now, so "sent" and "seen" collapse back into one thing, the same
    standard the API-key channel has always had. What still measures the claim is
    the citation: which frames the finished note actually points at, counted from
    the note rather than reported by the model.
    """
    content: list[dict[str, Any]] = [
        {"type": "text", "text": f"以下是这段录像的转录文字（带时间点）：\n\n{transcript}\n"}
    ]
    if not frames:
        content.append({
            "type": "text",
            "text": "这次没有可用的截图，只能根据转录文字写笔记，请在 basis_note 里说明。",
        })
    else:
        content.append({
            "type": "text",
            "text": (
                f"下面是从同一段录像里挑出的 {len(frames)} 张截图，按时间先后排列，都附在这条消息里，"
                f"直接看就行，不需要再去读文件。\n\n"
                f"看过之后按系统提示写笔记。引用某一张时用它的文件名，"
                f"不要编造没有给你的文件名。**给了你 {len(frames)} 张不等于要引用 {len(frames)} 张**——"
                f"没有给笔记增加东西的就不要引用。"
                f"在 basis_note 里说明哪几张真的用上了、哪几张看过之后判断没用。"
            ),
        })
        for frame in frames:
            content.append({
                "type": "text",
                "text": f"文件名：{frame.filename}　时间点：{_clock(frame.timestamp_seconds)}",
            })
            content.append(image_block(frame))
    return json.dumps({"type": "user", "message": {"role": "user", "content": content}},
                      ensure_ascii=False)


def _run_cli(
    command: list[str], prompt: str, execute: Callable[..., subprocess.CompletedProcess[str]]
) -> tuple[dict[str, Any], list[str]]:
    """One pass through the CLI: the result event, and the frames it opened."""
    # An empty working directory, so the only files in reach are the frames that
    # were explicitly added. Running this in the project directory would put the
    # user's whole repository one Read away from a note-writing errand.
    with tempfile.TemporaryDirectory(prefix="fluentflow-claude-note-") as scratch:
        try:
            completed = execute(
                command,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout_seconds(),
                cwd=scratch,
                env=_subprocess_env(),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClaudeVisionError(
                f"本机 Claude 超过 {timeout_seconds() // 60} 分钟没有返回，已经中断，笔记未生成。"
            ) from exc
        except OSError as exc:
            raise ClaudeVisionError(f"启动本机 Claude 失败：{exc}") from exc

    stdout = (completed.stdout or "").strip()
    if not stdout:
        raise _friendly_failure(completed.stderr or "本机 Claude 没有任何输出")
    payload, opened = _read_stream(stdout)
    if payload is None:
        if completed.returncode != 0:
            raise _friendly_failure(completed.stderr or stdout)
        raise ClaudeVisionError("本机 Claude 的输出无法解析，笔记未生成")
    if payload.get("is_error") or completed.returncode != 0:
        raise _friendly_failure(_payload_text(payload) or completed.stderr or "")
    return payload, opened


def write_visual_note(
    transcript: str,
    frames: list[FrameInput],
    *,
    api_key: str | None = None,  # noqa: ARG001 - this channel never uses one
    model: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    part: TranscriptPart | None = None,
) -> VisualNoteDraft:
    """Run the local Claude Code login on this errand and return the note.

    Two passes. The first carries the index and no pictures and asks which frames
    are worth opening; the second carries those pictures and no index and writes
    the note. The split is measured, not tidiness: doing both in one agentic loop
    kept a 48k-token index in context for all sixty turns of it, which cost 623k
    cache-creation tokens and $6.88 on a 103-minute lecture, against $2.21 for the
    fixed-twenty version it was meant to beat. The frames were never the expense.

    Who chooses does not change, which is the part worth protecting: the model
    picks, from the text actually on each frame. The alternative on the table was
    a script ranking frames by edge contrast, which has already been caught
    calling a slide deck blank.

    ``api_key`` is accepted and ignored so this is a drop-in replacement for the
    API-key writer; taking it and then not using it is deliberate, because the
    alternative is a caller that thinks it chose a credential and did not.
    ``runner`` is injectable so a test can assert on the assembled command
    without a subscription, a network, or a signed-in machine.
    """
    reason = unavailable_reason()
    if reason:
        raise ClaudeVisionError(reason, actionable=True)
    text = (transcript or "").strip()
    if not text:
        raise ClaudeVisionError("这个任务还没有转录文字，无法生成笔记")
    if part is None:
        fitted = transcript_for_request(text)
        text = fitted.text
        dropped, covered = fitted.chars_dropped, fitted.covered_until
    else:
        # One stretch of a longer recording: it was split to fit, so nothing is
        # dropped and the instruction that keeps a section from being written as
        # a whole note rides along with the prompt.
        text = part.text
        dropped, covered = 0, ""
    chosen_model = (model or "").strip() or configured_model()
    execute = runner if runner is not None else subprocess.run

    # Every frame, attached, in one request. There used to be a pass in front of
    # this one that read a text index of the frames and picked a subset, and it
    # existed for exactly one reason: to avoid sending everything. Once a
    # 103-minute lecture's 119 frames went in a single request and came back with
    # the best note this line of work has produced, that reason was gone — and the
    # pass had cost $1.19 of that run's $3.82 while choosing nothing.
    picked = spread_across(list(frames), FRAME_ATTACH_MAX)
    command = _command(
        chosen_model, [], inline=True,
        system_prompt=SYSTEM_PROMPT + (part_instruction(part) if part is not None else ""),
    )
    payload, _tool_reads = _run_cli(command, build_inline_message(text, picked), execute)

    parsed = _parse_note(_payload_text(payload))
    markdown = str(parsed.get("note_markdown") or "").strip()
    if not markdown:
        raise ClaudeVisionError("Claude 返回的笔记是空的")
    basis = str(parsed.get("basis_note") or "").strip()
    return VisualNoteDraft(
        markdown=markdown,
        basis_note=basis,
        model=str(payload.get("model") or chosen_model),
        frames_sent=picked,
        # Everything attached was in the model's context, provably, because this
        # side of the wire put it there. No tool calls to read it out of.
        frames_opened=[frame.filename for frame in picked],
        transcript_chars=len(text),
        transcript_chars_dropped=dropped,
        transcript_covered_until=covered,
        usage=_usage_from_payload(payload),
    )


def _read_stream(stdout: str) -> tuple[dict[str, Any] | None, list[str]]:
    """The final result object, and every frame the agent actually opened.

    The stream is one JSON object per line. Assistant messages carry the tool
    calls, so the frames that were opened are read out of the agent's own
    behaviour rather than taken from its account of itself — the difference
    between "it says it looked" and "it looked". Names are returned in the order
    they were first opened, so the record shows what it went to first.

    A malformed line is skipped rather than fatal: a note that arrived is worth
    more than a perfect audit of how it arrived, and a missing tool call shows up
    as a frame that was cited but never opened, which is the visible failure this
    is for.
    """
    payload: dict[str, Any] | None = None
    opened: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "result":
            payload = event
            continue
        message = event.get("message")
        blocks = message.get("content") if isinstance(message, dict) else None
        for block in blocks if isinstance(blocks, list) else []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            data = block.get("input")
            target = str((data or {}).get("file_path") or "") if isinstance(data, dict) else ""
            name = Path(target).name
            if name and name not in opened:
                opened.append(name)
    return payload, opened


def _usage_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """What the CLI already told us this note cost.

    This channel is the generous one: it reports dollars, turns and wall clock
    itself, so unlike the key channel there is nothing to derive. Every field is
    optional — a CLI version that stops sending one must not break a note — and
    absent means absent rather than zero, because a recorded 0 would read as
    "this note was free".
    """
    usage: dict[str, Any] = {}
    cost = payload.get("total_cost_usd")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        usage["total_cost_usd"] = float(cost)
    for name in ("num_turns", "duration_ms", "duration_api_ms"):
        value = payload.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            usage[name] = value
    reported = payload.get("usage")
    if isinstance(reported, dict):
        tokens = {
            name: value
            for name, value in reported.items()
            if isinstance(value, int) and not isinstance(value, bool)
        }
        if tokens:
            usage["tokens"] = tokens
    return usage


__all__ = [
    "CHANNEL_NAME",
    "DEFAULT_TIMEOUT_SECONDS",
    "build_prompt",
    "cli_available",
    "cli_path",
    "timeout_seconds",
    "unavailable_reason",
    "write_visual_note",
]
