"""The note-writing rules, read from a file each time a note is written.

The rules used to be a string in ``claude_vision``. Nobody but a code change
could improve them, and the owner's own note rules (kept in a Claude Code
skill) drifted away from them. Now they are one Markdown file:

- ``note_writing_rules.md`` beside this module is the default every install
  gets.
- ``note_writing_rules.md`` in the data directory, when it exists, replaces it
  for that machine. ``FLUENTFLOW_NOTE_RULES_PATH`` names another file instead.

The file is read on every note, so an edit applies to the next note without a
restart. What stays in code is the contract the parser depends on (which file
names exist, how a picture is cited, the JSON fields), in ``claude_vision``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from backend.core.runtime_paths import runtime_path

logger = logging.getLogger(__name__)

DEFAULT_RULES_PATH = Path(__file__).with_name("note_writing_rules.md")
RULES_FILENAME = "note_writing_rules.md"


@dataclass(frozen=True)
class NoteRules:
    text: str
    path: Path
    is_default: bool


def user_rules_path() -> Path:
    return runtime_path("FLUENTFLOW_NOTE_RULES_PATH", RULES_FILENAME)


def load_rules() -> NoteRules:
    """This machine's rules, or the default when it has none or they are empty.

    An unreadable or empty file falls back to the default with a warning, so a
    half-saved edit costs one note its custom rules rather than its note.
    """
    path = user_rules_path()
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        text = ""
    except OSError as exc:
        logger.warning("could not read note rules at %s (%s); using the default", path, exc)
        text = ""
    if text:
        return NoteRules(text=text, path=path, is_default=False)
    return NoteRules(
        text=DEFAULT_RULES_PATH.read_text(encoding="utf-8").strip(),
        path=DEFAULT_RULES_PATH,
        is_default=True,
    )


__all__ = ["DEFAULT_RULES_PATH", "NoteRules", "load_rules", "user_rules_path"]
