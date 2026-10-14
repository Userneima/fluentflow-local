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

Why things are the way they are is in `docs/02-决策记录.md`, grouped by product part (submission, where to cut, exporting the cut video, transcription, notes, outputs, install, ways of working); each entry carries its date, who decided and its commits on the line under its heading. Check the part a change touches before changing something already decided. When you fix, drop, park or narrow an item, add an entry to the part it belongs to and, if users notice the change, a line in `docs/03-更新记录.md`; never delete a decision entry, write a new one that supersedes it.

## Validation

Before a local checkpoint commit run `git diff --check`. For frontend changes run `npm run lint:frontend`, `npm run build:frontend`, and `npm run test:frontend`. For backend changes run the relevant `pytest` tests. Do not push, deploy, tag, or change a release version unless explicitly requested.

## Restarting the backend

Queued and running tasks live in the backend process. Restarting it marks every
one of them failed, and in practice every "服务重启中断了这个任务" failure on
record came from an agent restarting the service to load new code while its own
batch was still queued. Before stopping or restarting the backend on port 8000,
run `python3 launchers/macos/count_active_jobs.py http://127.0.0.1:8000`; restart
only when it prints `0`. Otherwise wait for the queue to drain, or test the new
code on another port.
