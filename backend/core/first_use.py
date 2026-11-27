"""Whether this machine has had its guided first use, and what the user chose.

Kept as the ``first_use`` section of fluentflow_config.json (the data folder),
so an update keeps it and removing all data clears it. The agent that installs
FluentFlow, or the one the user later tells 「带我用一次 FluentFlow」, reads it
through get_status and writes it with record_first_use: the need the user picked
(1 to 6, or their own words for "other"), the material they gave, and done once
the first delivery is made or they say they need no guide.

Who is guided: a machine whose record is not done and that had no real task
before the guide started. A machine with tasks from before (an old user
updating) is never guided; the MCP self-check's task does not count as a real
one, and neither does a task left in the platform's own data folder by an
earlier install that this one no longer reads (Windows: an install from before
the one-folder layout kept its tasks in %APPDATA%\\FluentFlow). The guide's own
task does not end it while it runs: tasks created after ``started_at`` (stamped
by the first record) count only once one has completed, so another
conversation can carry an unfinished guide on, and a guide whose delivery was
never marked done does not start again. A user who deleted every task is guided
again; that is accepted (they start over as new).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.core import job_store, local_config

SECTION = "first_use"
# scripts/check_mcp_server.py --backend-e2e submits a transcript under this title.
SELF_CHECK_TITLES = ("MCP smoke transcript",)
NEED_CHOICES = range(1, 7)
_MAX_NEED_TEXT = 200
_MAX_MATERIAL = 2000
# Every line the guide says; an agent without the skill (Cursor, any MCP-only
# client) is pointed at it by get_status.
GUIDE_PATH = Path(__file__).resolve().parents[2] / "agent" / "skills" / "fluentflow" / "first-use.md"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _section() -> dict[str, Any]:
    raw = local_config.load_config().get(SECTION)
    return dict(raw) if isinstance(raw, dict) else {}


def load() -> dict[str, Any]:
    raw = _section()
    return {
        "done": raw.get("done") is True,
        "need": raw.get("need"),
        "material": raw.get("material") or "",
        "started_at": raw.get("started_at"),
        "updated_at": raw.get("updated_at"),
    }


def status() -> dict[str, Any]:
    """The record plus should_onboard: the answer get_status carries. While the
    guide is due it also says where the guide is (guide_path) and whether the
    transcription model is on disk yet (speech_model_ready; the installer
    leaves it downloading, and false means the first recording waits for it)."""
    record = load()
    started = record["started_at"]
    record["should_onboard"] = not record["done"] and not (
        job_store.has_job_not_named(SELF_CHECK_TITLES, created_before=started)
        or (started and job_store.has_job_not_named(SELF_CHECK_TITLES, created_from=started, statuses=("completed",)))
        or _earlier_install_had_tasks(started)
    )
    if record["should_onboard"]:
        record["speech_model_ready"] = _speech_model_ready()
        if GUIDE_PATH.is_file():
            record["guide_path"] = str(GUIDE_PATH)
    return record


def _earlier_install_had_tasks(started: str | None) -> bool:
    from backend.core import runtime_paths

    earlier = runtime_paths.default_app_data_root() / "fluentflow_jobs.sqlite"
    try:
        if earlier.resolve() == Path(job_store.DEFAULT_DB_PATH).resolve():
            return False
    except OSError:
        return False
    return job_store.has_job_not_named(SELF_CHECK_TITLES, db_path=earlier, created_before=started, read_only=True)


def _speech_model_ready() -> bool | None:
    try:
        from backend.core.local_stt import planned_model
        from backend.core.local_stt_assets import is_downloaded

        return is_downloaded(planned_model())
    except Exception:  # noqa: BLE001 - a status line never fails the status
        return None


def _need(value: Any) -> int | str:
    """1 to 6 (a number or its digits), or the user's own words for "other"."""
    wrong = ValueError("need 要写 1 到 6 的数字，或者用户自己的一句话")
    if value is None or isinstance(value, bool):
        raise wrong
    if isinstance(value, (int, float)):
        if float(value).is_integer() and int(value) in NEED_CHOICES:
            return int(value)
        raise wrong
    text = str(value).strip()
    if text.isdigit():
        if int(text) in NEED_CHOICES:
            return int(text)
        raise wrong
    if not text:
        raise wrong
    if len(text) > _MAX_NEED_TEXT:
        raise ValueError(f"need 最多 {_MAX_NEED_TEXT} 个字")
    return text


def record(patch: dict[str, Any]) -> dict[str, Any]:
    """Merge the given fields (done, need, material) into the record and save it.
    A field left out keeps its value; an empty material clears it. The first
    record stamps started_at, which never moves after."""
    known = {"done", "need", "material"}
    unknown = sorted(set(patch) - known)
    if unknown:
        raise ValueError(f"不认识的字段：{', '.join(unknown)}（只有 done、need、material）")
    if not patch:
        raise ValueError("至少给一个字段：done、need 或 material")
    if "done" in patch and not isinstance(patch["done"], bool):
        raise ValueError("done 只能是 true 或 false")
    need = _need(patch["need"]) if "need" in patch else None
    material = None
    if "material" in patch:
        material = "" if patch["material"] is None else str(patch["material"]).strip()
        if len(material) > _MAX_MATERIAL:
            raise ValueError(f"material 最多 {_MAX_MATERIAL} 个字")

    def change(config: dict[str, Any]) -> dict[str, Any]:
        current = config.get(SECTION)
        current = dict(current) if isinstance(current, dict) else {}
        now = _now()
        current.setdefault("started_at", now)
        if "done" in patch:
            current["done"] = patch["done"]
        if need is not None:
            current["need"] = need
        if material is not None:
            if material:
                current["material"] = material
            else:
                current.pop("material", None)
        current["updated_at"] = now
        return {**config, SECTION: current}

    local_config.update_config(change)
    return status()
