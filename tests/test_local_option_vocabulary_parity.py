"""Pin the run-option vocabulary shared by the upload path and the by-path entry.

Why this test exists
--------------------
Run options in the local edition are assembled in three places, all calling
``_collect_options`` in ``backend/routers/local_processing.py``:

* ``process_media``   -- the browser upload / link route
* ``queue_process``   -- the multi-file / queue route
* ``local_path_options`` -- every entry that has no page: the folder intake,
  the Agent API, the MCP tools, a curl. This one reads a plain payload dict.

The recurring mistake is that a new option gets wired into the upload route and
forgotten in ``local_path_options``. ``_collect_options`` just strips and drops
whatever it is not handed, with no error, so the by-path run then *reports
success with the feature never having run*. It has happened at least three times
across separate sessions:

* 2026-08-28 -- a folder-queued task could not even be retried (the retry entry
  had drifted from the upload entry); fixed in commit 54a0a6ea.
* 2026-09-02 -- a meeting recording submitted by path came back with the
  speaker/voice switches ``requested=false``; fixed in commit c203055a.
* 2026-09-03 -- an 8-person meeting through the Agent API transcribed with no
  speakers, because "default on" lived only in the page; fixed in commit
  18ff2385.

``tests/test_local_path_options.py`` locks the *individual* options already
found. It cannot catch the *next* forgotten one. This test does: it snapshots
the option key set of each call site, so adding an option to the upload path
without a conscious decision about the by-path entry fails here, not on a user's
screen.

This test does not assert the by-path entry *should* forward every upload-path
option -- some are deliberately page-only. It pins the current difference. When
that difference changes, update the pinned sets below, and while you are here
decide whether the new option is a real gap (forward it in ``local_path_options``)
or genuinely page-only (add it to ``EXPECTED_UPLOAD_ONLY`` with a reason).
"""

from __future__ import annotations

import ast
import pathlib

_MODULE = pathlib.Path(__file__).resolve().parents[1] / "backend" / "routers" / "local_processing.py"


def _collect_options_keysets() -> dict[str, set[str]]:
    """Map each enclosing function to the keyword set it passes _collect_options.

    Read straight from the source (not at runtime) so the test needs no ffmpeg,
    no request, and no import side effects -- it is a structural tripwire.
    """
    tree = ast.parse(_MODULE.read_text())
    found: dict[str, set[str]] = {}

    def visit(node: ast.AST, enclosing: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            fn = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else enclosing
            if (
                isinstance(child, ast.Call)
                and getattr(child.func, "id", None) == "_collect_options"
                and fn is not None
            ):
                found.setdefault(fn, set()).update(
                    kw.arg for kw in child.keywords if kw.arg is not None
                )
            visit(child, fn)

    visit(tree, None)
    return found


# The keys the by-path entry (local_path_options) intentionally does NOT forward,
# as of 2026-09-07. Each is dropped for a by-path caller; if one of these is
# actually a gap it belongs in local_path_options instead of here.
EXPECTED_UPLOAD_ONLY = {
    "ai_model",           # AI provider/model come from local backend config for
    "ai_provider",        #   by-path callers, not the request payload.
    "export_to_lark",     # Feishu export + its routing/CLI switch are page-only
    "lark_export_route",  #   controls today.
    "lark_via_cli",
    "folder_token",       # Chosen in the page's Feishu picker.
    "generate_visuals",   # Keyframe/visual note toggle, page-only today.
    "system_prompt",      # Free-text prompt box, page-only today.
    "title",              # By-path callers derive the title from the filename.
}

# The key the by-path entry forwards that the upload routes do not build here.
EXPECTED_BYPATH_ONLY = {"voice_enhance"}


def test_the_two_upload_routes_share_one_option_vocabulary() -> None:
    """process_media and queue_process must accept the same options.

    They are the same upload feature reached two ways; if they drift, one entry
    silently loses an option (the shape of the 2026-08-28 retry gap)."""
    keysets = _collect_options_keysets()
    assert keysets["process_media"] == keysets["queue_process"], (
        "The upload route and the queue route no longer forward the same run "
        "options. Whatever was added to one must be added to the other."
    )


def test_by_path_entry_has_not_silently_dropped_a_new_option() -> None:
    """Freeze the difference between the upload path and the by-path entry.

    A new option added to the upload route but not to local_path_options shows up
    here as an unexpected upload-only key -- the exact silent drop that made
    by-path runs report success without the feature. Fix by forwarding it in
    local_path_options, or, if it is genuinely page-only, add it to
    EXPECTED_UPLOAD_ONLY with a one-line reason."""
    keysets = _collect_options_keysets()
    upload = keysets["process_media"]
    by_path = keysets["local_path_options"]

    upload_only = upload - by_path
    by_path_only = by_path - upload

    assert upload_only == EXPECTED_UPLOAD_ONLY, (
        "The set of options the upload path forwards but the by-path entry "
        "(local_path_options) drops has changed.\n"
        f"  newly dropped by-path: {sorted(upload_only - EXPECTED_UPLOAD_ONLY)}\n"
        f"  no longer dropped:     {sorted(EXPECTED_UPLOAD_ONLY - upload_only)}\n"
        "If you just added a run option, forward it in local_path_options so the "
        "folder intake / Agent API / MCP callers get it too, or add it to "
        "EXPECTED_UPLOAD_ONLY with a reason if it is deliberately page-only."
    )
    assert by_path_only == EXPECTED_BYPATH_ONLY, (
        "The by-path entry now forwards an option the upload routes do not. "
        f"unexpected: {sorted(by_path_only - EXPECTED_BYPATH_ONLY)}; "
        f"missing: {sorted(EXPECTED_BYPATH_ONLY - by_path_only)}. "
        "Keep the upload routes and the by-path entry aligned, then update "
        "EXPECTED_BYPATH_ONLY."
    )
