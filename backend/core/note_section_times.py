"""Which span of the recording each note section came from (decision 6.37).

The editor highlights the section being played and offers a time beside each
heading. The note is reorganised by topic and rewritten, so the match is made
by a model reading the note and the timed transcript, not by matching words.
The table is kept in the task's result under ``note_section_times``, never in
the md: the Obsidian and Feishu copies of the note are untouched.

When it is made: once a note is saved (a separate request, the note prompt is
not changed), the first time an older note is opened in the editor, and again
for a section whose heading changed. Each run asks only about the sections the
table has no row for; a changed cut list or a different playback file makes
every time wrong, so the whole table is made again.

How a row finds its section in a note that keeps changing (``match_rows``,
mirrored in frontend/src/lib/noteFollow.js): by level and heading text; among
several headings with the same text, by a hash of the section's own body;
what is still left over pairs up in order only when the counts agree. A
renamed heading leaves its row unmatched and only that section is asked
about again; its sub-sections keep their times; a new heading inserted with
the same text as an old one never takes the old one's time.

Times are on the timeline of the file the editor plays (the cut video when
there is one), the same timeline as ``display_segments``.

Prototype for the note-follows-playback mock-up, off unless
``FLUENTFLOW_NOTE_SECTION_TIMES`` is on; see
docs/交付记录/2026-10-24-笔记跟随播放样机.md.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable

from backend.core import job_store
from backend.core.result_schema import canonical_display_segments

logger = logging.getLogger(__name__)

RESULT_KEY = "note_section_times"
SWITCH_ENV = "FLUENTFLOW_NOTE_SECTION_TIMES"
# One transcript line given to the model per this many seconds of talk. The
# validation run used 15: starts come out 0 to 15 seconds early.
CHUNK_SECONDS = 15.0
# Matching the table to the note waits this long after the last change, so a
# heading being typed is not sent to the model letter by letter.
QUIET_SECONDS = 60.0
# After a failed match, opening the note tries again only after this long.
RETRY_AFTER_SECONDS = 600.0
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


def _age_seconds(stamp: Any) -> float | None:
    try:
        then = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    if then.tzinfo is None:
        then = then.astimezone()
    return (datetime.now().astimezone() - then).total_seconds()


def body_hash(text: str) -> str:
    """FNV-1a (32 bit) of the text's UTF-8 bytes, as 8 hex digits. The page
    computes the same (noteFollow.js); both test files pin one value."""
    value = 0x811C9DC5
    for byte in text.encode("utf-8"):
        value ^= byte
        value = (value * 0x01000193) & 0xFFFFFFFF
    return f"{value:08x}"


def note_sections(markdown: str) -> list[dict[str, Any]]:
    """The note's level-2 and level-3 headings in order, each with the hash of
    its own body: the lines up to the next level-2 or level-3 heading, part
    markers removed, trimmed. Headings in code blocks are text."""
    lines = PART_MARKER.sub("", markdown or "").splitlines()
    heads: list[tuple[int, int, str]] = []
    in_fence = False
    for number, line in enumerate(lines):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING.match(line)
        if match and match.group(2):
            heads.append((number, len(match.group(1)), match.group(2).strip()))
    sections = []
    for index, (number, level, heading) in enumerate(heads):
        end = heads[index + 1][0] if index + 1 < len(heads) else len(lines)
        body = "\n".join(lines[number + 1:end]).strip()
        sections.append({"index": index, "heading": heading, "level": level, "body_hash": body_hash(body)})
    return sections


def match_rows(rows: list[dict[str, Any]], sections: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Which table row each section of the note has, by section index."""
    groups: dict[tuple[int, str], tuple[list, list]] = {}
    for row in rows:
        if isinstance(row, dict):
            groups.setdefault((int(row.get("level") or 0), str(row.get("heading") or "")), ([], []))[0].append(row)
    for section in sections:
        group = groups.get((section["level"], section["heading"]))
        if group is not None:
            group[1].append(section)
    matched: dict[int, dict[str, Any]] = {}
    for group_rows, group_sections in groups.values():
        rows_left = list(group_rows)
        sections_left = list(group_sections)
        # Same heading and same body: pair in order only when as many rows as
        # sections share it. Otherwise one was inserted or deleted and which
        # one cannot be told (typically empty sections): none of them keeps a
        # time, so none gets a wrong one; they are asked about again.
        for digest in {section["body_hash"] for section in group_sections}:
            same_rows = [row for row in rows_left if row.get("body_hash") == digest]
            same_sections = [section for section in sections_left if section["body_hash"] == digest]
            if same_rows and len(same_rows) == len(same_sections):
                for row, section in zip(same_rows, same_sections):
                    matched[section["index"]] = row
                    rows_left.remove(row)
                    sections_left.remove(section)
            elif same_rows:
                for row in same_rows:
                    rows_left.remove(row)
                sections_left = [section for section in sections_left if section["body_hash"] != digest]
        if len(rows_left) == len(sections_left):
            for row, section in zip(rows_left, sections_left):
                matched[section["index"]] = row
    return matched


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
    """The note's sections the table has no row for, and whether the whole
    note must be matched again (no table, the timeline moved, or most of the
    note is new)."""
    sections = note_sections(str(result.get("summary_markdown") or ""))
    table = stored_table(result)
    if not sections:
        return [], False
    if not table or not _timeline_matches(table, result):
        return sections, True
    matched = match_rows(list(table.get("sections") or []), sections)
    missing = [section for section in sections if section["index"] not in matched]
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
    asked = {section["index"]: f"S{number + 1}" for number, section in enumerate(ask)}
    tagged: list[str] = []
    in_fence = False
    index = 0
    for line in PART_MARKER.sub("", markdown).splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            tagged.append(line)
            continue
        match = None if in_fence else HEADING.match(line)
        if match and match.group(2):
            tag = asked.get(index)
            tagged.append(f"{line}  〔{tag}〕" if tag else line)
            index += 1
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


def parse_answer(text: str, ask: list[dict[str, Any]], duration: float) -> dict[int, dict[str, Any]]:
    """The model's spans for the asked sections it answered, by section index."""
    import json

    raw = (text or "").strip()
    if "{" not in raw:
        raise ValueError("模型没有返回章节时间")
    payload = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    by_tag = {f"S{number + 1}": section for number, section in enumerate(ask)}
    spans: dict[int, dict[str, Any]] = {}
    for item in payload.get("sections") or []:
        section = by_tag.get(str((item or {}).get("id") or ""))
        primary = _span((item or {}).get("primary"), duration)
        if section is None or primary is None:
            continue
        others = [span for span in (_span(o, duration) for o in (item.get("others") or [])) if span and span[1] > span[0]]
        spans[section["index"]] = {"primary": primary, "others": others}
    return spans


def _row(section: dict[str, Any], spans: dict[str, Any], row_id: str | None = None) -> dict[str, Any]:
    return {
        "id": row_id or secrets.token_hex(4),
        "heading": section["heading"],
        "level": section["level"],
        "body_hash": section["body_hash"],
        "primary": spans["primary"],
        "others": spans["others"],
    }


Chat = Callable[[str, str], str]


def _default_chat() -> tuple[Chat, str, str] | None:
    from backend.core import claude_code_note, claude_text

    found = claude_text.claude_chat()
    if found is None:
        return None
    chat, channel = found
    return chat, channel, claude_code_note.subscription_model()


def align(task_id: str, *, chat: tuple[Chat, str, str] | None = None) -> dict[str, Any]:
    """Give every section of the task's note that has no row one, and save
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
        spans = parse_answer(answer, ask, duration)
    except Exception as exc:  # noqa: BLE001 - the editor works without the table
        logger.warning("note section times failed for %s: %s", task_id, exc)
        failure = {**(table or {}), "error": f"章节时间没有对上：{str(exc)[:160]}", "failed_at": _now()}
        _save(task_id, result, failure)
        return {"state": "failed", "error": failure["error"]}
    sections = note_sections(markdown)
    kept = {} if whole else match_rows(list((table or {}).get("sections") or []), sections)
    rows = []
    for section in sections:
        if section["index"] in spans:
            rows.append(_row(section, spans[section["index"]]))
        elif section["index"] in kept:
            old = kept[section["index"]]
            rows.append(_row(section, {"primary": old["primary"], "others": old.get("others") or []}, old.get("id")))
    # Sections asked about and not answered (left out, or a time that did not
    # read): kept with the time, so an open page asking again every look does
    # not send the whole transcript every 20 seconds for them.
    unanswered = [_identity(section) for section in ask if section["index"] not in spans]
    saved = {
        **_timeline(result),
        **({"unanswered": unanswered, "unanswered_at": _now()} if unanswered else {}),
        "aligned_by": channel,
        "model": model,
        "aligned_at": _now(),
        "seconds": round((datetime.now() - started).total_seconds(), 1),
        "sections": rows,
    }
    # The table describes the note as it was read above. Only this one field
    # is written onto the result as it is now, so a note saved meanwhile
    # stays; the listener then matches whatever that save changed.
    _save(task_id, result, saved)
    return {"state": "ready", "asked": len(ask), "answered": len(spans), "whole": whole}


def _identity(section: dict[str, Any]) -> list[Any]:
    return [section["level"], section["heading"], section["body_hash"]]


def _save(task_id: str, before: dict[str, Any], table: dict[str, Any]) -> None:
    job_store.save_result_changes(task_id, before, {**before, RESULT_KEY: table}, touch_updated_at=False)


def status(result: dict[str, Any] | None) -> dict[str, Any]:
    """What the editor reads: the rows, and whether they cover the note now."""
    result = result or {}
    table = stored_table(result)
    task_id = str(result.get("task_id") or "")
    ask, _whole = missing_sections(result) if result.get("summary_markdown") else ([], False)
    if task_id and (task_id in _RUNNING or task_id in _QUEUED or task_id in _TIMERS):
        state = "aligning"
    elif table and table.get("error") and ask:
        state = "failed"
    elif not table or not _timeline_matches(table, result):
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


def request(result: dict[str, Any]) -> dict[str, Any]:
    """The editor has the note open and some of its sections have no time:
    queue a match. A note saved in the editor less than ``QUIET_SECONDS`` ago
    waits for the rest of that time; after a failure, and for sections the
    model was asked about and left without a time, ``RETRY_AFTER_SECONDS``
    pass before the next try. Asking again while one waits or runs changes
    nothing, so the page may ask on every look."""
    task_id = str(result.get("task_id") or "")
    current = status(result)
    if not task_id or current["state"] not in {"missing", "partial", "failed"}:
        return current
    table = stored_table(result) or {}
    if current["state"] == "failed":
        age = _age_seconds(table.get("failed_at"))
        if age is not None and age < RETRY_AFTER_SECONDS:
            return current
    if current["state"] == "partial" and table.get("unanswered"):
        # The model was asked about exactly these and gave no time: the same
        # wait as after a failure. A section that changed since is asked at once.
        age = _age_seconds(table.get("unanswered_at"))
        unanswered = {tuple(item) for item in table["unanswered"] if isinstance(item, list)}
        ask, _whole = missing_sections(result)
        if age is not None and age < RETRY_AFTER_SECONDS and all(tuple(_identity(section)) in unanswered for section in ask):
            return current
    edited = _age_seconds(result.get("summary_edited_at"))
    wait = QUIET_SECONDS - edited if edited is not None and edited < QUIET_SECONDS else 0.0
    schedule_soon(task_id, wait)
    return status(result)


# One alignment at a time: each is a model call on the user's subscription.
_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="note-section-times")
_QUEUED: set[str] = set()
_RUNNING: set[str] = set()
_AGAIN: set[str] = set()
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
            again = task_id in _AGAIN
            _AGAIN.discard(task_id)
    if again:
        schedule(task_id)


def schedule(task_id: str) -> bool:
    """Queue a match now. Asked while one runs, another follows it: the note
    may have changed after the running one read it. ``False`` when one is
    already waiting."""
    with _STATE_LOCK:
        if task_id in _RUNNING:
            _AGAIN.add(task_id)
            return True
        if task_id in _QUEUED:
            return False
        _QUEUED.add(task_id)
    _WORKER.submit(_run, task_id)
    return True


def _start_timer(task_id: str, delay: float, *, restart: bool) -> None:
    def fire() -> None:
        with _STATE_LOCK:
            if _TIMERS.get(task_id) is timer:
                _TIMERS.pop(task_id, None)
        schedule(task_id)

    timer = threading.Timer(delay, fire)
    timer.daemon = True
    with _STATE_LOCK:
        previous = _TIMERS.get(task_id)
        if previous is not None:
            if not restart:
                return
            previous.cancel()
        _TIMERS[task_id] = timer
    timer.start()


def schedule_when_quiet(task_id: str, delay: float | None = None) -> None:
    """Match once the note has not changed for ``QUIET_SECONDS``; each change
    starts the wait again."""
    _start_timer(task_id, QUIET_SECONDS if delay is None else delay, restart=True)


def schedule_soon(task_id: str, delay: float = 0.0) -> None:
    """Match after ``delay`` unless a wait is already running (kept as it is)."""
    if delay <= 0:
        with _STATE_LOCK:
            waiting = task_id in _TIMERS
        if not waiting:
            schedule(task_id)
        return
    _start_timer(task_id, delay, restart=False)


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


def switched_on() -> bool:
    return (os.environ.get(SWITCH_ENV) or "").strip().lower() in {"1", "on", "true", "yes"}


def start() -> None:
    """Called at service start. Saved notes are matched on their own only
    when ``FLUENTFLOW_NOTE_SECTION_TIMES`` is on (the mock-up's switch for the
    service; the page's is ``?follow=on``): each match is a model call. Off in
    tests and scripts, which save notes without wanting one."""
    global _enabled
    _enabled = switched_on()


job_store.add_result_listener(_on_result_saved)


__all__ = [
    "RESULT_KEY",
    "align",
    "body_hash",
    "build_prompt",
    "match_rows",
    "missing_sections",
    "note_sections",
    "parse_answer",
    "request",
    "schedule",
    "schedule_soon",
    "schedule_when_quiet",
    "start",
    "status",
    "transcript_lines",
]
