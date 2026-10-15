"""The recording a task was made from, when it is not where it was.

Requirement (owner, 2026-10-15). After a task, re-rendering a cut (a decision
turned around, a change undone) reads the original again, and so does
listening to a cut stretch. Users move, rename and delete files, and external
drives come and go. So before using the original:

- it is looked for where it was, then by its file name anywhere Spotlight
  indexes (a recording moved to another folder is found and the task follows
  it);
- whatever is found must be as long as the recording that was processed: a
  different file under the same name would have the old decisions cut into it
  in the wrong places;
- when it cannot be found, the user is told where it was and what to do,
  instead of a render that fails half way.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

from backend.core import job_store, silence_cuts
from backend.core.storage_paths import find_source_file

# Containers round durations differently; a different recording is minutes off.
DURATION_TOLERANCE_SECONDS = 1.5
SEARCH_TIMEOUT_SECONDS = 10
MAX_CANDIDATES = 20


class OriginalMissing(RuntimeError):
    """The original cannot be used, in the user's words."""


def _recorded(job: dict[str, Any]) -> tuple[str, Path | None]:
    """Which metadata field holds the original's path, and the path."""
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    origin = metadata.get("folder_intake") if isinstance(metadata.get("folder_intake"), dict) else {}
    if str(origin.get("original_path") or "").strip():
        return "folder_intake", Path(str(origin["original_path"])).expanduser()
    if str(metadata.get("kept_original_path") or "").strip():
        return "kept_original_path", Path(str(metadata["kept_original_path"])).expanduser()
    return "", None


def expected_duration(job: dict[str, Any]) -> float | None:
    """How long the processed recording was."""
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    state = result.get("debreath") if isinstance(result.get("debreath"), dict) else {}
    report = ((state.get("survey") or {}).get("report") or {}) if isinstance(state.get("survey"), dict) else {}
    plan = state.get("plan") if isinstance(state.get("plan"), dict) else {}
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    for value in (report.get("before_seconds"), plan.get("source_duration_seconds"), metadata.get("duration_seconds")):
        try:
            if value:
                return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _duration(path: Path) -> float | None:
    try:
        return silence_cuts.media_duration_seconds(path)
    except Exception:  # noqa: BLE001 - unreadable is not a match
        return None


def spotlight(name: str) -> list[Path]:
    """Files called exactly ``name`` that Spotlight knows about."""
    try:
        found = subprocess.run(
            ["mdfind", "-name", name], capture_output=True, text=True, timeout=SEARCH_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    paths = [Path(line) for line in (found.stdout or "").splitlines() if line.strip()]
    return [path for path in paths if path.name == name and path.is_file()][:MAX_CANDIDATES]


def _same_length(path: Path, expected: float | None) -> bool:
    if expected is None:
        return True
    duration = _duration(path)
    return duration is not None and abs(duration - expected) <= DURATION_TOLERANCE_SECONDS


def _clock(seconds: float) -> str:
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def locate(task_id: str, client_id: str | None = None, *, search: Callable[[str], list[Path]] = spotlight) -> Path:
    """The original, checked to be the recording that was processed. Follows a
    moved file and records its new place on the task. Raises
    ``OriginalMissing`` with what to do when there is none to use."""
    job = job_store.get_job(task_id, client_id=client_id)
    if not job:
        raise OriginalMissing("任务不存在")
    expected = expected_duration(job)
    stored = find_source_file(task_id)
    if stored is not None and _same_length(stored, expected):
        return stored
    field, recorded = _recorded(job)
    if recorded is None:
        raise OriginalMissing("这个任务的原片已经不在本机了（可能过了保留期），没法再从原片重新导出。")
    if recorded.is_file():
        if _same_length(recorded, expected):
            return recorded
        found = _duration(recorded)
        raise OriginalMissing(
            f"「{recorded.name}」和处理时的不是同一个文件：处理时长 {_clock(expected or 0)}，"
            f"现在是 {_clock(found or 0)}。把处理时那份放回 {recorded.parent}，再试一次。"
        )
    for candidate in search(recorded.name):
        if _same_length(candidate, expected):
            _follow(job, field, candidate)
            return candidate
    raise OriginalMissing(
        f"原片找不到了：原来在 {recorded}。把它放回原处，或放在电脑上任何文件夹里、保持原来的文件名，再试一次。"
    )


def _follow(job: dict[str, Any], field: str, path: Path) -> None:
    metadata = dict(job.get("metadata") or {}) if isinstance(job.get("metadata"), dict) else {}
    if field == "folder_intake":
        metadata["folder_intake"] = {**dict(metadata.get("folder_intake") or {}), "original_path": str(path)}
    else:
        metadata["kept_original_path"] = str(path)
    job_store.upsert_job(
        task_id=str(job["task_id"]), status=str(job.get("status") or "completed"),
        client_id=job.get("client_id"), metadata=metadata,
    )


__all__ = ["OriginalMissing", "expected_duration", "locate", "spotlight"]
