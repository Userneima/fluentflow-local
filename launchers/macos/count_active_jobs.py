"""How many tasks are actually running, for the launcher's restart guard.

Its own file because the launcher used to answer this with a grep over the raw
JSON, and that was wrong twice over:

1. **It counted things that were not tasks.** `grep -Eo '"status":"pending"'`
   matches the *steps* inside a task's progress snapshot. One failed task with
   four not-yet-run steps made the launcher announce "4 个任务正在进行中" forever,
   on a machine with nothing running — which is exactly the shape of warning that
   teaches a user to ignore warnings.
2. **It was looking at the wrong list.** The request carried no client id, so it
   saw only unscoped jobs — one of them — while the browser's list held seven. A
   guard whose job is to protect running work could not see the running work.

So this parses JSON and counts nothing but each job's own `status`, and it asks
under both scopes the local edition uses: no client id (agents, CLI) and
`local-single-user`, which is what the page always sends on 127.0.0.1. The larger
answer wins, because either scope having a live task is a reason not to restart.

Prints one integer. `-1` means "could not tell" — a service that is broken or
answering nonsense must lead to one extra question, never to a silent restart that
interrupts a transcription.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

LIVE_STATUSES = {"queued", "processing", "running", "pending"}

# Both scopes the local edition uses. `local-single-user` is what the browser
# sends for 127.0.0.1/localhost (see shouldUseLocalSingleUserClientId); anything
# submitted by an agent or by curl without a header lands in the unscoped list.
SCOPES = (None, "local-single-user")

TIMEOUT_SECONDS = 3


def count_for_scope(base_url: str, client_id: str | None) -> int | None:
    request = urllib.request.Request(base_url.rstrip("/") + "/jobs")
    if client_id:
        request.add_header("X-FluentFlow-Client-Id", client_id)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        return None
    return sum(
        1
        for job in jobs
        if isinstance(job, dict) and str(job.get("status") or "") in LIVE_STATUSES
    )


def active_job_count(base_url: str) -> int:
    counts = [count for scope in SCOPES if (count := count_for_scope(base_url, scope)) is not None]
    return max(counts) if counts else -1


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(-1)
        return 0
    print(active_job_count(argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
