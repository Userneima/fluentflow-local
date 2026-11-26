# Installing FluentFlow Local for a user (instructions for agents)

You are an agent (Claude Code, Codex, or similar) and the user asked you to install
FluentFlow Local: a local app that turns videos and recordings into transcripts and
notes. Do the whole install yourself. Nothing here needs the user's password, a click
in a system dialog, or an account; the cases that need a person are listed at the
end, and only those go to the user.

Tell the user once, before you start, that the install downloads about 8 GB and takes
15 to 30 minutes. On Windows, ask one question first (below, which drive); then work
without further questions.

## Windows: ask which drive

The whole product (code, Python environment, data, FFmpeg, transcription models)
goes into one folder, `FluentFlow`, on one drive. List the drives and their free space:

```powershell
Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object { "{0} {1} GB free" -f $_.DeviceID, [math]::Floor($_.FreeSpace / 1GB) }
```

When there is more than one drive, ask the user which to install to, recommending the
one other than C: with the most free space (it needs about 10 GB). Pass the answer as
the folder: `D:\FluentFlow` for drive D, `"$env:USERPROFILE\FluentFlow"` for drive C
(PowerShell's spelling, in double quotes so it is expanded). With only
drive C, do not ask. The script never asks anything itself when an agent runs it: without
`FLUENTFLOW_INSTALL_DIR` it picks that same recommended drive and prints where it went.

## Run one command

macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/Userneima/fluentflow-local/main/install.sh | bash
```

Windows (PowerShell), with the folder from the question above:

```powershell
$env:FLUENTFLOW_INSTALL_DIR = "D:\FluentFlow"
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/Userneima/fluentflow-local/main/install.ps1 | iex"
```

When the user is in mainland China, or GitHub does not open, take the same scripts from
npmmirror, published with every release:

```bash
curl -fsSL https://registry.npmmirror.com/fluentflow-local/latest/files/install.sh | bash
```

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://registry.npmmirror.com/fluentflow-local/latest/files/install.ps1 | iex"
```

Run it in the background and wait; it prints a `== step ==` line before each step.
macOS: it downloads the latest release into `~/fluentflow-local` and installs whatever
is missing into the app's data folder. Windows: it puts everything in the chosen folder
(`app` the code, `data` the tasks and downloaded tools, `models` the transcription
models, `downloads` videos fetched from links, `cache` download caches) and writes those
locations into `app\.env`; only Python, when the machine has none, is installed for the
user on drive C. Then it builds the interface, starts the transcription model (1.5 to
3 GB) downloading in the background (the install does not wait for it; a recording
submitted before it is done shows "正在下载转录模型" and carries on once it is), generates the agent access token into `.env`, starts the app at `http://127.0.0.1:8000`,
registers the MCP server `fluentflow-local` with Claude Code and Codex when their command
line tools are on this machine, and checks that server. Every download has a source in
China: the script tries the original host for a few seconds and switches to the mirror
when it is unreachable or slow; downloaded programs are checked against fixed checksums.

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

- FluentFlow is installed; later it opens from the Desktop icon. You do not need them
  to open it before you work: the tools start the service themselves, without opening
  a page.
- To get a note, give you (the agent) a video file or a Douyin, Bilibili or YouTube link
  in a new conversation, or drop it into the app's page.
- You write the notes yourself, so no API key is needed. Entering a model key in the
  app's settings is optional; it is a fallback when you cannot complete the work or the user delegates it, and supports submissions made on the page.

The MCP tools appear in the user's next conversation with you. When asked for a note
then: submit with `submit_local_media` (a recording or a subtitle file, by path) or
`submit_video_link` using the default `intelligence="agent"`; hand `agent_cut` to a subagent with a fresh context, which uses
`get_agent_cut_material`, `save_agent_cut` and `save_agent_cut_report`; when `wait_task` returns `agent_note` ready, the cut is
rendering and the note can start. Fetch `get_agent_note_material`, write and check the note
yourself (in a subagent with a fresh context), and `save_agent_note` with its `base_version`.
A note left half written (`get_task`'s `note_draft`) goes to a new subagent for the parts left.
Between `wait_task` calls say nothing; call it as a tool call of its own (not from a
script), and each call holds up to four minutes. On Codex spawn subagents with
`fork_turns="none"`, and start the cut's subagent only once `agent_cut` is waiting.
`skip_summary=true` leaves writing to you; it does not complete the user's request.
When the user only wants the transcript, submit with `cut=false`: nothing is cut and no cut waits for you.
Configured keys do not change these defaults. Only delegate with `intelligence="fluentflow"`
when you cannot do it, failed, or the user asks; explain which configured model pays.
`save_agent_note` never calls a checker unless you explicitly set `model_check=true`.
A recording is fetched and written in one go by
default; when it is too long for one go, fetch it with `small_parts=true` and save each
part as you write it. When a note lost content or the
user wants an earlier version, `过程文件/修改记录.md` beside the note says what each change
changed, `list_note_versions` shows every kept version with what
replaced it, `read_note_version` reads one, and `restore_note_version` (after the user
agrees) puts it back everywhere the note is kept. To fix a word or a sentence of a saved
note, `replace_note_text` changes that passage alone (named by its exact text, which must
occur once; several passages go in one call as `replacements`, and one that does not fit
changes none); `save_agent_note` is for handing back a note rewritten as a whole. In the delegated background flow FluentFlow corrects
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
To fix or delete passages of a review note without handing it back whole, `replace_note_text`
with `note="review"` changes them in the md, leaving the user's own edits there alone.
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

## Updating

When the user asks you to update FluentFlow, or the page says a new version is out
(`http://127.0.0.1:8000/release-check` answers `"update_available": true`), run the same
install command again (with the same `FLUENTFLOW_INSTALL_DIR` on Windows), from a directory
outside the install. It sees the install, downloads the newest release beside it, keeps
`.env`, the tasks, settings and models, stops the service, swaps the code in, adds what the
new version needs (packages, speaker models, the transcription model) and starts it again.
If any of that fails, it puts the previous version back, starts it again and says so. It
also refuses, changing nothing, when a data, download or model location is inside the code
folder (a relative path counts) or cannot be read for sure; the message names the setting,
and the user decides where to move it. It refuses while tasks
are being processed: wait for them, or set `FLUENTFLOW_UPDATE_FORCE=1` after the user agrees
(interrupted tasks carry on after the restart). A git checkout is not updated by it; there,
`git pull` and run the setup script.

## Starting, checking and stopping the service

The service runs in the background and does not belong to any window or shell, yours
included, so it keeps running after your command ends. To check it, ask
`http://127.0.0.1:8000/health` (a JSON answer with `"edition":"local"` means it is up).

You do not have to start it first. The MCP tools answer only while it runs, and they
start it themselves: when a tool is called and nothing answers on this machine, the
MCP server starts the launcher in the background — no terminal window, no browser page
— waits up to 90 seconds for `/health` to report the local edition, then carries on
with the call. Its output is in `~/Library/Logs/FluentFlow/mcp-autostart.log` on macOS
and `%LOCALAPPDATA%\FluentFlow\Logs\mcp-autostart.log` on Windows; a start that does
not come up says so, with the last lines of that log, so read them rather than guessing.
Two calls arriving together start one service. Nothing is started or stopped when the
port is already answered by something else (the hosted edition, another program): that
error is unchanged. Never kill a process to make room. `FLUENTFLOW_MCP_AUTOSTART=0`
turns the automatic start off, for a user who runs the service themselves.

To start it by hand (for example with the automatic start off), use the launcher with
the page turned off, in the background, and wait for the health answer:

- macOS: `FLUENTFLOW_OPEN_PAGE=0 nohup ~/fluentflow-local/launchers/macos/FluentFlowLocal.command >>~/Library/Logs/FluentFlow/local-launcher.log 2>&1 </dev/null &`
  (the Desktop app also starts it, but it opens a browser page: use it when the user
  wants to see FluentFlow, not when you only need the service)
- Windows: `$env:FLUENTFLOW_OPEN_PAGE = "0"; powershell -NoProfile -ExecutionPolicy Bypass -File <folder>\app\launchers\windows\FluentFlowLocal.ps1`
  (it checks the environment, rebuilds a broken Python environment by itself, starts
  the service in the background and exits)

Do not start uvicorn yourself in a shell of yours: when that shell is closed or
reclaimed, the service goes with it. To stop it on Windows:
`launchers\windows\stop-local.ps1 -Yes`. On macOS, first make sure FluentFlow is what
answers (`/health` shows `"edition":"local"`), then stop only the process listening on its
port (8000, or `FLUENTFLOW_LOCAL_PORT` in `.env`): `kill $(lsof -nP -iTCP:8000 -sTCP:LISTEN -t)`.
Logs: macOS `~/Library/Logs/FluentFlow/local.log`,
Windows `%LOCALAPPDATA%\FluentFlow\Logs\local.log`.

## Uninstalling

When the user asks you to remove FluentFlow, run the uninstaller inside the install
(macOS `~/fluentflow-local`; Windows `<folder>\app`, for example `D:\FluentFlow\app`).
Look first, then delete:

macOS:

```bash
bash ~/fluentflow-local/launchers/macos/uninstall-local.sh --all --dry-run
```

Windows:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\FluentFlow\app\launchers\windows\uninstall-local.ps1 -All -DryRun
```

Run it from a directory outside the install (your home folder, for example): Windows
cannot remove a folder a shell is standing in. The dry run lists what would go, with
sizes, and deletes nothing. Show the user that
list and ask one thing: whether their task data (history, transcripts, notes, media, and
on Windows the videos downloaded from links) goes too. It cannot be recovered. Then run
the same command without `--dry-run` / `-DryRun`, adding `--yes` / `-Yes`:

- keep the data: `--models --yes` (Windows `-Models -Yes`)
- remove everything: `--all --yes` (Windows `-All -Yes`)

It stops the running service, removes the program (for an install from these
instructions, the code folder itself, and the copy a failed update kept beside it), the downloaded FFmpeg and Node.js, the Desktop
icon, the logs and the agent skill, and unregisters `fluentflow-local` from Claude Code
and Codex (only an entry that points at this install). On Windows with `-All`, the
`FluentFlow` folder is gone afterwards. When the data folder also holds files FluentFlow
does not recognise, only FluentFlow's own are removed and the rest are listed, in the dry
run too: show the user that list, and do not delete those files yourself. A copy of the code
folder the user made (`app.old`, for example) is left alone. It leaves Python and anything that was on the
machine before. Tell the user what was removed and what is left (the last lines say).
The MCP tools disappear from your next conversation.

## Only these need the user

- **Not enough disk space** (a step fails with "No space left on device"; the install
  needs about 8 GB free): ask the user to free space, then run the command again.
- **No network access to GitHub, PyPI, npm and their mirrors**: ask the user to connect
  a network or a proxy, then run the command again.
- **Windows without winget, when Python or FFmpeg could not be downloaded** (the script
  says so): ask the user to install "App Installer" from the Microsoft Store.
- **Videos that need a login** (some Bilibili and YouTube links): the user chooses a
  browser in the app's settings whose login FluentFlow may reuse.
