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

## Where things are decided

Each concept below is decided in one place. Change it there; do not add a second
judgement of it beside the code you happen to be editing. A new concept that
others will need gets a line here in the same change.
`tests/test_where_things_are_decided.py` checks that every file named here
exists and that no other file judges whether a task is still busy.

- Whether a task is still working, on what, and whether it is finished: `backend/core/task_activity.py`
- The steps a task records after it is queued, and how long each took (`timings`, the queue and the agent's own note included): `backend/core/task_steps.py`
- The processing queue (which task runs now, which wait): `backend/core/task_queue.py`
- What can be done to a task (the cut report's answers, cancel, stop a step, carry on), for the page and agents alike: `backend/core/task_actions.py`; stopping what a step started: `backend/core/task_stop.py`
- What runs once a task's turn comes (pipeline, cut, note, compressed copy, Feishu): `backend/core/task_flow.py`
- Turning a submission into a queued task (options, context, by path, retry): `backend/core/task_submission.py`; a video link: `backend/core/link_submission.py`
- Cutting again or rewriting the note of a finished task on request: `backend/core/task_reruns.py`
- A note from a transcript file (an uploaded .srt/.vtt/.txt, or a link's captions): `backend/core/transcript_note.py`
- Storing and reading task records: `backend/core/job_store.py`
- What to cut, read from the whole timeline: `backend/core/survey_cut.py`
- Which model judges the cut: `backend/core/cut_judge.py`; the user's agent judging it: `backend/core/agent_cut.py`
- Rendering the cut video: `backend/core/debreath_job.py`
- The stretches a decided cut keeps while it renders (what an agent's note is written from meanwhile): `backend/core/survey_cut.py` (`ranges_while_rendering`); whether the agent can start its note then: `backend/core/agent_note.py` (`ready_while_cut_renders`)
- Who handles understanding on an agent submission (current agent by default, explicit background delegation): `backend/core/task_intelligence.py`
- Whether Claude writes the note, through the subscription or a key: `backend/core/visual_note_channel.py`
- How a note is written: `backend/note_skills/fluentflow-note-default/SKILL.md` (a user's own skill replaces it, `FLUENTFLOW_NOTE_SKILL_DIR`)
- How a review note is written: `backend/note_skills/fluentflow-review-note/SKILL.md`; writing and revising it beside the follow-along note: `backend/core/review_note.py`
- The text-model note and the calls to text models: `backend/core/ai_summarizer.py`, `backend/core/ai_client.py`
- Recording each model call (which task, step, model, tokens) and what a task's calls cost: `backend/core/model_usage.py`; the prices: `backend/core/model_prices.json`. Every exit that calls a model wraps its request in `model_usage.call`, and work handed to another thread goes through `model_usage.carry` or `CarryingExecutor`
- Whether this service runs on a test copy of the task database, and what it may then write (only tasks made in the copy; no carrying on at startup, no Feishu): `backend/core/test_copy.py`
- Whether a model call may go out in a test run (the budget) and whether the fake model answers it: `model_usage.call`; what the fake model answers: `backend/core/fake_model.py`. Every exit asks `call.fake` before sending (`tests/test_test_run_switches.py` fails on one that does not)
- Correcting the transcript from on-screen subtitles and from the word list: `backend/core/subtitle_correction.py`, `backend/core/glossary_correction.py`
- The output folder beside a recording: `backend/core/output_folder.py`; keeping a note in step with Feishu: `backend/core/feishu_sync.py`
- Matching note sections to playback times: `backend/core/note_section_times.py`; the current agent reading material and returning its table: `backend/core/agent_alignment.py`
- What each processing step currently uses, for the settings page and agents: `backend/core/workflow_status.py`
- Where runtime data lives: `backend/core/runtime_paths.py`; which edition answers: `backend/core/edition_identity.py`
- Button and card styles: `frontend/src/lib/ui.js`; a task's status as the pages show it: `frontend/src/lib/taskState.js`
- What an agent is told: `scripts/fluentflow_mcp_server.py` (each tool described once, in the `tool()` decorator on its function) and `agent/skills/fluentflow/SKILL.md` (workflow)

`frontend/src/routes/editor.jsx` is the file most often edited by several
sessions at once. When a change touches it, first move the part you change into
its own file, then change it there: its header bar, transcript pane, note pane,
and playback with note following. Do not split it for its own sake. The routes
in `backend/routers/` only turn requests into calls; what they call lives in
`backend/core`, and the agent routes (`backend/routers/local_agent.py`) import
nothing from the page's routes (`tests/test_agent_surface.py`).

## Open problems

Open work, including known problems not yet fixed, is in `docs/01-待办.md`. Its order is the owner's priority rule: items that change what a recording turns into (the cut, the transcript, the note) come first, install next, speed and convenience last; within each, batches that can be done together. When you add an entry, place it by that rule, and for a result-changing item say whether redoing it needs the original recording or only the cut video and output folder, since the owner decides from that whether to keep his originals (decision 8.3). Read it before working in an area it covers; remove an entry when you fix, drop or park it.

Why things are the way they are is in `docs/02-决策记录.md`, grouped by product part (submission, where to cut, exporting the cut video, transcription, notes, outputs, install, ways of working); each entry carries its date, who decided and its commits on the line under its heading. Check the part a change touches before changing something already decided. When you fix, drop, park or narrow an item, or when the owner settles a decision, sets a rule or a way of working, or something is verified by measurement or reading the code, add an entry to the part it belongs to in the same turn, even with no code change and, if users notice the change, a line in `docs/03-更新记录.md`; never delete a decision entry, write a new one that supersedes it.

A change is finished when every document it affects says what is true now, in the same push, without being asked: besides the to-do, the decision log and the changelog, `README.md` (what a user does and gets: where outputs land, what to click, what to expect), `AGENT_INSTALL.md`, `agent/skills/fluentflow/SKILL.md` and the MCP tool descriptions in `scripts/fluentflow_mcp_server.py` (what an agent is told), and `docs/04-维护手册.md` (settings, environment variables, checks). Before pushing, search these for the feature or behaviour you changed and fix what no longer holds (2026-10-19: the output folder and Obsidian editing shipped with the decision log updated and the README silent).

## Interface rules

Before changing any page, read `docs/06-界面规矩.md`: FluentFlow's words, which formatter writes each quantity, button tiers and sizes, colours, corners, type sizes and the three window widths to check. Button and card styles live only in `frontend/src/lib/ui.js`; `frontend/src/ui-rules.test.js` fails when a page writes its own. The rules that hold for any tool product are in the `tool-product-ui` skill.

## Validation

Before a local checkpoint commit run `git diff --check`. For frontend changes run `npm run lint:frontend`, `npm run build:frontend`, and `npm run test:frontend`. For backend changes run the relevant `pytest` tests while working, the full suite once before pushing with `.venv/bin/python -m pytest -n auto` (pytest-xdist, about 40 seconds against 2 minutes one by one on the owner's Mac, 2026-10-24; install it with `.venv/bin/python -m pip install pytest-xdist` if missing; a test that fails only under `-n auto` usually depends on another test having run first; when the machine is busy `-n auto` can be killed by the system, exit code 137, so use `-n 4` then), and `python3 -m ruff check backend/ scripts/` (CI runs it; the test suite does not catch unused imports). Push finished, checked work to `main` as it is done, without asking; do not deploy, tag, or change a release version unless explicitly requested. Users install the latest release tag, not `main`, with three exceptions that reach every user the moment they are pushed: `install.sh` and `install.ps1` (fetched from `main`, then they download the release) and `backend/core/recommended_models.json` (every installed copy reads it from `main` daily). Changes to these three must work with the latest release.

## Where files go

Owner, 2026-10-28 (decision 8.15). The repository root holds code, configuration and the entry documents (`README.md`, `AGENTS.md`, `CLAUDE.md`, `AGENT_INSTALL.md`, `CONTRIBUTING.md`, `SECURITY.md`, `LICENSE`, `VERSION`), nothing else; a script that writes output defaults to a folder under `private/`.

- `docs/`: the numbered documents are the living ones; `docs/方案/` dated proposals, `docs/评测/` dated evaluations, `docs/交付记录/` one record per delivered sub-session, `docs/handoffs/` handoffs, deleted when the task is done.
- `private/` (git-ignored, only in the main checkout): `测试记录/` real-run records (next section), `报告/` output of the evaluation scripts and review reports, `promo/` the promo film project and its cuts, `笔记考题/` note quizzes, `旧工作副本/` uncommitted files saved before a worktree was removed.
- `.claude/worktrees/`: sub-session worktrees. Remove one once its branch is in `main`; a prototype kept for later stays as a branch, not a worktree.

## Test records

When a real-run test needs a written record of the process (timings, models
used, what was checked, problems found), save it under
`private/测试记录/<YYYY-MM-DD-HHMMSS>-<topic>/` in the main checkout
(`/Users/yuchao/Developer/fluentflow-local`; `private/` is git-ignored, so a
worktree does not have it). Owner, 2026-10-16; moved here 2026-10-27 under
the file-placement rule in `~/Documents/整理规则.md`: these records are the
evidence the decision log cites. Keep the text, data and frames;
leave out copies of the source video or audio, which can be cut again. FluentFlow's own outputs stay where the product puts them: the
folder beside a recording from the user's disk, with the transcript and
subtitles in its `过程文件`. Link the record and that output folder in the
reply.

Real-run tests on the owner's data run on a copy prepared by
`scripts/prepare_test_copy.py`, on a port of their own (decision 8.11, owner
2026-10-26; how, in `docs/04-维护手册.md`「在真实数据的副本上实测」). A test of the
flow (restart, stop, carry on, export) starts it with `--fake`: the fake model
calls nothing and costs nothing. A test of what a model produces keeps the
budget the script sets; before running it, say in the reply whether it goes
through the Claude subscription or a key billed per call and what it should
cost from earlier measurements, and wait for 王愉超's go-ahead when it is billed
per call. Afterwards report what the usage record shows.

## Delivery records

A block of work handed to a separate session (one lane of a plan, one
sub-session task) leaves a record in `docs/交付记录/YYYY-MM-DD-<topic>.md`,
following the `subsession-acceptance` skill (owner, 2026-10-22): the session's delivery
note, the acceptance section the main session adds after an independent review,
and the file name of the archived conversation (a readable Markdown record). Before/after screenshots go in
`docs/images/` and are linked from the record. The plan document links each
lane to its record. Follow-up work after acceptance gets its own record,
linked from the original. Records are kept, not deleted. Archived conversations live
outside the repository in `~/9.归档/对话存档/fluentflow-local/`.

## Restarting the backend

Queued and running tasks live in the backend process. A restart carries each
task it cut off on in place, from the step it was on (decision 6.42), but the
step that was running is done again (a transcription from the start, a note in
full), a task cut off more than twice in a row is left failed, and in practice
every "服务重启中断了这个任务" failure on record came from an agent restarting
the service to load new code while its own batch was still queued. Before
stopping or restarting the backend on port 8000,
run `python3 launchers/macos/count_active_jobs.py http://127.0.0.1:8000`; restart
only when it prints `0`, and when `git status` in this checkout shows no other session's uncommitted changes under `backend/` or `scripts/`: the service on 8000 runs this working tree, so a restart would load their unfinished code. It counts every task with a step still to come (cut,
note, compressed copy, Feishu export), though the task itself already reads
completed; the service answers that per task from its steps (`activity.busy`).
Stopping the service stops the encodes its tasks started and leaves their step
records as they were (`task_stop.shutdown`), so the restart carries those steps
on; work a step runs on its own thread pool (correcting the transcript from
on-screen subtitles) still finishes its round first.
Otherwise wait for the queue to drain, or test the new code on another port.
