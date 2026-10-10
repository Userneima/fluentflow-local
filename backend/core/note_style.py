"""Turning a user's corrections into their own note skill.

The meta skill (``fluentflow-note-style-builder``) does the reading; this
module feeds it and keeps the state, in the user's skill folder:

- ``candidates.json``: rules proposed from the user's edits or from notes they
  are happy with, each waiting for a yes or a no.
- ``proposal.json``: one pending change to the skill: the new text, the rules
  it adds, and a comparison of one task's note written with the current and the
  new skill. Nothing reaches the skill until the user applies it.
- ``analyzed.json``: which edits have already been read, so a correction is
  proposed once.

Every rule goes through the user, and every change through a side-by-side
comparison before it takes effect.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.core import note_preview, note_skills
from backend.core.claude_text import claude_chat
from backend.core.job_store import list_jobs

logger = logging.getLogger(__name__)

# How much of each note goes to the meta skill. Long notes are cut at the end;
# the corrections that become rules (structure, glossary, headings) show early.
NOTE_CHARS_FOR_ANALYSIS = 12_000
MAX_PAIRS_PER_ANALYSIS = 8

_LOCK = threading.Lock()


class NoteStyleError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _path(name: str) -> Path:
    return note_skills.user_skill_dir() / name


def _read(name: str, default: Any) -> Any:
    try:
        return json.loads(_path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(name: str, value: Any) -> None:
    target = _path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)


def _chat() -> Callable[[str, str], str]:
    from backend.core.local_config import resolve_secret

    found = claude_chat(resolve_secret(None, "anthropic_api_key"))
    if found is None:
        raise NoteStyleError("整理笔记风格要用 Claude：在设置页「笔记」填入 Anthropic API Key 后再试。")
    return found[0]


def _json_from(text: str) -> Any:
    """The first JSON value in a reply, which may carry a fence or a sentence."""
    raw = (text or "").strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = raw.find(opener), raw.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except ValueError:
                continue
    raise NoteStyleError("Claude 返回的内容无法解析，这次没有提出规则。可以再试一次。")


# ── edits ───────────────────────────────────────────────────────────────────


def edited_notes(*, client_id: str | None = None) -> list[dict[str, Any]]:
    """Notes the user changed by hand, with what was generated, newest first."""
    pairs = []
    for job in list_jobs(limit=None, client_id=client_id, include_result=True):
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        original = str(result.get("summary_original_markdown") or "").strip()
        edited = str(result.get("summary_markdown") or "").strip()
        if result.get("summary_edited") and original and edited and original != edited:
            pairs.append({
                "task_id": job["task_id"],
                "title": result.get("display_title") or result.get("filename") or job.get("source_filename") or "",
                "edited_at": result.get("summary_edited_at") or job.get("updated_at"),
                "original": original,
                "edited": edited,
            })
    return pairs


def _unread(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = _read("analyzed.json", {})
    return [pair for pair in pairs if seen.get(pair["task_id"]) != pair["edited_at"]]


def _instruction(task: str, body: str) -> tuple[str, str]:
    system = note_skills.meta_skill().rules
    user = (
        f"这次做：{task}\n\n用户现在的笔记 skill：\n<skill>\n{note_skills.load_note_skill().rules}\n</skill>\n\n{body}\n\n"
        "只输出一个 JSON 数组，每项是 {\"rule\": 规则, \"evidence\": 依据, \"replaces\": 被替换的已有规则或空字符串}。"
        "没有可提的规则时输出 []。"
    )
    return system, user


def _store_candidates(items: Any, *, source: str, task_ids: list[str]) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        raise NoteStyleError("Claude 返回的内容不是规则列表，这次没有提出规则。可以再试一次。")
    added = []
    for item in items:
        if not isinstance(item, dict) or not str(item.get("rule") or "").strip():
            continue
        added.append({
            "id": uuid.uuid4().hex[:12],
            "rule": str(item["rule"]).strip(),
            "evidence": str(item.get("evidence") or "").strip(),
            "replaces": str(item.get("replaces") or "").strip(),
            "source": source,
            "task_ids": task_ids,
            "status": "pending",
            "created_at": _now(),
        })
    with _LOCK:
        _write("candidates.json", _read("candidates.json", []) + added)
    return added


def propose_from_edits(*, client_id: str | None = None, chat: Callable | None = None) -> list[dict[str, Any]]:
    """Candidate rules from the edits not read yet."""
    pairs = _unread(edited_notes(client_id=client_id))[:MAX_PAIRS_PER_ANALYSIS]
    if not pairs:
        return []
    body = "\n\n".join(
        f"<pair task=\"{pair['title']}\">\n<original>\n{pair['original'][:NOTE_CHARS_FOR_ANALYSIS]}\n</original>\n"
        f"<edited>\n{pair['edited'][:NOTE_CHARS_FOR_ANALYSIS]}\n</edited>\n</pair>"
        for pair in pairs
    )
    system, user = _instruction("一、从修改里提出候选规则", body)
    items = _json_from((chat or _chat())(system, user))
    added = _store_candidates(items, source="edits", task_ids=[pair["task_id"] for pair in pairs])
    with _LOCK:
        seen = _read("analyzed.json", {})
        seen.update({pair["task_id"]: pair["edited_at"] for pair in pairs})
        _write("analyzed.json", seen)
    return added


def propose_from_examples(notes: list[str], *, chat: Callable | None = None) -> list[dict[str, Any]]:
    """Candidate rules from notes the user is happy with: the starting point."""
    usable = [note.strip()[:NOTE_CHARS_FOR_ANALYSIS] for note in notes if note and note.strip()]
    if not usable:
        raise NoteStyleError("没有收到笔记内容。")
    body = "\n\n".join(f"<note index=\"{index}\">\n{note}\n</note>" for index, note in enumerate(usable, 1))
    system, user = _instruction("二、从满意的笔记里提炼起步风格", body)
    return _store_candidates(_json_from((chat or _chat())(system, user)), source="examples", task_ids=[])


def candidates(status: str | None = "pending") -> list[dict[str, Any]]:
    items = _read("candidates.json", [])
    return [item for item in items if status is None or item.get("status") == status]


def _set_status(ids: list[str], status: str) -> None:
    with _LOCK:
        items = _read("candidates.json", [])
        for item in items:
            if item.get("id") in ids:
                item["status"] = status
                item["decided_at"] = _now()
        _write("candidates.json", items)


def dismiss(candidate_id: str) -> None:
    _set_status([candidate_id], "dismissed")


# ── proposal: merge, compare, apply ─────────────────────────────────────────


def _merged_skill(rules: list[dict[str, Any]], chat: Callable) -> str:
    current = note_skills.load_note_skill()
    listed = "\n".join(
        f"- {item['rule']}" + (f"（替换：{item['replaces']}）" if item.get("replaces") else "") for item in rules
    )
    system = note_skills.meta_skill().rules
    user = (
        "这次做：三、把确认的规则并进用户的 skill\n\n"
        f"用户现在的笔记 skill（完整文件）：\n<skill>\n{current.text}\n</skill>\n\n"
        f"用户确认的规则：\n{listed}\n\n"
        "只输出新的完整 skill 文件，从 --- 开始，不加任何说明。"
    )
    text = (chat(system, user) or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    if not note_skills.NoteSkill(text, current.path, False).rules:
        raise NoteStyleError("Claude 没有返回新的笔记 skill，这次没有改动。可以再试一次。")
    if current.is_default:
        # The user's own copy gets its own name; the default keeps the product's.
        text = text.replace("name: fluentflow-note-default", "name: my-note-style", 1)
    return text


def proposal() -> dict[str, Any] | None:
    return _read("proposal.json", None)


def start_proposal(
    *,
    candidate_ids: list[str] | None = None,
    skill_text: str | None = None,
    task_id: str,
    client_id: str | None = None,
    chat: Callable | None = None,
    writer: Callable | None = None,
    run: Callable[[Callable[[], None]], Any] | None = None,
) -> dict[str, Any]:
    """Build a pending change and compare it on one task, in the background.

    Either ``candidate_ids`` (rules to merge in) or ``skill_text`` (a whole
    version, e.g. an earlier one to go back to). ``run`` starts the slow part;
    it defaults to a thread.
    """
    picked = [item for item in candidates() if item["id"] in (candidate_ids or [])]
    if skill_text is None and not picked:
        raise NoteStyleError("先选至少一条规则。")
    current = note_skills.load_note_skill()
    record = {
        "id": uuid.uuid4().hex[:12],
        "status": "running",
        "candidate_ids": [item["id"] for item in picked],
        "rules": [item["rule"] for item in picked],
        "task_id": task_id,
        "base_version": current.version,
        "created_at": _now(),
    }
    _write("proposal.json", record)

    def work() -> None:
        try:
            text = skill_text if skill_text is not None else _merged_skill(picked, chat or _chat())
            new = note_skills.NoteSkill(text.strip(), current.path, False)
            old_draft, new_draft = note_preview.compare(
                task_id, current.rules, new.rules, client_id=client_id, writer=writer,
            )
            record.update({
                "status": "ready",
                "skill_text": new.text,
                "current_text": current.text,
                "old": {"markdown": old_draft.markdown, "cited": old_draft.cited},
                "new": {"markdown": new_draft.markdown, "cited": new_draft.cited},
                "finished_at": _now(),
            })
        except Exception as exc:  # noqa: BLE001 - shown to the user on the page
            logger.warning("note style proposal failed", exc_info=True)
            record.update({"status": "failed", "error": str(exc), "finished_at": _now()})
        _write("proposal.json", record)

    (run or (lambda fn: threading.Thread(target=fn, daemon=True).start()))(work)
    return record


def apply_proposal() -> note_skills.NoteSkill:
    """Make the compared version the user's skill."""
    record = proposal()
    if not record or record.get("status") != "ready":
        raise NoteStyleError("没有可以生效的改动。")
    if record.get("base_version") != note_skills.load_note_skill().version:
        raise NoteStyleError("笔记 skill 在对比之后又变过，这份对比已经不准了。请重新生成对比。")
    skill = note_skills.save_user_skill(record["skill_text"])
    _set_status(record.get("candidate_ids") or [], "accepted")
    _path("proposal.json").unlink(missing_ok=True)
    return skill


def discard_proposal() -> None:
    _path("proposal.json").unlink(missing_ok=True)


__all__ = [
    "NoteStyleError",
    "apply_proposal",
    "candidates",
    "discard_proposal",
    "dismiss",
    "edited_notes",
    "proposal",
    "propose_from_edits",
    "propose_from_examples",
    "start_proposal",
]
