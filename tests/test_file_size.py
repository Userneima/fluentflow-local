"""Source files stop growing past the size an agent can read in one go.

Requirements (owner, 2026-11-19, docs/方案/2026-11-19-全项目优化和防再犯机制.md):

- A source file (backend, scripts, launchers, frontend/src; tests excluded)
  has at most 800 lines.
- The files already longer than that are listed below with their size on
  2026-11-19, and none may grow past it. Agents changing this repository spent
  most of each session reading: the editor page had one 2,100-line component
  and was read 38 times in one session, the MCP server grew by 830 lines in a
  month. New code goes into a new module (a hook, a component, a helper
  module) that the long file calls.
- When a listed file shrinks, lower its number in the same commit, so the room
  freed is not filled again; when it drops to 800 or below, delete its line.
- Raising a number is for the rare change that cannot live anywhere else; say
  why in the commit message.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOTS = ("backend", "scripts", "launchers", "frontend/src")
SUFFIXES = {".py", ".js", ".jsx", ".mjs"}
LIMIT = 800

# Line counts on 2026-11-19. Only ever lowered.
LONG_FILES = {
    "backend/core/video_source.py": 2743,
    "backend/core/silence_cuts.py": 2361,
    "backend/core/media_job.py": 1826,
    "backend/core/visual_note_job.py": 1723,
    "backend/core/ai_summarizer.py": 1656,
    "backend/core/job_store.py": 1559,
    "backend/core/survey_cut.py": 1359,
    "backend/core/local_stt.py": 1200,
    "frontend/src/lib/format.js": 1170,
    "backend/core/debreath_job.py": 1059,
    "backend/core/output_folder.py": 1030,
    "backend/core/lark_exporter.py": 1015,
    "frontend/src/routes/media-text.jsx": 1010,
    "backend/core/task_flow.py": 950,
    "backend/core/review_note.py": 921,
    "backend/core/subtitle_correction.py": 918,
    "backend/core/claude_code_note.py": 912,
    "backend/core/task_detail_core.py": 891,
    "backend/core/agent_note.py": 888,
    "frontend/src/routes/agent-trace.jsx": 856,
    "scripts/history_snapshot.py": 850,
    "backend/core/task_submission.py": 845,
}


def _sources():
    for root in ROOTS:
        for path in (ROOT / root).rglob("*"):
            if (
                path.suffix in SUFFIXES
                and ".test." not in path.name
                and "node_modules" not in path.parts
                and "__pycache__" not in path.parts
            ):
                yield path


def _lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8", errors="ignore").splitlines())


def test_no_source_file_grows_past_its_limit() -> None:
    too_long = []
    for path in _sources():
        name = path.relative_to(ROOT).as_posix()
        allowed = LONG_FILES.get(name, LIMIT)
        lines = _lines(path)
        if lines > allowed:
            too_long.append(f"{name}: {lines} lines, allowed {allowed}")
    assert not too_long, (
        "Put the new code in a new module the long file calls, instead of "
        "growing it:\n" + "\n".join(too_long)
    )


def test_listed_numbers_follow_the_files_down() -> None:
    stale = []
    for name, allowed in LONG_FILES.items():
        path = ROOT / name
        if not path.exists():
            stale.append(f"{name}: gone, delete its line")
            continue
        lines = _lines(path)
        if lines <= LIMIT:
            stale.append(f"{name}: now {lines} lines, delete its line")
        elif lines < allowed - 50:
            stale.append(f"{name}: now {lines} lines, lower {allowed} to {lines}")
    assert not stale, "Lower these in tests/test_file_size.py:\n" + "\n".join(stale)
