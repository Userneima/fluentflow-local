# FluentFlow Local Agent Notes

## Purpose

This repository is the independent source repository for FluentFlow Local: a local-first video/audio transcription and note workspace. Its HTTP application is `backend/local_main.py`; it must not acquire hosted accounts, quotas, deployment configuration, or cloud-only administration.

Before any implementation or handoff, read `docs/05-仓库边界.md` and
confirm that the task belongs to Local. Historical export terminology is not a
current workflow. If the task is for Hosted, switch to the independent
`fluentflow` repository and follow its `AGENTS.md`.

## Boundaries

- Keep runtime data outside the repository. Never commit `.env`, credentials, databases, media, transcripts, notes, logs, exports, `node_modules/`, `.venv/`, or `frontend/dist-local/`.
- Preserve compatibility with existing runtime paths in `backend/core/runtime_paths.py`; do not move or delete user data as part of source maintenance.
- The macOS and Windows launchers are product surfaces. Local frontend changes build to `frontend/dist-local` and are served by `backend.local_main`.
- This repository is independently maintained. Do not add an export-from-another-repository workflow or generated-source provenance files.
- Shared capabilities are deliberately ported, reviewed, tested, and committed
  in each repository; never assume a change here reaches Hosted automatically.

## Open problems

Open work, including known problems not yet fixed, is in `docs/01-待办.md`. Its order is the owner's priority rule: items that change what a recording turns into (the cut, the transcript, the note) come first, install next, speed and convenience last; within each, batches that can be done together. When you add an entry, place it by that rule, and for a result-changing item say whether redoing it needs the original recording or only the cut video and output folder, since the owner decides from that whether to keep his originals (decision 8.3). Read it before working in an area it covers; remove an entry when you fix, drop or park it.

Why things are the way they are is in `docs/02-决策记录.md`, grouped by product part (submission, where to cut, exporting the cut video, transcription, notes, outputs, install, ways of working); each entry carries its date, who decided and its commits on the line under its heading. Check the part a change touches before changing something already decided. When you fix, drop, park or narrow an item, or when the owner settles a decision, sets a rule or a way of working, or something is verified by measurement or reading the code, add an entry to the part it belongs to in the same turn, even with no code change and, if users notice the change, a line in `docs/03-更新记录.md`; never delete a decision entry, write a new one that supersedes it.

A change is finished when every document it affects says what is true now, in the same push, without being asked: besides the to-do, the decision log and the changelog, `README.md` (what a user does and gets: where outputs land, what to click, what to expect), `AGENT_INSTALL.md`, `agent/skills/fluentflow/SKILL.md` and the MCP tool descriptions in `scripts/fluentflow_mcp_server.py` (what an agent is told), and `docs/04-维护手册.md` (settings, environment variables, checks). Before pushing, search these for the feature or behaviour you changed and fix what no longer holds (2026-10-19: the output folder and Obsidian editing shipped with the decision log updated and the README silent).

## Interface rules

Before changing any page, read `docs/06-界面规矩.md`: FluentFlow's words, which formatter writes each quantity, button tiers and sizes, colours, corners, type sizes and the three window widths to check. Button and card styles live only in `frontend/src/lib/ui.js`; `frontend/src/ui-rules.test.js` fails when a page writes its own. The rules that hold for any tool product are in the `tool-product-ui` skill.

## Validation

Before a local checkpoint commit run `git diff --check`. For frontend changes run `npm run lint:frontend`, `npm run build:frontend`, and `npm run test:frontend`. For backend changes run the relevant `pytest` tests and `python3 -m ruff check backend/ scripts/` (CI runs it; the test suite does not catch unused imports). Push finished, checked work to `main` as it is done, without asking; do not deploy, tag, or change a release version unless explicitly requested. Users install the latest release tag, not `main`, with three exceptions that reach every user the moment they are pushed: `install.sh` and `install.ps1` (fetched from `main`, then they download the release) and `backend/core/recommended_models.json` (every installed copy reads it from `main` daily). Changes to these three must work with the latest release.

## Test records

When a real-run test needs a written record of the process (timings, models
used, what was checked, problems found), save it under
`~/Downloads/FluentFlow测试记录/<YYYY-MM-DD-HHMMSS>-<topic>/` (owner,
2026-10-16). FluentFlow's own outputs stay where the product puts them: the
folder beside a recording from the user's disk, with the transcript and
subtitles in its `过程文件`. Link the record and that output folder in the
reply.

## Delivery records

A block of work handed to a separate session (one lane of a plan, one
sub-session task) leaves a record in `docs/交付记录/YYYY-MM-DD-<topic>.md`,
following the `subsession-acceptance` skill (owner, 2026-10-22): the session's delivery
note, the acceptance section the main session adds after an independent review,
and the file name of the exported conversation. Before/after screenshots go in
`docs/images/` and are linked from the record. The plan document links each
lane to its record. Follow-up work after acceptance gets its own record,
linked from the original. Records are kept, not deleted. Exported conversations live
outside the repository in `~/Documents/对话存档/fluentflow-local/`.

## Restarting the backend

Queued and running tasks live in the backend process. Restarting it marks every
one of them failed, and in practice every "服务重启中断了这个任务" failure on
record came from an agent restarting the service to load new code while its own
batch was still queued. Before stopping or restarting the backend on port 8000,
run `python3 launchers/macos/count_active_jobs.py http://127.0.0.1:8000`; restart
only when it prints `0`. It counts a task whose cut is still rendering or whose
note is still being written, though the task itself already reads completed.
Otherwise wait for the queue to drain, or test the new code on another port.
