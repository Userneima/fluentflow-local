# Installing FluentFlow Local for a user (instructions for agents)

You are an agent (Claude Code, Codex, or similar) and the user asked you to install
FluentFlow Local: a local app that turns videos and recordings into transcripts and
notes. Do the whole install yourself. Nothing below needs the user's password, a
click in a system dialog, or an account; the steps that need a person are listed at
the end, and only those go to the user.

Tell the user once, before you start, that the install downloads about 8 GB and
takes 15 to 30 minutes, then work without further questions.

## 1. Get the code

Use the latest release, unpacked into `~/fluentflow-local` (Windows:
`%USERPROFILE%\fluentflow-local`). Download the zip with `curl` (Windows:
`Invoke-WebRequest`) rather than cloning: on a Mac without the command line developer
tools, running `git` opens a dialog.

```bash
TAG="$(curl -fsSL https://api.github.com/repos/Userneima/fluentflow-local/releases/latest | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p')"
curl -fL -o /tmp/fluentflow-local.zip "https://github.com/Userneima/fluentflow-local/archive/refs/tags/${TAG}.zip"
unzip -q /tmp/fluentflow-local.zip -d /tmp/fluentflow-local-unpacked
mv /tmp/fluentflow-local-unpacked/fluentflow-local-* ~/fluentflow-local
```

If `~/fluentflow-local` already exists, this is an update: follow "更新到新版本" in its
`README.md` instead of overwriting it.

## 2. Run the setup script

```bash
cd ~/fluentflow-local
bash launchers/macos/setup-local.sh
```

Windows (PowerShell):

```powershell
cd $env:USERPROFILE\fluentflow-local
powershell -ExecutionPolicy Bypass -File launchers\windows\setup-local.ps1
```

It installs whatever is missing (FFmpeg, a Python, a portable Node.js) into the app's
own data folder, installs the Python packages, builds the interface, downloads the
transcription model, generates the agent access token into `.env`, runs the readiness
check, and puts a launcher on the Desktop. Failed downloads are retried from mirrors
in China automatically. Run it in the background and wait; it prints a line before
each step.

Success is exit code 0 with every line of the readiness check marked `[OK ]` (a
`[WARN]` line is fine). On failure the last lines say what went wrong and what to do;
fix that and run the script again. It skips what is already installed.

## 3. Start the app and check it

```bash
open "$HOME/Desktop/FluentFlow Local.app"
```

Windows: run `FluentFlow Local.cmd` on the Desktop. The app opens in the browser at
`http://127.0.0.1:8000`. Confirm the service answers:

```bash
curl -fsS http://127.0.0.1:8000/health
```

The answer contains `"status":"ok"` and `"edition":"local"`.

## 4. Connect the MCP tools

The app ships an MCP server. It reads the access token from the repository `.env`,
so registering it needs no secret. Use the virtual environment's Python, not the
system `python3`.

Claude Code:

```bash
claude mcp add --scope user fluentflow-local -- "$HOME/fluentflow-local/.venv/bin/python" "$HOME/fluentflow-local/scripts/fluentflow_mcp_server.py"
```

Codex, in `~/.codex/config.toml`:

```toml
[mcp_servers.fluentflow-local]
command = "/Users/<name>/fluentflow-local/.venv/bin/python"
args = ["/Users/<name>/fluentflow-local/scripts/fluentflow_mcp_server.py"]
```

On Windows the Python is `.venv\Scripts\python.exe`. Check the server starts and lists
its tools:

```bash
~/fluentflow-local/.venv/bin/python ~/fluentflow-local/scripts/check_mcp_server.py
```

The tools appear in the user's next conversation with you.

## 5. Tell the user how to use it

Say this, in the user's language:

- FluentFlow is installed; it opens from the Desktop icon.
- To get a note, give you (the agent) a video file or a Douyin, Bilibili or YouTube
  link in a new conversation, or drop it into the app's page.
- You write the notes yourself, so no API key is needed. Entering a model key in the
  app's settings is optional; with one, the app writes notes on its own.

When asked for a note later: submit with `submit_local_media` or `submit_video_link`,
wait with `wait_task`, then fetch the material with `get_agent_note_material`, write
the note following its `instructions`, and save it with `save_agent_note`. A long
recording comes in several parts; fetch and write each, then save them together.

## Only these need the user

- **Not enough disk space** (a step fails with "No space left on device"; the install
  needs about 8 GB free): ask the user to free space, then rerun the setup script.
- **No network access to GitHub, PyPI, npm and their mirrors**: ask the user to connect
  a network or a proxy, then rerun the setup script.
- **Windows without winget**: ask the user to install "App Installer" from the
  Microsoft Store.
- **Videos that need a login** (some Douyin and Bilibili links): the user chooses a
  browser in the app's settings whose login FluentFlow may reuse.
