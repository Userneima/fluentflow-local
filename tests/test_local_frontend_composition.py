"""Local frontend composition root: the transitive import graph must not reach
hosted-only frontend files.

Mixed files remain reachable for now (the shared pages still carry hosted
branches); purifying them is the tracked mixed-split export blocker. What this
suite pins is the step-6 boundary: no login/AccessGate, admin console,
commercial landing, or OSS upload client in the local import graph.
"""

from __future__ import annotations

import re
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_ROOT = "frontend/src/local-app.jsx"
JS_IMPORT_RE = re.compile(r"(?:import|export)\s+(?:[^'\"]+?\s+from\s+)?['\"]([^'\"]+)['\"]")


def _resolve_js_import(current: str, target: str) -> str | None:
    if not target.startswith("."):
        return None
    candidate = (ROOT / current).parent / target
    options = (candidate, candidate.with_suffix(".js"), candidate.with_suffix(".jsx"), candidate / "index.js", candidate / "index.jsx")
    for option in options:
        if option.is_file():
            return option.resolve().relative_to(ROOT.resolve()).as_posix()
    return None


def _frontend_import_graph(start: str) -> set[str]:
    seen: set[str] = set()
    queue = deque([start])
    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        text = (ROOT / current).read_text(encoding="utf-8")
        for target in JS_IMPORT_RE.findall(text):
            resolved = _resolve_js_import(current, target)
            if resolved and resolved not in seen:
                queue.append(resolved)
    return seen


def test_local_frontend_composition_root_is_present():
    assert (ROOT / LOCAL_ROOT).is_file()
    assert (ROOT / "frontend/local.html").is_file()


def test_local_frontend_graph_omits_hosted_only_surfaces():
    graph = _frontend_import_graph(LOCAL_ROOT)
    for absent in (
        "frontend/src/app.jsx",
        "frontend/src/app/AccessGate.jsx",
        "frontend/src/app/AppShell.jsx",
        "frontend/src/app/AppProvider.jsx",
        "frontend/src/app/hostedRoutes.jsx",
        "frontend/src/routes/landing.jsx",
        "frontend/src/routes/admin.jsx",
        "frontend/src/routes/dashboard.jsx",
        "frontend/src/routes/HostedEditorWorkspace.jsx",
        "frontend/src/routes/HostedMediaText.jsx",
        "frontend/src/lib/ossDirectUpload.js",
    ):
        assert absent not in graph, f"hosted-only surface reachable from local root: {absent}"
    # Sanity: the walk is real — it must reach the actual workspace pages.
    for present in (
        "frontend/src/app/localRoutes.jsx",
        "frontend/src/app/shared.jsx",
        "frontend/src/components/SideNav.jsx",
    ):
        assert present in graph, f"local graph unexpectedly missing: {present}"


def test_local_entry_html_boots_the_local_root():
    html = (ROOT / "frontend/local.html").read_text(encoding="utf-8")
    assert 'src="/src/local-app.jsx"' in html
    assert "/src/app.jsx" not in html


def test_local_root_uses_the_account_free_state_provider():
    source = (ROOT / LOCAL_ROOT).read_text(encoding="utf-8")
    assert "LocalAppProvider" in source
    assert "./app/AppProvider.jsx" not in source


def test_local_frontend_graph_omits_extracted_hosted_api_module():
    """The hosted-only fetch helpers (guest trial, quota, admin, hosted Feishu
    OAuth, desktop sync) were inverted out of shared.jsx into hostedApi.js,
    registered only by the hosted composition root. It must be unreachable from
    the local root, while the registry seam (hostedApiExtension.js) stays."""
    graph = _frontend_import_graph(LOCAL_ROOT)
    assert "frontend/src/app/hostedApi.js" not in graph
    assert "frontend/src/app/hostedApiExtension.js" in graph


# Source terms fully removed from the local import graph. The hosted route
# strings left with the API-helper inversion (hostedApi.js); the cloud STT
# provider left with the STT policy seam (sttPolicy.js defaults to the local
# policy, app.jsx registers HOSTED_STT_POLICY, and settings/agent-tasks render
# policy-provided options and labels instead of naming the provider); the
# guest-trial cancel left when the record-cancel seam landed (shared pages call
# cancelJobRecord, whose default is the only cancel this edition has and whose
# hosted override routes a trial record through the trial's own token); the
# hosted Feishu-OAuth route identifier left when the Lark route policy default
# flipped to the local edition (app.jsx registers HOSTED_LARK_EXPORT_POLICY,
# the settings page renders policy-provided extra route options, and
# settingsModel keeps only the plain legacy stored values as migration
# inputs that remap to the fallback when no OAuth route is registered).
LOCAL_GRAPH_CLEARED_SOURCE_TERMS = (
    "/guest-trial",
    "/desktop-sync/",
    "/oss-upload-sessions",
    "/auth/google",
    "/account/desktop-pair",
    "FLUENTFLOW_PUBLIC_MODE",
    "elevenlabs_scribe",
    "LARK_EXPORT_ROUTE_USER_OAUTH",
    "/account/deletion",
    "getDesktopSyncStatus",
    "processGuestTrialFile",
    "subscribeGuestTrialJobEvents",
    "cancelGuestTrialJob",
    "fluentflow_guest_trial_token",
    "fluentflow_guest_trial_task_id",
)

# Honest residue tracking: when a purification stage cannot finish a term,
# pin it here as {term: {exact files}} so it cannot spread. Currently empty —
# both tracked page/leaf residue terms were cleared.
LOCAL_GRAPH_REMAINING_SOURCE_TERMS = {}


def _local_graph_js_files() -> set[str]:
    return {
        path
        for path in _frontend_import_graph(LOCAL_ROOT)
        if path.endswith((".js", ".jsx"))
    }


def test_local_frontend_graph_cleared_source_terms_are_gone():
    js_files = _local_graph_js_files()
    for term in LOCAL_GRAPH_CLEARED_SOURCE_TERMS:
        offenders = sorted(
            path for path in js_files if term in (ROOT / path).read_text(encoding="utf-8")
        )
        assert offenders == [], f"forbidden term '{term}' reachable from local root: {offenders}"


def test_local_frontend_graph_remaining_source_terms_stay_confined():
    js_files = _local_graph_js_files()
    for term, expected in LOCAL_GRAPH_REMAINING_SOURCE_TERMS.items():
        offenders = {
            path for path in js_files if term in (ROOT / path).read_text(encoding="utf-8")
        }
        assert offenders == expected, (
            f"residual term '{term}' spread beyond the tracked files; "
            f"expected {sorted(expected)}, found {sorted(offenders)}"
        )


def test_local_frontend_graph_has_no_guest_trial_paths():
    """Slash-less variant guard: the gate's source_terms list uses /guest-trial,
    which missed the API-relative 'guest-trial/jobs/' form (found in review)."""
    graph = _frontend_import_graph(LOCAL_ROOT)
    offenders = [
        path for path in graph
        if "guest-trial/" in (ROOT / path).read_text(encoding="utf-8")
    ]
    assert offenders == []
