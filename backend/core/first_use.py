"""Whether this machine has had its guided first use, and what the user chose.

Kept as the ``first_use`` section of fluentflow_config.json (the data folder),
so an update keeps it and removing all data clears it. The agent that installs
FluentFlow, or the one the user later tells 「带我用一次 FluentFlow」, reads it
through get_status and writes it with record_first_use: the need the user picked
(1 to 6, or their own words for "other"), the material they gave, and done once
the first delivery is made or they say they need no guide.

Who is guided: a machine whose record is not done and that has no real task. A
machine with tasks from before this existed (an old user updating) is never
guided; the MCP self-check's task does not count as a real one.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.core import job_store, local_config

SECTION = "first_use"
# scripts/check_mcp_server.py --backend-e2e submits a transcript under this title.
SELF_CHECK_TITLES = ("MCP smoke transcript",)
NEED_CHOICES = range(1, 7)
_MAX_NEED_TEXT = 200
_MAX_MATERIAL = 2000


def load() -> dict[str, Any]:
    raw = local_config.load_config().get(SECTION)
    raw = raw if isinstance(raw, dict) else {}
    return {
        "done": raw.get("done") is True,
        "need": raw.get("need"),
        "material": raw.get("material") or "",
        "updated_at": raw.get("updated_at"),
    }


def status() -> dict[str, Any]:
    """The record plus should_onboard: the answer get_status carries. While the
    guide is due, speech_model_ready says whether the transcription model is
    on disk yet (the installer leaves it downloading): false means the first
    recording waits for it, and the guide's first line says so."""
    record = load()
    record["should_onboard"] = not record["done"] and not job_store.has_job_not_named(SELF_CHECK_TITLES)
    if record["should_onboard"]:
        record["speech_model_ready"] = _speech_model_ready()
    return record


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
    if isinstance(value, bool):
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
    A field left out keeps its value; an empty material clears it."""
    known = {"done", "need", "material"}
    unknown = sorted(set(patch) - known)
    if unknown:
        raise ValueError(f"不认识的字段：{', '.join(unknown)}（只有 done、need、material）")
    if not patch:
        raise ValueError("至少给一个字段：done、need 或 material")
    current = local_config.load_config().get(SECTION)
    current = dict(current) if isinstance(current, dict) else {}
    if "done" in patch:
        if not isinstance(patch["done"], bool):
            raise ValueError("done 只能是 true 或 false")
        current["done"] = patch["done"]
    if "need" in patch:
        current["need"] = _need(patch["need"])
    if "material" in patch:
        material = "" if patch["material"] is None else str(patch["material"]).strip()
        if len(material) > _MAX_MATERIAL:
            raise ValueError(f"material 最多 {_MAX_MATERIAL} 个字")
        if material:
            current["material"] = material
        else:
            current.pop("material", None)
    current["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    local_config.save_section(SECTION, current)
    return status()
