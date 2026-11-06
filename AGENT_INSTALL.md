# Installing FluentFlow Local for a user (instructions for agents)

You are an agent (Claude Code, Codex, or similar) and the user asked you to install
FluentFlow Local: a local app that turns videos and recordings into transcripts and
notes. Do the whole install yourself. Nothing here needs the user's password, a click
in a system dialog, or an account; the cases that need a person are listed at the
end, and only those go to the user.

Tell the user once, before you start, that the install downloads about 8 GB and takes
15 to 30 minutes, then work without further questions.

## Run one command

macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/Userneima/fluentflow-local/main/install.sh | bash
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/Userneima/fluentflow-local/main/install.ps1 | iex"
```

Run it in the background and wait; it prints a `== step ==` line before each step.
It downloads the latest release into `~/fluentflow-local` (Windows:
`%USERPROFILE%\fluentflow-local`), installs whatever is missing into the app's own data
folder, builds the interface, downloads the transcription model, generates the agent
access token into `.env`, starts the app at `http://127.0.0.1:8000`, registers the MCP
server `fluentflow-local` with Claude Code and Codex when their command line tools are
on this machine, and checks that server. Failed downloads are retried from mirrors in
China automatically.

Success is exit code 0 and a last line starting with `✓` (Windows: "FluentFlow Local
is installed"). On failure the last lines say what went wrong and what to do; fix that
and run the same command again. It keeps what is already installed.

When neither `claude` nor `codex` is found, the script prints the command to register
as an MCP server; register it in whatever tool you are. The MCP server reads its access
token from the repository `.env`, so the registration carries no secret.

On Codex, one tool call is given up after 300 seconds. Tell the user, and add it only if
they agree, that one line under `[mcp_servers.fluentflow-local]` in `~/.codex/config.toml`
lets FluentFlow's waits run longer, so a long recording costs fewer turns of their Codex
allowance: `tool_timeout_sec = 900` (measured 2026-11-05: a single call then held 420
seconds without an extra turn). It changes nothing else.

## Tell the user how to use it

Say this, in the user's language:

- FluentFlow is installed and running; later it opens from the Desktop icon.
- To get a note, give you (the agent) a video file or a Douyin, Bilibili or YouTube link
  in a new conversation, or drop it into the app's page.
- You write the notes yourself, so no API key is needed. Entering a model key in the
  app's settings is optional; it is a fallback when you cannot complete the work or the user delegates it, and supports submissions made on the page.

The MCP tools appear in the user's next conversation with you. When asked for a note
then: submit with `submit_local_media` or `submit_video_link` using the default
`intelligence="agent"`; hand `agent_cut` to a subagent with a fresh context, which uses
`get_agent_cut_material`, `save_agent_cut` and `save_agent_cut_report`; when `wait_task` returns `agent_note` ready, the cut is
rendering and the note can start. Fetch `get_agent_note_material`, write and check the note
yourself (in a subagent with a fresh context), and `save_agent_note` with its `base_version`.
A note left half written (`get_task`'s `note_draft`) goes to a new subagent for the parts left.
Between `wait_task` calls say nothing; call it as a tool call of its own (not from a
script), and each call holds up to four minutes. On Codex spawn subagents with
`fork_turns="none"`, and start the cut's subagent only once `agent_cut` is waiting.
`skip_summary=true` leaves writing to you; it does not complete the user's request.
Configured keys do not change these defaults. Only delegate with `intelligence="fluentflow"`
when you cannot do it, failed, or the user asks; explain which configured model pays.
`save_agent_note` never calls a checker unless you explicitly set `model_check=true`.
A recording is fetched and written in one go by
default; when it is too long for one go, fetch it with `small_parts=true` and save each
part as you write it. When a note lost content or the
user wants an earlier version, `过程文件/修改记录.md` beside the note says what each change
changed, `list_note_versions` shows every kept version with what
replaced it, `read_note_version` reads one, and `restore_note_version` (after the user
agrees) puts it back everywhere the note is kept. In the delegated background flow FluentFlow corrects
misheard names and terms in the transcript from the subtitles in the picture and from the
series' word list (`FluentFlow 校正文档.md`); `list_transcript_corrections` (source
`subtitles` or `glossary`) shows each change, `revert_transcript_corrections` undoes it after the
user agrees, and `run_glossary_correction` runs the word-list step again on a finished task.
On agent submissions those corrections and the translation of English subtitles are the agent's:
once the task is done, `wait_task` names them, and `get_agent_correction_material` /
`save_agent_correction` hand over one page at a time and take the answer back, recorded and undone
like the background's.
When the user wants a review note (复习笔记, a second note reorganised by topic for learning
it again), `write_review_note` hands the agent the material to write it from the md the user
corrected and saves it beside the follow-along note, and `revise_review_note` changes it at their
word without starting over; with `writer="fluentflow"` FluentFlow's own model writes instead.
`get_cut_report` shows what the cut removed and kept; `flip_cut_decision` (after the user
agrees) turns one decision around and renders the cut again. `stop_task_step` stops the
step a task is on, `cancel_task` cancels one still transcribing, and `resume_task` carries
a stopped task, or a step that failed, on from that step on the same task.

## Personal writing preferences

Follow `agent/skills/fluentflow/SKILL.md` for note feedback and preferences.
Propose only preferences supported by the user's feedback or edits and confirm
what future notes should do; an explicit "always do this" already counts.
Check one affected excerpt for wording or formatting, representative excerpts
for content selection or structure. Read `get_note_skill` and merge into that
version; `apply_note_rule` requires its `base_version`, recorded feedback and
honest verification evidence. A conflict requires rereading and merging.
Settings shows the current writing rules, their change history and undo; it
no longer extracts candidates or runs mandatory side-by-side comparisons.

For section times or playback following, the current agent does the alignment too:
`get_agent_alignment_material` returns the tagged note and timed transcript. Read every
page with `next_offset` and the same `material_version`, judge the primary span and
other sources for each tagged section, then `save_agent_alignment` with that version,
the section rows and your actual model. No Claude installation, login or model key is
needed. Stale material must be read again. Only the table changes, not the note or files.
Agent-written notes and tasks handed to an agent do not trigger background Claude alignment.

## Only these need the user

- **Not enough disk space** (a step fails with "No space left on device"; the install
  needs about 8 GB free): ask the user to free space, then run the command again.
- **No network access to GitHub, PyPI, npm and their mirrors**: ask the user to connect
  a network or a proxy, then run the command again.
- **Windows without winget**: ask the user to install "App Installer" from the
  Microsoft Store.
- **Videos that need a login** (some Bilibili and YouTube links): the user chooses a
  browser in the app's settings whose login FluentFlow may reuse.
