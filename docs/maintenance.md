# Independent Local Repository

FluentFlow Local is maintained here as an independent source repository. It has its own local application entrypoint, frontend build, tests, CI, launchers, dependencies and release history.

`docs/edition_boundaries.md` is the mandatory entry point for new conversations
and cross-repository work. It overrides any historical wording that describes
this repository as a generated export.

The hosted FluentFlow repository may retain similar implementations where each product needs them, but neither repository is a generated mirror of the other. Changes required by both editions are intentionally ported and reviewed in each repository; do not create a new shared-base repository merely to remove duplication.

The local app keeps existing user data compatibility through `backend/core/runtime_paths.py`. Repository maintenance must not delete or relocate the system application-data directory, local credentials, task history, media, or generated results.

For normal development, install `requirements-local.txt`, then run `npm run build:frontend` and start `backend.local_main:app`. CI validates the local frontend and the selected local backend suite on Python 3.10.
