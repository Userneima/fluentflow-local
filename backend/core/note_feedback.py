"""What the user said about a note, and the rules their agent wrote from it.

The user points at a problem (usually a screenshot sent to their agent). The
agent fixes that note, decides whether the problem would come back, and if so
drafts a rule, rewrites the affected part several times with it, has a separate
checker say whether the problem is still there, and only then writes the rule
into the user's skill. The user is told afterwards; they step in only when the
same problem is reported again.

Two records live beside the user's skill:

- ``feedback.json``: every reported problem, with a snapshot of the stretch of
  transcript it came from and what counts as fixed. Each one is a test case: a
  later rule change reruns them so a fixed problem cannot quietly come back.
- ``rules_log.json``: every rule written into the skill, with the feedback it
  came from and how it was verified, so the settings page can say where each
  rule came from and the skill version to go back to.

Whether a new report is "the same problem" as an old one is a judgement about
meaning, so it is left to the agent reading ``list_feedback``; nothing here
matches text.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.core import note_skills

FEEDBACK_FILE = "feedback.json"
RULES_LOG_FILE = "rules_log.json"
KINDS = ("one_off", "pattern")


class NoteFeedbackError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _path(name: str) -> Path:
    return note_skills.user_skill_dir() / name


# Record and rule writes read a list, add to it and write it back; two agents
# doing that at once lost one entry (audit, 2026-10-18).
_LOCK = threading.RLock()


def _read(name: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(_path(name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _write(name: str, items: list[dict[str, Any]]) -> None:
    target = _path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)


def _text(value: Any) -> str:
    return str(value or "").strip()


# The frozen transcript of each case, one file per feedback id. An answer names
# the file instead of carrying the text: a part is about 15,000 characters, so a
# list of a few cases ran past what an agent takes in one tool result.
EXCERPT_DIR = "feedback_excerpts"


def _excerpt_path(item_id: str) -> Path:
    return _path(EXCERPT_DIR) / f"{item_id}.txt"


def public_view(item: dict[str, Any]) -> dict[str, Any]:
    """A case as an answer shows it: the transcript by its file, not inline."""
    view = {key: value for key, value in item.items() if key != "transcript_excerpt"}
    excerpt = _text(item.get("transcript_excerpt"))
    view["transcript_excerpt_chars"] = len(excerpt)
    if excerpt and item.get("id"):
        path = _excerpt_path(str(item["id"]))
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(excerpt + "\n", encoding="utf-8")
        view["transcript_excerpt_file"] = str(path)
    return view


def record_feedback(
    *,
    task_id: str,
    problem: str,
    kind: str,
    criterion: str = "",
    note_excerpt: str = "",
    part: int | None = None,
    transcript_excerpt: str = "",
    title: str = "",
) -> dict[str, Any]:
    """Keep one reported problem as a test case."""
    if not _text(problem):
        raise NoteFeedbackError("要写明用户指出的问题（problem）")
    if kind not in KINDS:
        raise NoteFeedbackError("kind 只能是 one_off（一次性的错）或 pattern（会反复出现的写法问题）")
    if kind == "pattern" and not _text(criterion):
        raise NoteFeedbackError("写法问题要写明怎么算改好（criterion），检查者按它判断")
    if not _text(transcript_excerpt) and _text(task_id):
        transcript_excerpt = part_transcript(task_id, part or 1)
    item = {
        "id": uuid.uuid4().hex[:12],
        "created_at": _now(),
        "task_id": _text(task_id),
        "title": _text(title),
        "part": part,
        "kind": kind,
        "problem": _text(problem),
        "criterion": _text(criterion),
        "note_excerpt": _text(note_excerpt),
        # The input the case is rerun on. Frozen here rather than re-read from the
        # task later: the task's transcript can be edited or retranscribed, and a
        # test whose input moves is not the same test.
        "transcript_excerpt": _text(transcript_excerpt),
        "rule_id": None,
    }
    with _LOCK:
        items = _read(FEEDBACK_FILE)
        items.append(item)
        _write(FEEDBACK_FILE, items)
    return item


def part_transcript(task_id: str, part: int = 1, *, client_id: str | None = None) -> str:
    """The transcript of one part, on the clock the note was written from."""
    from backend.core import agent_note

    job = agent_note._job(task_id, client_id)
    _media, parts, _segments = agent_note._eligible(task_id, job)
    if not 1 <= part <= len(parts):
        raise NoteFeedbackError(f"这段录像分成 {len(parts)} 部分，part 要在 1 到 {len(parts)} 之间")
    return parts[part - 1].text


def draft_part(task_id: str, *, rules: str, part: int = 1, client_id: str | None = None) -> dict[str, Any]:
    """One part of the note written by the product's own writer with ``rules``.

    For verifying a candidate rule when FluentFlow, not the agent, writes the
    user's notes. Nothing is stored, and the frames are the ones already on
    disk, so the draft sees the same pictures the task's note did.
    """
    from backend.core import claude_vision, note_preview, visual_note_channel
    from backend.core import visual_note_job as vn

    if not _text(rules):
        raise NoteFeedbackError("要给出候选写法的全文（rules）")
    job = vn.get_job(task_id, client_id=client_id)
    if not job:
        raise NoteFeedbackError("任务不存在")
    channel = visual_note_channel.resolve_channel(None)
    if not channel.available:
        raise NoteFeedbackError(f"{channel.unavailable_reason}。没有可用的模型时，由你（agent）按候选写法自己写这一部分。")
    media, transcript, _timeline, _segments = vn._require_eligible(task_id, job, api_key=None, channel=channel)
    # Split the way the agent's material is split: the agent names a part by the
    # number get_agent_note_material gave it, and feedback freezes that part's
    # transcript, so part 3 here has to be the same stretch as part 3 there.
    from backend.core import agent_note

    parts = agent_note._parts(transcript, small=agent_note._small_parts(task_id))
    if parts[0].total == 1 and len(parts[0].text) > claude_vision.MAX_TRANSCRIPT_CHARS:
        raise NoteFeedbackError(
            "这段录像整段太长，FluentFlow 的模型一次写不完。先用 get_agent_note_material 带 small_parts=true "
            "取一次材料（分成小段），再用出问题那一段的 part 调用。"
        )
    if not 1 <= part <= len(parts):
        raise NoteFeedbackError(f"这段录像分成 {len(parts)} 部分，part 要在 1 到 {len(parts)} 之间")
    chosen = parts[part - 1]
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    frames = note_preview._existing_frames(task_id, result) if media.has_video else []
    draft = vn._checked_draft(channel.write, chosen, claude_vision.frames_for_part(frames, chosen),
                              api_key=None, rules=rules)
    return {"task_id": task_id, "part": chosen.index, "total_parts": chosen.total,
            "markdown": draft.markdown, "model": draft.model}


def list_feedback() -> list[dict[str, Any]]:
    """Every reported problem, newest first."""
    return list(reversed(_read(FEEDBACK_FILE)))


def apply_rule(
    *,
    skill_text: str,
    rule: str,
    feedback_ids: list[str],
    rounds: int,
    evidence: str,
    base_version: str | None = None,
) -> dict[str, Any]:
    """Write a verified rule into the user's skill and say where it came from.

    ``base_version`` is the skill version the agent read and merged into; when
    the skill changed since (the user edited it, another agent added a rule),
    the write is refused instead of replacing that change.

    ``skill_text`` is the whole new skill (the agent merges the rule where it
    belongs instead of appending, so rules do not pile up as an afterthought
    list). The skill it replaces is kept in the skill's history, and its version
    is recorded here so the settings page can offer to go back to it.
    """
    if not _text(rule):
        raise NoteFeedbackError("要写明这次加进去的规则（rule）")
    if int(rounds or 0) < 1:
        raise NoteFeedbackError("规则要先验证过才能写进去（rounds 至少 1）")
    known = {item["id"]: item for item in _read(FEEDBACK_FILE)}
    missing = [fid for fid in feedback_ids if fid not in known]
    if not feedback_ids or missing:
        raise NoteFeedbackError(f"规则要对应已记录的反馈（feedback_ids），找不到：{', '.join(missing) or '没有给'}")
    with _LOCK:
        return _apply_rule_locked(skill_text, rule, feedback_ids, rounds, evidence, base_version)


def _apply_rule_locked(skill_text, rule, feedback_ids, rounds, evidence, base_version):
    before = note_skills.load_note_skill()
    if base_version and base_version != before.version:
        raise NoteFeedbackError(
            "笔记规则在你读取之后被改过（用户自己改了，或别的 agent 加了规则）。"
            "先用 get_note_skill 读最新的规则，把这条规则合进去，再带上新的版本号提交。"
        )
    after = note_skills.save_user_skill(skill_text)
    entry = {
        "id": uuid.uuid4().hex[:12],
        "applied_at": _now(),
        "rule": _text(rule),
        "feedback_ids": list(feedback_ids),
        "rounds": int(rounds),
        "evidence": _text(evidence),
        "skill_version_before": before.version,
        "skill_was_default": before.is_default,
        "skill_version_after": after.version,
    }
    log = _read(RULES_LOG_FILE)
    log.append(entry)
    _write(RULES_LOG_FILE, log)
    items = _read(FEEDBACK_FILE)
    for item in items:
        if item.get("id") in feedback_ids:
            item["rule_id"] = entry["id"]
    _write(FEEDBACK_FILE, items)
    return {"rule": entry, "skill": {"version": after.version, "path": str(after.path)}}


def rules_log() -> list[dict[str, Any]]:
    """Every rule written from feedback, newest first, with its feedback attached."""
    feedback = {item["id"]: item for item in _read(FEEDBACK_FILE)}
    entries = []
    for entry in reversed(_read(RULES_LOG_FILE)):
        entries.append({
            **entry,
            "feedback": [
                {key: feedback[fid].get(key) for key in ("id", "created_at", "title", "problem")}
                for fid in entry.get("feedback_ids") or [] if fid in feedback
            ],
        })
    return entries


__all__ = [
    "KINDS",
    "NoteFeedbackError",
    "apply_rule",
    "draft_part",
    "list_feedback",
    "part_transcript",
    "record_feedback",
    "rules_log",
]
