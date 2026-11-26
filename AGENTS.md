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
- The steps a task records after it is queued, and how long each took (`timings`, the queue and the agent's own note included): `backend/core/task_steps.py`; a step's write or read from the event loop is the `*_off_loop` call there, never the sync one
- The processing queue (which task runs now, which wait): `backend/core/task_queue.py`
- Whether FluentFlow holds a note for a task (the outcome badge, the card's note length, the 有笔记 filter): `frontend/src/lib/taskState.js` (`noteCharsOf`)
- The order of the records list and the start page's recent list (live tasks, then the ones the user pinned, then by time): `frontend/src/lib/jobMappers.js` (`sortJobsForHistoryView`); whether a record is pinned: `metadata.pinned_at`, set only by `POST /jobs/{id}/pin` in `backend/routers/local_job_mutation.py`
- Whether a note is studied (学完了), its verdict and course (the recording's folder), the study record it leaves in the note feedback, and the note as last written whole that the record compares against: `backend/core/study_record.py` (`metadata.studied`, set only through `mark`, by `POST /jobs/{id}/studied` and the agent's `mark_studied`); the 学完的 list's groups: `frontend/src/routes/agent-tasks-list.js` (`groupStudied`)
- What can be done to a task (the cut report's answers, cancel, stop a step, carry on), for the page and agents alike: `backend/core/task_actions.py`; stopping what a step started: `backend/core/task_stop.py`
- What runs once a task's turn comes (pipeline, cut, note, compressed copy, Feishu): `backend/core/task_flow.py`
- Turning a submission into a queued task (options, context, by path, retry): `backend/core/task_submission.py`; a video link: `backend/core/link_submission.py`
- Cutting again or rewriting the note of a finished task on request: `backend/core/task_reruns.py`
- A note from a transcript file (an uploaded .srt/.vtt/.txt, a link's captions, or an .srt/.vtt on this machine submitted by path, whose note goes in a folder beside it): `backend/core/transcript_note.py`
- Storing and reading task records: `backend/core/job_store.py`
- How the page calls the service and who it is to it (client id, access token and their cookies, `apiFetch`): `frontend/src/app/apiConfig.js`; `shared.jsx` re-exports it
- The address of a task's page (note and playback) given to agents as `page_url`: `backend/routers/agent_v1/common.py` (`_page_url`)
- The agent routes (`/agent/v1`): `backend/routers/agent_v1/`, one module per group of routes, mounted in path-matching order by `backend/routers/local_agent.py`; what they share, and which names tests replace on `common` rather than on a route module, is in `common.py`'s docstring
- What to cut, read from the whole timeline: `backend/core/survey_cut.py`
- Whether the cut report still asks the user about a judge's places (closing its reminders is offered once he kept agreeing with that judge, and done only on his answer, kept in the config as `cut_judge_reminders`), for the list, the page and agents: `backend/core/cut_trust.py`
- Which model judges the cut: `backend/core/cut_judge.py`; the user's agent judging it: `backend/core/agent_cut.py` (and whether an agent's submission is cut at all, `cut=false`: `cut_declined`)
- Rendering the cut video: `backend/core/debreath_job.py`
- The stretches a decided cut keeps while it renders (what an agent's note is written from meanwhile): `backend/core/survey_cut.py` (`ranges_while_rendering`); whether the agent can start its note then: `backend/core/agent_note.py` (`ready_while_cut_renders`)
- Who handles understanding on an agent submission (current agent by default, explicit background delegation): `backend/core/task_intelligence.py`
- Whether Claude writes the note, through the subscription or a key: `backend/core/visual_note_channel.py`
- How a note is written: `backend/note_skills/fluentflow-note-default/SKILL.md` (a user's own skill replaces it, `FLUENTFLOW_NOTE_SKILL_DIR`), for Claude, the agent and a text model alike; what a text model is told on top of it, the transcript it reads, and the 「没看画面」 line on its notes' copies: `backend/core/note_prompt.py`
- How a review note is written: `backend/note_skills/fluentflow-review-note/SKILL.md`; writing and revising it beside the follow-along note, by the agent or FluentFlow's model: `backend/core/review_note.py`
- Changing passages of a note by their exact text (each found exactly once, all pairs or none), for the follow-along and the review note alike: `backend/core/note_replace.py`
- Running the text-model note (direct, by chapters, the coverage check) and the calls to text models: `backend/core/ai_summarizer.py`, `backend/core/ai_client.py`
- How far FluentFlow's own note writer has got while it writes (stage, parts, chapters, text so far), for the task list and the editor: `backend/core/note_progress.py`; a writer reports there, the page reads it through `task_activity` and `GET /jobs/{id}/note-progress`
- A step's model answers kept until it is done, so carrying it on after an interruption does not pay again: `backend/core/model_answers.py` (every exit that asks with text goes through `remembered`); a stopping service, or a step the user stopped, not waiting for a request in flight and sending nothing more: `model_usage.unless_stopping` (called from `remembered`) and `model_usage.stop_if_asked`; which work counts as the task's: `task_stop.bound`
- Recording each model call (which task, step, model, tokens) and what a task's calls cost: `backend/core/model_usage.py`; the prices: `backend/core/model_prices.json`. Every exit that calls a model wraps its request in `model_usage.call`, and work handed to another thread goes through `model_usage.carry` or `CarryingExecutor`
- Whether this service runs on a test copy of the task database, and what it may then write (only tasks made in the copy, however a request names a task; no carrying on at startup, no Feishu for any task, PDFs only inside the copy), which page build it serves and that the page's requests come back to it: `backend/core/test_copy.py`
- Whether a model call may go out in a test run (the budget) and whether the fake model answers it: `model_usage.call`; what the fake model answers: `backend/core/fake_model.py`. Every exit asks `call.fake` before sending (`tests/test_test_run_switches.py` fails on one that does not)
- Correcting the transcript from on-screen subtitles and from the word list: `backend/core/subtitle_correction.py`, `backend/core/glossary_correction.py`; the current agent doing them, and translating the subtitles, from paged material (which steps are left to it, the pages and their versions, how its answer lands): `backend/core/agent_correction.py`
- The lines Whisper writes when no one says them (channel credits, dropped in any language; stock lines such as "Thank you.", dropped only when the stretch's language is not the recording's): `backend/core/whisper_stock_lines.py`
- A recording in which no one was heard (an empty transcript fails the task and the steps after it are skipped): `backend/core/media_job.py` (`require_speech`), the words and diagnosis in `backend/core/local_error_diagnostics.py` (`NO_SPEECH` and its variants); a video with sound but no speech gets its on-screen text as the transcript instead, and whether it does (loudness, length, `picture_note`): `backend/core/screen_text_transcript.py`; whether a file has a picture to read text from at all (an audio file's cover art does not count), for it and for correcting from on-screen subtitles: `backend/core/frame_text.py` (`has_picture`)
- Saving a correction of a task's transcript and then its txt/srt/vtt files, so a refused save leaves the files alone: `backend/core/transcript_files.py`
- The output folder beside a recording (or a subtitle file by path, `SUBTITLE_INTAKE_KEY`), or where a `FluentFlow 产出位置.md` above it says (`output_parent`), and finding it after it moved: `backend/core/output_folder.py`; following a moved recording: `backend/core/original_locator.py`; keeping a note in step with Feishu: `backend/core/feishu_sync.py`; putting a review note in Feishu above its follow-along note: `backend/core/review_note_feishu.py`; blank lines in a review note (where the owner wants them, and back for the PDF and Feishu; `joins` is asked by the md and by the Feishu layout alike) and which lines are inside a code block (`in_code`, for the md, the PDF and Feishu): `backend/core/note_spacing.py`
- Writing a note into Feishu through lark-cli: `backend/core/lark_cli_exporter.py` and the `lark_cli_*.py` parts beside it; its docstring names each part and where tests replace what
- Matching note sections to playback times: `backend/core/note_section_times.py`; the current agent reading material and returning its table: `backend/core/agent_alignment.py`
- What each processing step currently uses, for the settings page and agents: `backend/core/workflow_status.py`
- How every ffmpeg and ffprobe process is started (`-nostdin` and an empty stdin, so one started under a terminal cannot hang on it): `backend/core/ffmpeg_cmd.py` (`tests/test_ffmpeg_stdin.py` fails on a call that starts one any other way)
- Where runtime data lives, and reading the repository `.env` before any path is asked (`load_project_env`; the shell scripts' copy of that rule: `launchers/windows/fluentflow-env.ps1`, `launchers/macos/fluentflow-env.sh`): `backend/core/runtime_paths.py`; which edition answers: `backend/core/edition_identity.py`
- Where a path setting puts files, for the update check and the uninstallers (references expanded, `~`, a relative path taken from the code folder; what cannot be placed for sure counts as inside it): `Resolve-FluentFlowPlace` / `ff_place` in the two shell files above, whose exact text `install.ps1` and `install.sh` carry; what FluentFlow itself makes in the data folder (and, for a common name such as `bin` or `models`, what it puts inside), the only things removing all data deletes when the folder holds anything else: `launchers/data-folder-contents.txt`
- Which host a download comes from first (the original, or its mirror in China when the original is unreachable or slow): `backend/core/download_source.py`, and the same rule in the two shell files above
- Whether a newer release is out, for the page: `backend/core/release_check.py`
- Button and card styles: `frontend/src/lib/ui.js`; a task's status as the pages show it, and what each pipeline stage is called (`stageName`): `frontend/src/lib/taskState.js`; the record card's live line (what the current stage is doing, never a line an earlier stage left in metadata): `frontend/src/routes/agent-tasks-labels.js` (`liveStageDetail`); when the task entered its stage (`stage_started_at`): `backend/core/job_store.py` (`upsert_job`)
- What an agent is told: the MCP tools in `scripts/fluentflow_mcp/`, one module per group of tools (each tool described once, in the `tool()` decorator on its function; `scripts/fluentflow_mcp_server.py` is the entry point and loads them in the order `tools/list` gives), and `agent/skills/fluentflow/SKILL.md` (workflow)
- How an MCP tool reaches the service (the request, access token, edition check), the
  hold that answers a long call "still working", the heartbeat for watched tasks, and
  whether it starts the service itself when nothing answers (`_start_local_service`):
  `scripts/fluentflow_mcp/core.py` (tests replace them on `core`)
- Whether a launcher opens the page once the service is up (`FLUENTFLOW_OPEN_PAGE`; the
  MCP server's own start passes `0`): `open_app` in `launchers/macos/FluentFlowLocal.command`
  and `Open-AppPage` in `launchers/windows/FluentFlowLocal.ps1`

`frontend/src/routes/editor.jsx` and the records page `agent-tasks.jsx` are the
files most often edited by several sessions at once. Each only calls its hooks
and lays its parts out; the parts live beside it as `editor-<part>.js(x)` and
`agent-tasks-<part>.js(x)`, named in the page file's header comment, and a
change goes into the part's file. The routes
in `backend/routers/` only turn requests into calls; what they call lives in
`backend/core`, and the agent routes (`backend/routers/local_agent.py` and
`backend/routers/agent_v1/`) import nothing from the page's routes (`tests/test_agent_surface.py`).

Before adding or changing an MCP tool, read `.claude/rules/mcp-tools.md` (Claude Code loads it on its own when it opens the tool files).

## Open problems and decisions

Open work and known problems are in `docs/01-待办.md`, ordered by the rule at its top; read it before working in an area it covers, add an entry by that rule (a result-changing item says whether redoing it needs the original recording, decision 8.3), and remove an entry when you fix, drop or park it.

Why things are the way they are is in `docs/决策记录/`, one file per product part; `docs/02-决策记录.md` is the index and says how to read and write entries. Before changing something already decided, read the 现状 at the top of its part. When you fix, drop, park or narrow an item, when the owner settles something, or when something is verified by measurement or reading the code, add an entry in the same turn, as that index says.

A change is finished when every document it affects says what is true now, in the same push, without being asked: the to-do, the decision log, `docs/03-更新记录.md` (what users notice), `README.md` (what a user does and gets), `AGENT_INSTALL.md`, `agent/skills/fluentflow/SKILL.md` and the MCP tool descriptions (what an agent is told), and `docs/04-维护手册.md` (settings, environment variables, checks). Before pushing, search these for the behaviour you changed.

## Interface rules

Before changing any page, read `docs/06-界面规矩.md`: FluentFlow's words, which formatter writes each quantity, button tiers and sizes, colours, corners, type sizes and the three window widths to check. Button and card styles live only in `frontend/src/lib/ui.js`; `frontend/src/ui-rules.test.js` fails when a page writes its own. The rules that hold for any tool product are in the `tool-product-ui` skill.

## Validation

- Before a local commit: `git diff --check`. Frontend changes: `npm run lint:frontend` (it runs knip too: delete what it names, or say in `knip.json` how it is used), `npm run build:frontend` (see below where it builds to), `npm run test:frontend`. Backend changes: the relevant `pytest` tests while working; before pushing, the full suite with `.venv/bin/python -m pytest -n auto` (when the machine is busy and it is killed, exit 137, use `-n 4`; a test failing only under `-n` usually depends on another having run first) and `.venv/bin/python -m ruff check backend/ scripts/`.
- Codex reviews only the push that delivers a piece of work, over everything since the last delivery: push it as `PUSH_REVIEW=deliver git push` with a 10-minute timeout (other pushes to `main` are not reviewed); when it blocks, fix the P0/P1 findings, commit, push again, never `--no-verify`. Relay each risky-area change it prints to the user and say whether it was intended.
- Push finished, checked work to `main` without asking; never deploy, tag or change a release version unless asked. Users install the latest release tag, except `install.sh`, `install.ps1` and `backend/core/recommended_models.json`, which reach every user from `main` the moment they are pushed and must work with the latest release.
- Keep a change readable (decisions 8.23, 8.24): split out the part you change only when understanding it means reading unrelated logic around it. When a button goes, its copy in `shared.jsx` goes too (`tests/test_frontend_copy_used.py`); a hint saying to click 「X」 names text the page has (`tests/test_hint_buttons_exist.py`).

The service on port 8000 serves `frontend/dist-local` straight from this checkout, so `npm run build:frontend` replaces the page the owner is using. When `git status` shows changes you did not make, build into a scratch folder instead (`npx vite build --config vite.local.config.mjs --outDir <scratch folder> --emptyOutDir`) and look at the page through `npm run dev:frontend` (port 5186), so another session's unfinished work does not reach the owner's page. To compare against the code before your change, open a clean copy with `git worktree add <scratch folder> HEAD`; never `git stash` here, it puts away the other sessions' changes too.

## Where files go

Owner, 2026-10-28 (decision 8.15). The repository root holds code, configuration and the entry documents (`README.md`, `AGENTS.md`, `CLAUDE.md`, `AGENT_INSTALL.md`, `CONTRIBUTING.md`, `SECURITY.md`, `LICENSE`, `VERSION`), nothing else; a script that writes output defaults to a folder under `private/`.

- `docs/`: the numbered documents are the living ones; `docs/方案/` dated proposals, `docs/评测/` dated evaluations, `docs/交付记录/` one record per delivered sub-session, `docs/handoffs/` handoffs, deleted when the task is done.
- `private/` (git-ignored, only in the main checkout): `测试记录/` real-run records (`docs/04-维护手册.md`「在真实数据的副本上实测」), `报告/` output of the evaluation scripts and review reports, `promo/` the promo film project and its cuts, `笔记考题/` note quizzes, `旧工作副本/` uncommitted files saved before a worktree was removed.
- `.claude/worktrees/`: sub-session worktrees. Remove one once its branch is in `main`; a prototype kept for later stays as a branch, not a worktree.

## Real-run tests, delivery records, restarting

- Real-run tests on the owner's data run on a copy prepared by `scripts/prepare_test_copy.py`, on a port of their own, `--fake` for a test of the flow; before a test of what a model produces, say whether it goes through the Claude subscription or a key billed per call and what it should cost, and wait for 王愉超's go-ahead when it is billed per call (decision 8.11). How, and where the written record goes: `docs/04-维护手册.md`「在真实数据的副本上实测」.
- A block of work done in a separate session leaves a record in `docs/交付记录/`, as the `subsession-acceptance` skill says.
- Before stopping or restarting the backend on port 8000, run `python3 launchers/macos/count_active_jobs.py http://127.0.0.1:8000` and restart only when it prints `0` and `git status` shows no other session's uncommitted changes under `backend/` or `scripts/`; otherwise wait or test on another port. Why: `docs/04-维护手册.md`「重启服务」.
