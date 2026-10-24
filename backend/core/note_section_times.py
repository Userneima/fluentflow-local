"""Which span of the recording each note section came from (decision 6.37).

The editor highlights the section being played and offers a time beside each
heading. The note is reorganised by topic and rewritten, so the match is made
by a model reading the note and the timed transcript, not by matching words.
The table is kept in the task's result under ``note_section_times``, never in
the md: the Obsidian and Feishu copies of the note are untouched.

When it is made: once a note is saved (a separate request, the note prompt is
not changed), the first time an older note is opened in the editor, and again
for a section whose heading changed. Each run asks only about the sections the
table has no entry for; a changed cut list or a different playback file makes
every time wrong, so the whole table is made again.

Times are on the timeline of the file the editor plays (the cut video when
there is one), the same timeline as ``display_segments``.

Prototype for the note-follows-playback mock-up; see
docs/交付记录/2026-10-24-笔记跟随播放样机.md.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable

from backend.core import job_store
from backend.core.result_schema import canonical_display_segments

logger = logging.getLogger(__name__)

RESULT_KEY = "note_section_times"
# One transcript line given to the model per this many seconds of talk. The
# validation run used 15: starts come out 0 to 15 seconds early.
CHUNK_SECONDS = 15.0
# Matching the table to the note waits this long after the last change, so a
# heading being typed is not sent to the model letter by letter.
QUIET_SECONDS = 60.0
# More of the note than this unmatched means it was rewritten: align it all.
REALIGN_ALL_SHARE = 0.5

PART_MARKER = re.compile(r"<!--\s*第\s*\d+/\d+\s*部分.*?-->")
HEADING = re.compile(r"^(#{2,3})\s+(.*?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")

SYSTEM = (
    "你在给一篇视频笔记的章节标出它对应录像里的哪一段。笔记是按主题重组、改写过的，"
    "字面和转录稿对不上，也不一定按录像顺序，要按意思判断。只输出要求的 JSON，不要别的文字。"
)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def note_sections(markdown: str) -> list[dict[str, Any]]:
    """The note's level-2 and level-3 headings in order, each with its parent
    and a key that names it apart from the others: level, parent, heading and
    which occurrence of that heading it is. Headings in code blocks are text."""
    sections: list[dict[str, Any]] = []
    seen: dict[tuple, int] = {}
    parent: str | None = None
    in_fence = False
    for line in PART_MARKER.sub("", markdown or "").splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING.match(line)
        if not match or not match.group(2):
            continue
        level = len(match.group(1))
        heading = match.group(2).strip()
        owner = None if level == 2 else parent
        if level == 2:
            parent = heading
        base = (level, owner, heading)
        seen[base] = seen.get(base, 0) + 1
        sections.append({
            "heading": heading,
            "level": level,
            "parent": owner,
            "key": section_key(level, owner, heading, seen[base]),
        })
    return sections


def section_key(level: int, parent: str | None, heading: str, occurrence: int = 1) -> str:
    # Written exactly as the page's JSON.stringify writes it: the page matches
    # the note it shows to the table by this text.
    return json.dumps([level, parent or "", heading, occurrence], ensure_ascii=False, separators=(",", ":"))


def _timeline(result: dict[str, Any]) -> dict[str, Any]:
    debreath = result.get("debreath") if isinstance(result.get("debreath"), dict) else {}
    kind = str(result.get("playback_media_kind") or "")
    return {
        "timeline": kind or "source",
        "cut_list_updated_at": debreath.get("cut_list_updated_at") if kind == "debreath_media" else None,
    }


def stored_table(result: dict[str, Any] | None) -> dict[str, Any] | None:
    table = (result or {}).get(RESULT_KEY)
    return table if isinstance(table, dict) else None


def _timeline_matches(table: dict[str, Any], result: dict[str, Any]) -> bool:
    now = _timeline(result)
    return table.get("timeline") == now["timeline"] and table.get("cut_list_updated_at") == now["cut_list_updated_at"]


def missing_sections(result: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """The note's sections the table has no time for, and whether the whole
    note must be matched again (no table, the timeline moved, or most of the
    note is new)."""
    sections = note_sections(str(result.get("summary_markdown") or ""))
    table = stored_table(result)
    if not sections:
        return [], False
    if not table or not _timeline_matches(table, result):
        return sections, True
    known = {row.get("key") for row in table.get("sections") or [] if isinstance(row, dict)}
    missing = [section for section in sections if section["key"] not in known]
    if len(missing) > len(sections) * REALIGN_ALL_SHARE:
        return sections, True
    return missing, False


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _parse_clock(text: Any) -> float | None:
    try:
        total = 0.0
        for part in str(text).strip().split(":"):
            total = total * 60 + float(part)
    except ValueError:
        return None
    return total


def transcript_lines(segments: list[dict[str, Any]]) -> list[tuple[float, str]]:
    """The transcript in lines of about ``CHUNK_SECONDS``, each with its start
    and the speaker changes marked."""
    lines: list[tuple[float, str]] = []
    start: float | None = None
    buf: list[str] = []
    last_speaker = None
    for seg in segments:
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        speaker = str(seg.get("speaker") or "")
        if start is None:
            start = float(seg.get("start") or 0)
        if speaker and speaker != last_speaker:
            buf.append(f"【{speaker.replace('SPEAKER_', 'S')}】")
            last_speaker = speaker
        buf.append(text)
        if float(seg.get("end") or 0) - start >= CHUNK_SECONDS:
            lines.append((start, " ".join(buf)))
            start, buf = None, []
    if buf and start is not None:
        lines.append((start, " ".join(buf)))
    return lines


def build_prompt(markdown: str, ask: list[dict[str, Any]], lines: list[tuple[float, str]], duration: float) -> str:
    """The note with the asked sections' headings tagged 〔S1〕〔S2〕…, and the
    timed transcript. Untagged headings stay as context."""
    asked = {section["key"]: f"S{index + 1}" for index, section in enumerate(ask)}
    tagged: list[str] = []
    in_fence = False
    walker = iter(note_sections(markdown))
    current = next(walker, None)
    for line in PART_MARKER.sub("", markdown).splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else HEADING.match(line)
        if match and match.group(2) and current is not None:
            tag = asked.get(current["key"])
            tagged.append(f"{line}  〔{tag}〕" if tag else line)
            current = next(walker, None)
        else:
            tagged.append(line)
    transcript = "\n".join(f"[{_clock(t)}] {text}" for t, text in lines)
    note = "\n".join(tagged)
    return (
        f"录像总长 {_clock(duration)}。下面先是笔记，要你标的标题后面用〔S数字〕标了编号；"
        "然后是带时间的转录稿，每行开头是这一行在录像里的开始时间。\n\n"
        "给每个编号的章节标出它主要对应的录像时间段（primary），start 和 end 用转录稿里出现过的行首时间，"
        "end 取这一段之后紧接的那一行的时间。一节的内容来自录像里不相邻的几处时，primary 放讲得最多的那一段，"
        "其他几处放 others；只来自一处时 others 为空。二级标题下面有三级标题时，二级标题的时间段覆盖它下面各小节的主要部分。"
        "总结性的章节（决定、待办、还没定的这类）按其中各条出处的主要那一段标，others 列其他出处。"
        "每个编号都要出现一次，没有编号的标题不用管。\n\n"
        '只输出一个 JSON 对象：{"sections": [{"id": "S1", "primary": {"start": "0:01:02", "end": "0:03:40"}, '
        '"others": [{"start": "...", "end": "..."}]}]}\n\n'
        f"=== 笔记 ===\n{note}\n\n=== 转录稿 ===\n{transcript}\n"
    )


def _span(value: Any, duration: float) -> list[float] | None:
    if not isinstance(value, dict):
        return None
    start, end = _parse_clock(value.get("start")), _parse_clock(value.get("end"))
    if start is None or end is None:
        return None
    start = max(0.0, min(start, duration))
    end = max(start, min(end, duration))
    return [round(start, 1), round(end, 1)]


def parse_answer(text: str, ask: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    """The model's spans as table rows, for the asked sections it answered."""
    raw = (text or "").strip()
    if "{" not in raw:
        raise ValueError("模型没有返回章节时间")
    payload = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    by_id = {f"S{index + 1}": section for index, section in enumerate(ask)}
    rows: list[dict[str, Any]] = []
    for item in payload.get("sections") or []:
        section = by_id.get(str((item or {}).get("id") or ""))
        primary = _span((item or {}).get("primary"), duration)
        if section is None or primary is None:
            continue
        others = [span for span in (_span(o, duration) for o in (item.get("others") or [])) if span and span[1] > span[0]]
        rows.append({
            "key": section["key"],
            "heading": section["heading"],
            "level": section["level"],
            "parent": section["parent"],
            "primary": primary,
            "others": others,
        })
    return rows


Chat = Callable[[str, str], str]


def _default_chat() -> tuple[Chat, str, str] | None:
    from backend.core import claude_code_note, claude_text

    found = claude_text.claude_chat()
    if found is None:
        return None
    chat, channel = found
    return chat, channel, claude_code_note.subscription_model()


def align(task_id: str, *, chat: tuple[Chat, str, str] | None = None) -> dict[str, Any]:
    """Give every section of the task's note that has no time one, and save
    the table. Returns what happened: ``{"state": "ready" | "unchanged" |
    "failed" | "skipped", ...}``."""
    job = job_store.get_job(task_id)
    result = dict((job or {}).get("result") or {})
    markdown = str(result.get("summary_markdown") or "")
    if not job or not markdown.strip() or str(result.get("summary_status") or "") not in {"", "completed"}:
        return {"state": "skipped"}
    segments = canonical_display_segments(result)
    if not segments:
        return {"state": "skipped"}
    ask, whole = missing_sections(result)
    table = stored_table(result)
    if not ask:
        sections = note_sections(markdown)
        current = {section["key"] for section in sections}
        kept = [row for row in (table or {}).get("sections") or [] if row.get("key") in current]
        if table and len(kept) != len(table.get("sections") or []):
            _save(task_id, result, {**table, "sections": _in_note_order(kept, sections), "note_digest": _digest(markdown)})
        return {"state": "unchanged"}
    reached = chat or _default_chat()
    if reached is None:
        failure = {**(table or {}), "error": "这台电脑上用不了 Claude，章节时间没有对上。", "failed_at": _now()}
        _save(task_id, result, failure)
        return {"state": "failed", "error": failure["error"]}
    talk, channel, model = reached
    duration = max(float(seg.get("end") or 0) for seg in segments)
    started = datetime.now()
    try:
        answer = talk(SYSTEM, build_prompt(markdown, ask, transcript_lines(segments), duration))
        rows = parse_answer(answer, ask, duration)
    except Exception as exc:  # noqa: BLE001 - the editor works without the table
        logger.warning("note section times failed for %s: %s", task_id, exc)
        failure = {**(table or {}), "error": f"章节时间没有对上：{str(exc)[:160]}", "failed_at": _now()}
        _save(task_id, result, failure)
        return {"state": "failed", "error": failure["error"]}
    sections = note_sections(markdown)
    current = {section["key"] for section in sections}
    previous = [] if whole else [row for row in (table or {}).get("sections") or [] if row.get("key") in current]
    merged = {row["key"]: row for row in [*previous, *rows]}
    saved = {
        **_timeline(result),
        "note_digest": _digest(markdown),
        "aligned_by": channel,
        "model": model,
        "aligned_at": _now(),
        "seconds": round((datetime.now() - started).total_seconds(), 1),
        "sections": _in_note_order(list(merged.values()), sections),
    }
    _save(task_id, result, saved)
    return {"state": "ready", "asked": len(ask), "answered": len(rows), "whole": whole}


def _in_note_order(rows: list[dict[str, Any]], sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {section["key"]: index for index, section in enumerate(sections)}
    return sorted(rows, key=lambda row: order.get(row.get("key"), len(order)))


def _save(task_id: str, before: dict[str, Any], table: dict[str, Any]) -> None:
    # Only this one field is written onto the result as it is now; the note,
    # its version and the task's place in the records list stay as they are.
    job_store.save_result_changes(task_id, before, {**before, RESULT_KEY: table}, touch_updated_at=False)


def status(result: dict[str, Any] | None) -> dict[str, Any]:
    """What the editor reads: the table and whether it covers the note now."""
    result = result or {}
    table = stored_table(result)
    task_id = str(result.get("task_id") or "")
    ask, _whole = missing_sections(result) if result.get("summary_markdown") else ([], False)
    if task_id in _RUNNING or task_id in _QUEUED:
        state = "aligning"
    elif table and table.get("error") and ask:
        state = "failed"
    elif not table or (table and not _timeline_matches(table, result)):
        state = "missing"
    elif ask:
        state = "partial"
    else:
        state = "ready"
    usable = table if table and _timeline_matches(table, result) else None
    return {
        "state": state,
        "sections": list((usable or {}).get("sections") or []),
        "missing": len(ask),
        "error": (table or {}).get("error") if state == "failed" else None,
        "aligned_at": (usable or {}).get("aligned_at"),
        # Which file the times are on, so the page shows them only while that
        # file is the one playing.
        "timeline": (usable or {}).get("timeline"),
    }


# One alignment at a time: each is a model call on the user's subscription.
_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="note-section-times")
_QUEUED: set[str] = set()
_RUNNING: set[str] = set()
_STATE_LOCK = threading.Lock()
_TIMERS: dict[str, threading.Timer] = {}
_enabled = False


def _run(task_id: str) -> None:
    with _STATE_LOCK:
        _QUEUED.discard(task_id)
        _RUNNING.add(task_id)
    try:
        align(task_id)
    except Exception as exc:  # noqa: BLE001 - a worker never dies on one task
        logger.warning("note section times crashed for %s: %s", task_id, exc)
    finally:
        with _STATE_LOCK:
            _RUNNING.discard(task_id)


def schedule(task_id: str) -> bool:
    """Queue an alignment now (the editor opened a note with no table).
    ``False`` when one is already queued or running."""
    with _STATE_LOCK:
        if task_id in _QUEUED or task_id in _RUNNING:
            return False
        _QUEUED.add(task_id)
    _WORKER.submit(_run, task_id)
    return True


def schedule_when_quiet(task_id: str, delay: float | None = None) -> None:
    def fire() -> None:
        with _STATE_LOCK:
            if _TIMERS.get(task_id) is timer:
                _TIMERS.pop(task_id, None)
        schedule(task_id)

    timer = threading.Timer(QUIET_SECONDS if delay is None else delay, fire)
    timer.daemon = True
    with _STATE_LOCK:
        previous = _TIMERS.get(task_id)
        if previous is not None:
            previous.cancel()
        _TIMERS[task_id] = timer
    timer.start()


def _watched(result: dict[str, Any] | None) -> tuple[Any, ...]:
    result = result or {}
    return (
        str(result.get("summary_markdown") or ""),
        result.get("summary_status"),
        *_timeline(result).values(),
    )


def _on_result_saved(task_id: str, before: dict[str, Any] | None, after: dict[str, Any] | None) -> None:
    """A note saved, a heading renamed, a cut list re-exported: once the
    changes stop, match whatever the table no longer covers. A table already
    covering every heading costs nothing (no model call)."""
    if not _enabled or not isinstance(after, dict) or _watched(before) == _watched(after):
        return
    if not str(after.get("summary_markdown") or "").strip():
        return
    if str(after.get("summary_status") or "") not in {"", "completed"}:
        return
    ask, _whole = missing_sections(after)
    if ask:
        schedule_when_quiet(task_id)


def start() -> None:
    """Called at service start: from then on saved notes are matched. Off in
    tests and scripts, which save notes without wanting a model call."""
    global _enabled
    _enabled = True


job_store.add_result_listener(_on_result_saved)


__all__ = [
    "RESULT_KEY",
    "align",
    "build_prompt",
    "missing_sections",
    "note_sections",
    "parse_answer",
    "schedule",
    "schedule_when_quiet",
    "start",
    "status",
    "transcript_lines",
]
