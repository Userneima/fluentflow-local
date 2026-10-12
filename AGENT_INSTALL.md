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

## Tell the user how to use it

Say this, in the user's language:

- FluentFlow is installed and running; later it opens from the Desktop icon.
- To get a note, give you (the agent) a video file or a Douyin, Bilibili or YouTube link
  in a new conversation, or drop it into the app's page.
- You write the notes yourself, so no API key is needed. Entering a model key in the
  app's settings is optional; with one, the app writes notes on its own.

The MCP tools appear in the user's next conversation with you. When asked for a note
then: submit with `submit_local_media` or `submit_video_link`, wait with `wait_task`,
fetch the material with `get_agent_note_material`, write the note following its
`instructions`, and save it with `save_agent_note`. A long recording comes in several
parts; fetch and write each, then save them together.

## Only these need the user

- **Not enough disk space** (a step fails with "No space left on device"; the install
  needs about 8 GB free): ask the user to free space, then run the command again.
- **No network access to GitHub, PyPI, npm and their mirrors**: ask the user to connect
  a network or a proxy, then run the command again.
- **Windows without winget**: ask the user to install "App Installer" from the
  Microsoft Store.
- **Videos that need a login** (some Bilibili and YouTube links): the user chooses a
  browser in the app's settings whose login FluentFlow may reuse.
