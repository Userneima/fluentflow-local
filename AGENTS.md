# FluentFlow Local Agent Notes

## Purpose

This repository is the independent source repository for FluentFlow Local: a local-first video/audio transcription and note workspace. Its HTTP application is `backend/local_main.py`; it must not acquire hosted accounts, quotas, deployment configuration, or cloud-only administration.

Before any implementation or handoff, read `docs/edition_boundaries.md` and
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
