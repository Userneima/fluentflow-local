"""What a text model is told when it writes a note, decided here only.

Requirement (owner, 2026-11-06, docs/方案/2026-11-06-文字模型和Claude共用一份笔记写法.md):
the three writers (Claude with the frames, the user's agent, a text model
reading the transcript) write from one set of rules. Claude and the agent
read the note skill (the default one, or the user's own copy carrying the
rules ``apply_note_rule`` added) plus the series' correction documents; the
text model now reads the same, plus a short part saying it has no pictures.
Before this the text model had prompts of its own in ``ai_prompts``, and every
change to the skill (meetings' 还没定的, 「（存疑）」, a user's rule) missed it.

Every text-model note goes through ``for_text_model``: the first note after
processing, each chapter and the stitching of a long one, the coverage check,
a rewrite from the page or an agent, and a rule tried out on a part
(``tests/test_note_prompt.py`` holds them to it).
"""

from __future__ import annotations

from typing import Any

from backend.core import correction_notes, note_skills

# Said after the skill: the parts of it about pictures and the structured
# answer do not apply to a writer that gets the transcript alone.
TEXT_ONLY = """## 这一次只有转录文字（固定要求）

你这次只拿到这段录像的转录文字，没有截图。

- 上面「画面」一节不适用：不引用图片，不写只有画面上才有的内容。转录稿和截图都定不下来的名字照「转录稿的错」标「（存疑）」，这里就只看转录稿。
- 上面说的 basis_note 不用写；音质、杂音、用了哪个模型、处理过程这类说明不写进笔记。
- 只输出笔记本身：第一行就是笔记的一级标题，前面不加任何说明，不用代码围栏包住整篇。"""

# The line md, PDF and Feishu copies open with when a text model wrote the
# note (decision 5.3: a note written without the pictures says so). One exact
# line, so taking an edited copy back can take it out again.
TEXT_MODEL_LINE = "> 这篇笔记由文字模型按转录稿写，没看画面。"

# How the task records that a text model wrote its note in place of the
# frame note, and that one was rewritten by a text model on request; the
# page's 笔记说明 and the copies' line read these (``summary_written_from``,
# which every writer sets, so it is never left over from an earlier note).
TEXT_FALLBACK = "text_fallback"
TEXT_REGENERATION = "text_regeneration"


def for_text_model(
    task_id: str | None = None,
    client_id: str | None = None,
    *,
    rules: str | None = None,
    extra: str | None = None,
) -> str:
    """The rules a text model writes a note with.

    ``rules`` writes with a given skill text (a rule being tried out);
    otherwise the one in effect. ``extra`` is what was asked for this one
    note (an agent's ``system_prompt``), after the rules, not instead of
    them. With a task, its series' correction documents follow."""
    text = rules if rules is not None else note_skills.load_note_skill().rules
    parts = [text.strip(), TEXT_ONLY]
    if extra and extra.strip():
        parts.append("## 这一篇另外的要求\n\n" + extra.strip())
    body = "\n\n".join(parts)
    if task_id:
        body += correction_notes.as_section(correction_notes.for_task(task_id, client_id))
    return body


def transcript_for_note(result: dict[str, Any], *, copy: str | None = None) -> tuple[str, bool]:
    """The transcript a text model writes from, and whether its lines carry
    speaker labels: the corrected one when there is one, as「说话人 A：」lines
    when two or more people were told apart. The first note and every
    rewrite read it from here, so a rewrite no longer loses who said what.

    ``copy`` names which of the task's two copies to read, ``"original"`` or
    ``"corrected"``: a rewrite from the page reads the one whose text the
    page sent, with that copy's own lines."""
    from backend.core.speaker_diarization import build_speaker_annotated_transcript

    if copy == "original":
        transcript = str(result.get("transcript_text") or "")
        segments = result.get("raw_segments") or []
    elif copy == "corrected":
        transcript = str(result.get("corrected_transcript_text") or "")
        segments = result.get("corrected_segments") or []
    elif result.get("transcript_edited"):
        # Saved from the editor: the user's text is the newest, and the save
        # does not touch the corrected copy.
        transcript = str(result.get("transcript_text") or "")
        segments = result.get("raw_segments") or []
    else:
        transcript = str(result.get("corrected_transcript_text") or result.get("transcript_text") or "")
        segments = result.get("corrected_segments") or result.get("raw_segments") or []
    speaker_text = build_speaker_annotated_transcript(segments if isinstance(segments, list) else [])
    return (speaker_text, True) if speaker_text else (transcript, False)


def written_by_text_model(result: dict[str, Any] | None) -> bool:
    """Whether the task's note is one a text model wrote without the pictures,
    in place of the frame note or as a rewrite. A transcript file's note is
    not marked: it had no pictures to see."""
    result = result or {}
    stamp = str(result.get("summary_written_from") or "")
    if stamp:
        return stamp in (TEXT_FALLBACK, TEXT_REGENERATION)
    return result.get("note_written_by") == TEXT_FALLBACK


def with_text_model_line(markdown: str, result: dict[str, Any] | None) -> str:
    """``markdown`` with the text-model line under its title, when a text
    model wrote it; unchanged otherwise or when the line is already there."""
    if not written_by_text_model(result) or not markdown.strip() or TEXT_MODEL_LINE in markdown:
        return markdown
    lines = markdown.split("\n")
    at = 1 if lines and lines[0].startswith("# ") else 0
    return "\n".join([*lines[:at], "", TEXT_MODEL_LINE, "", *lines[at:]]).replace("\n\n\n", "\n\n").lstrip("\n")


def without_text_model_line(markdown: str) -> str:
    """A copy taken back (Obsidian, Feishu) without the line the copy was
    given, so it never becomes part of the task's note."""
    if TEXT_MODEL_LINE not in markdown:
        return markdown
    kept = [line for line in markdown.split("\n") if line.strip() != TEXT_MODEL_LINE]
    text = "\n".join(kept)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text


__all__ = [
    "TEXT_FALLBACK", "TEXT_MODEL_LINE", "TEXT_REGENERATION", "TEXT_ONLY", "for_text_model", "transcript_for_note",
    "with_text_model_line", "without_text_model_line", "written_by_text_model",
]
