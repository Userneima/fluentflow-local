"""The note skills: the product's default style, and each user's own.

A skill is a ``SKILL.md`` file of writing rules for the model that writes the
note. FluentFlow reads it itself and hands it to Claude on every note, on both
channels, so it is always applied. Claude Code's own skill loading is not used:
there the model decides whether to open a skill, and the API-key channel has no
such mechanism (see ``docs/方案/2026-10-09-写笔记改用skill.md``).

- ``backend/note_skills/fluentflow-note-default/SKILL.md`` ships with the product.
- The user's agent proposes preference changes and records their source.
- The user's own skill lives in the data directory, ``note_skills/mine``. When
  it exists it is used instead of the default. Every replacement keeps the
  version it replaced in ``history/``, so any change can be undone.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.core.runtime_paths import runtime_path

SKILLS_ROOT = Path(__file__).resolve().parents[1] / "note_skills"
DEFAULT_SKILL_PATH = SKILLS_ROOT / "fluentflow-note-default" / "SKILL.md"
SKILL_FILENAME = "SKILL.md"
_WRITE_LOCK = threading.RLock()


class NoteSkillConflict(ValueError):
    """The user or another agent changed the writing rules meanwhile."""


_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


@dataclass(frozen=True)
class NoteSkill:
    text: str
    path: Path
    is_default: bool

    @property
    def version(self) -> str:
        """A short fingerprint of the text, recorded on every note."""
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:12]

    @property
    def rules(self) -> str:
        """What the writing model reads: the skill without its frontmatter."""
        return _FRONTMATTER.sub("", self.text, count=1).strip()


def user_skill_dir() -> Path:
    return runtime_path("FLUENTFLOW_NOTE_SKILL_DIR", "note_skills", "mine")


def user_skill_path() -> Path:
    return user_skill_dir() / SKILL_FILENAME


def default_skill() -> NoteSkill:
    return NoteSkill(DEFAULT_SKILL_PATH.read_text(encoding="utf-8").strip(), DEFAULT_SKILL_PATH, True)


def load_note_skill() -> NoteSkill:
    """The skill the next note is written with. Read on every note, so a
    change applies without a restart; an empty or unreadable file falls back to
    the default rather than costing a note."""
    path = user_skill_path()
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    if text:
        return NoteSkill(text, path, False)
    return default_skill()


def save_user_skill(text: str, *, base_version: str | None = None) -> NoteSkill:
    """Make ``text`` the user's skill, keeping the one it replaces."""
    body = (text or "").strip()
    if not body:
        raise ValueError("笔记 skill 不能是空的")
    with _WRITE_LOCK:
        return _replace(body, base_version=base_version)


def _replace(body: str, *, base_version: str | None) -> NoteSkill:
    current = load_note_skill()
    if base_version is not None and base_version != current.version:
        raise NoteSkillConflict("笔记写法已在别处改过，请重新读取后再修改。")
    directory = user_skill_dir()
    history = directory / "history"
    history.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S-%f")
    (history / f"{stamp}-{current.version}.md").write_text(current.text + "\n", encoding="utf-8")
    target = user_skill_path()
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(body + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return load_note_skill()


def restore_user_skill(*, name: str | None = None, use_default: bool = False, base_version: str) -> NoteSkill:
    """Restore an existing snapshot, or follow the product's current default.

    No model or recording is needed. The replaced rules stay in history;
    checking the version and writing share the lock with agent updates.
    """
    if not base_version:
        raise NoteSkillConflict("请先读取当前笔记写法，再带上版本号撤回。")
    if bool(name) == bool(use_default):
        raise ValueError("请选择一个旧版本，或恢复默认写法。")
    with _WRITE_LOCK:
        if use_default:
            return _replace("", base_version=base_version)
        match = next((path for path in skill_history() if path.name == name), None)
        if match is None:
            raise ValueError("找不到这个旧版本。")
        body = match.read_text(encoding="utf-8").strip()
        if not body:
            raise ValueError("这个旧版本没有写法内容。")
        return _replace("" if body == default_skill().text else body, base_version=base_version)


def skill_history() -> list[Path]:
    """Earlier versions, newest first."""
    history = user_skill_dir() / "history"
    if not history.is_dir():
        return []
    return sorted(history.glob("*.md"), reverse=True)


__all__ = [
    "DEFAULT_SKILL_PATH",
    "NoteSkillConflict",
    "NoteSkill",
    "default_skill",
    "load_note_skill",
    "restore_user_skill",
    "save_user_skill",
    "skill_history",
    "user_skill_path",
]
