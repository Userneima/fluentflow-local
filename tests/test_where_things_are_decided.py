"""Each concept is decided in one place (AGENTS.md, "Where things are decided").

Requirements (owner, 2026-10-25, docs/方案/2026-10-25-让agent改得对的架构整理.md):

- Every file the map names exists, so an agent following it lands somewhere.
- Whether a task is still busy is judged only by ``task_activity``. Four places
  once each guessed it from the note's and the cut's running state, and they
  disagreed. Reading that state directly elsewhere fails here, except in the
  places listed below, each with its reason.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNED = ("backend", "scripts", "launchers")

# Module names the running-state readers are imported as.
_RUNNING_STATE_MODULES = {"debreath_job", "dj", "visual_note_job", "vn"}

# Files that may read the cut's or the note's running state directly.
ALLOWED_RUNNING_STATE = {
    "backend/core/task_activity.py": "the one place that judges whether a task is busy",
    "backend/core/debreath_job.py": "its own state, and clearing renders a restart stranded",
    "backend/core/visual_note_job.py": "its own state, and clearing notes a restart stranded",
    "backend/routers/local_job_debreath.py": "one cut at a time for the same task, not whether the task is busy",
    "backend/core/agent_note.py": "an agent's note refused while the same note is being rewritten",
}

# Files that may compare a note status with "pending".
ALLOWED_PENDING_NOTE = {
    "backend/core/task_activity.py": "the one place that judges whether a task is busy",
    "backend/core/visual_note_job.py": "its own state, and clearing notes a restart stranded",
    "launchers/macos/count_active_jobs.py": "reading a service from before tasks carried their activity",
}


def _python_files():
    for folder in SCANNED:
        for path in (ROOT / folder).rglob("*.py"):
            if "__pycache__" not in path.parts:
                yield path.relative_to(ROOT).as_posix(), path


def _reads_running_state(tree: ast.AST, own_module: bool) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "is_running"
            and isinstance(func.value, ast.Name)
            and func.value.id in _RUNNING_STATE_MODULES
        ) or (own_module and isinstance(func, ast.Name) and func.id == "is_running"):
            lines.append(node.lineno)
    return lines


def _compares_note_status_with_pending(source: str, tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        parts = [node.left, *node.comparators]
        if not any(isinstance(part, ast.Constant) and part.value == "pending" for part in parts):
            continue
        if "summary_status" in (ast.get_source_segment(source, node) or ""):
            lines.append(node.lineno)
    return lines


def test_every_file_the_map_names_exists():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    section = text.split("## Where things are decided", 1)[1].split("\n## ", 1)[0]
    named = re.findall(r"`((?:backend|frontend|scripts|agent|tests)/[^`]+)`", section)
    assert len(named) >= 10, "the map lost its entries"
    missing = [path for path in named if not (ROOT / path).exists()]
    assert not missing, f"AGENTS.md names files that do not exist: {missing}"


def test_only_task_activity_judges_whether_a_task_is_busy():
    found = []
    for relative, path in _python_files():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        own = relative.endswith(("debreath_job.py", "visual_note_job.py"))
        if relative not in ALLOWED_RUNNING_STATE:
            found += [f"{relative}:{line} reads a running state" for line in _reads_running_state(tree, own)]
        if relative not in ALLOWED_PENDING_NOTE:
            found += [
                f"{relative}:{line} reads a pending note"
                for line in _compares_note_status_with_pending(source, tree)
            ]
    assert not found, (
        "Ask backend/core/task_activity.py whether a task is busy instead of reading "
        "the cut's or the note's state (AGENTS.md, 'Where things are decided'):\n" + "\n".join(found)
    )


def test_the_allowed_places_still_need_their_exception():
    # An exception nobody uses any more is removed, so the list stays honest.
    unused = []
    for relative, path in _python_files():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        own = relative.endswith(("debreath_job.py", "visual_note_job.py"))
        if relative in ALLOWED_RUNNING_STATE and relative != "backend/core/task_activity.py":
            if not _reads_running_state(tree, own):
                unused.append(relative + " (running state)")
        if relative in ALLOWED_PENDING_NOTE and relative != "backend/core/task_activity.py":
            if not _compares_note_status_with_pending(source, tree):
                unused.append(relative + " (pending note)")
    assert not unused, f"remove these from the allowed lists: {unused}"


def test_the_check_catches_the_shapes_it_is_for():
    sample = (
        "from backend.core import debreath_job\n"
        "if debreath_job.is_running(result):\n    pass\n"
        "if result.get('summary_status') == 'pending':\n    pass\n"
    )
    tree = ast.parse(sample)
    assert _reads_running_state(tree, own_module=False) == [2]
    assert _compares_note_status_with_pending(sample, tree) == [4]
