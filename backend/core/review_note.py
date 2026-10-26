"""The review note: a second note, reorganised by topic, written from the
follow-along note the user has corrected (decision 5.37).

The follow-along note keeps the speaker's order for reading beside the video.
The review note is for learning it again later: the main line first, each
point once, the user's own question boxes folded into the point they ask
about, the speaker's predictions marked for checking again, a term list,
self-test questions and what is still unclear. How it is written lives in
``backend/note_skills/fluentflow-review-note/SKILL.md``.

The base is the follow-along md on disk, not the note FluentFlow holds: the
user corrects it in Obsidian, and a folder moved elsewhere (decision 6.34) is
never synced back. The transcript is only for checking facts and finding each
point's moment. Times come from the note-follow table (``note_section_times``)
when the task has one, on the timeline of the cut video.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from backend.core import correction_notes, job_store, model_usage, note_section_times, task_steps
from backend.core.result_schema import canonical_display_segments

logger = logging.getLogger(__name__)

Chat = Callable[[str, str], str]
RESULT_KEY = "review_note"
# The answer is long (a third to a half of the base note); a key's default
# answer length would cut it off partway.
ANSWER_MAX_TOKENS = 64_000
# Headings the note must end with; one missing means the answer was cut off.
REQUIRED_HEADINGS = ("## 主线", "## 术语表", "## 自测题", "## 还没弄清的", "## 出处信息")

SKILL_PATH = Path(__file__).resolve().parent.parent / "note_skills" / "fluentflow-review-note" / "SKILL.md"
REVIEW_SUFFIX = "_复习笔记"
NOTE_SUFFIX = "_笔记"

# ``<名>_笔记.md``, ``<名>_笔记 (2).md``: the follow-along note as the output
# folder names it (a later task on a recording that sits in its own folder
# carries `` (2)``, `` (3)``...), and the review note beside it.
_NOTE_NAME = re.compile(r"^(?P<stem>.+)_笔记(?P<suffix>(?: \(\d+\))?)$")
_REVIEW_NAME = re.compile(r"^(?P<stem>.+)_复习笔记(?P<suffix>(?: \(\d+\))?)$")
# Characters Obsidian cannot follow in a link's file or heading part.
_LINK_UNSAFE = re.compile(r"[\[\]|#^]")
_H2_NUMBER = re.compile(r"^([一二三四五六七八九十百零]+)、")
_H3_NUMBER = re.compile(r"^(（[一二三四五六七八九十]+）)")
_SRT_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{3})")


def skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def review_path_for(note_path: Path) -> Path:
    """``<名>_笔记 (2).md`` → ``<名>_复习笔记 (2).md``, in the same folder,
    named the way the output folder names a task's files."""
    found = _NOTE_NAME.match(note_path.stem)
    if found:
        return note_path.with_name(f"{found['stem']}{REVIEW_SUFFIX}{found['suffix']}.md")
    return note_path.with_name(f"{note_path.stem}{REVIEW_SUFFIX}.md")


def base_paths_for(review_path: Path) -> list[Path]:
    """The follow-along notes a review note may have been written from, the
    output folder's name first."""
    found = _REVIEW_NAME.match(review_path.stem)
    if not found:
        return []
    return [review_path.with_name(f"{found['stem']}{NOTE_SUFFIX}{found['suffix']}.md"),
            review_path.with_name(f"{found['stem']}{found['suffix']}.md")]


def base_version(note_path: Path, text: str) -> dict[str, Any]:
    """Which follow-along note the review note was written from."""
    stat = note_path.stat()
    return {
        "path": str(note_path),
        "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def _chapter_label(h2: str, h3: str | None) -> str:
    match = _H2_NUMBER.match(h2)
    label = f"第{match.group(1)}章" if match else h2
    if h3:
        sub = _H3_NUMBER.match(h3)
        label += sub.group(1) if sub else f"·{h3}"
    return label


def _clock(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def section_table(note_markdown: str, note_stem: str, time_rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Every chapter and sub-chapter of the base note: an id the model cites
    (``〔S12 1:05:20〕``), the Obsidian link that replaces it, a label (第五章,
    第五章（二）) and its time span. The model never copies the link itself:
    the note's file name is long and a hundred copies of it cost output and
    invite typos.

    A sub-chapter is linked through its chapter (``#章#节``) so that two
    chapters each with a 「（一）主线」 do not land on the same heading."""
    sections = note_section_times.note_sections(note_markdown)
    matched = note_section_times.match_rows(list(time_rows or []), sections)
    table: list[dict[str, Any]] = []
    current_h2 = ""
    for number, section in enumerate(sections, start=1):
        heading = section["heading"]
        if section["level"] == 2:
            current_h2 = heading
            target, label = heading, _chapter_label(heading, None)
        else:
            target = f"{current_h2}#{heading}" if current_h2 else heading
            label = _chapter_label(current_h2, heading) if current_h2 else heading
        row = matched.get(section["index"]) or {}
        primary = row.get("primary") if isinstance(row.get("primary"), list) else None
        # A heading Obsidian cannot follow gets the words and the time, never
        # a link that opens nothing.
        parts = [note_stem, heading] + ([current_h2] if section["level"] == 3 and current_h2 else [])
        linkable = not any(_LINK_UNSAFE.search(part) for part in parts)
        table.append({
            "id": f"S{number}",
            "level": section["level"],
            "heading": heading,
            "label": label,
            "link": f"[[{note_stem}#{target}|{label}]]" if linkable else None,
            "start": float(primary[0]) if primary else None,
            "end": float(primary[1]) if primary else None,
            "others": [list(span) for span in (row.get("others") or []) if isinstance(span, list)],
        })
    return table


def srt_segments(path: Path) -> list[dict[str, Any]]:
    """Segments from an srt file: the transcript beside a moved folder, when
    the task cannot be found."""
    segments: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8", errors="replace")):
        lines = [line.strip() for line in block.strip().splitlines()]
        for index, line in enumerate(lines):
            match = _SRT_TIME.search(line)
            if match:
                g = [int(x) for x in match.groups()]
                start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
                end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
                text = " ".join(lines[index + 1:]).strip()
                if text:
                    segments.append({"start": start, "end": end, "text": text})
                break
    return segments


def build_prompt(
    *,
    note_markdown: str,
    note_name: str,
    table: list[dict[str, Any]],
    transcript: list[tuple[float, str]],
    version: dict[str, Any],
    playback_name: str,
    glossary: str = "",
) -> tuple[str, str]:
    """The system prompt (the writing rules and the series' word list) and the
    material."""
    rows = []
    for row in table:
        span = f"{_clock(row['start'])}–{_clock(row['end'])}" if row["start"] is not None else "时间未知"
        others = "；".join(f"{_clock(a)}–{_clock(b)}" for a, b in row["others"])
        indent = "  " if row["level"] == 3 else ""
        rows.append(f"{indent}- 〔{row['id']}〕 {row['label']}　{row['heading']}　{span}"
                    + (f"（其他出处 {others}）" if others else ""))
    lines = "\n".join(f"[{_clock(start)}] {text}" for start, text in transcript)
    material = (
        f"# 材料一：底稿（随看笔记 {note_name}，修改于 {version['modified_at']}）\n\n"
        f"{note_markdown.strip()}\n\n"
        "# 材料二：章节表（出处写成〔编号 时间〕；时间是剪后视频上的时间）\n\n"
        + "\n".join(rows)
        + f"\n\n# 材料三：转录稿（剪后视频 {playback_name} 上的时间，每行约 15 秒）\n\n{lines}\n\n"
        f"底稿约 {len(note_markdown)} 字。复习笔记全文写 {len(note_markdown) // 3} 到 {len(note_markdown) // 2} 字，"
        f"其中各章正文（不算术语表、自测题和最后两节）合计不超过 {len(note_markdown) * 3 // 8} 字。"
        "按写法写这条录像的复习笔记，只输出笔记本身。"
    )
    return skill_text() + correction_notes.as_section(glossary), material


# 〔S12〕, 〔S12 1:05:20〕, and the forms a model slips into: 〔S12，1:05:20〕,
# 〔S12 1:05:20–1:06:00〕 (the start is kept).
_TIME = r"\d{1,2}:\d{2}(?::\d{2})?"
_CITE = re.compile(rf"〔\s*(S\d+)\s*(?:[，,、\s]\s*({_TIME})(?:\s*[–—\-~～至]\s*{_TIME})?)?\s*〕")
_LEFTOVER_CITE = re.compile(r"〔\s*S\d+[^〕]*〕")
UNKNOWN_SOURCE = "（出处待核）"


def expand_sources(text: str, table: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """``〔S12 1:05:20〕`` → the chapter's Obsidian link and the time; without a
    time, the chapter's start; a chapter Obsidian cannot link to gets its
    label and the time. An id the table does not have, or a citation that
    cannot be read, becomes 「（出处待核）」 and is returned, so no invented
    source is written as if it were real."""
    by_id = {row["id"]: row for row in table}
    unknown: list[str] = []

    def swap(match: re.Match[str]) -> str:
        row = by_id.get(match.group(1))
        if row is None:
            unknown.append(match.group(0))
            return UNKNOWN_SOURCE
        moment = match.group(2) or _clock(row["start"])
        # Right after another link (「在 [[#知识点]]〔S9〕」) the two would run together.
        lead = "，" if match.start() > 0 and text[match.start() - 1] in "]〕" else ""
        return f"{lead}{row['link'] or row['label']} {moment}".rstrip()

    expanded = _CITE.sub(swap, text)

    def leftover(match: re.Match[str]) -> str:
        unknown.append(match.group(0))
        return UNKNOWN_SOURCE

    return _LEFTOVER_CITE.sub(leftover, expanded), unknown


_LINK = re.compile(rf"(?P<lead>(?<=\])，)?(?P<link>\[\[[^\]]+\]\])(?: (?P<time>{_TIME}))?")


def collapse_sources(text: str, table: list[dict[str, Any]]) -> tuple[str, dict[str, str]]:
    """The reverse of ``expand_sources``, so a model revising the note handles
    short ids instead of long file names: each source becomes
    ``〔S12 1:05:20〕`` again, and ``expand_sources`` gives back the same text.

    A link that is not one of today's sources (the follow-along note's
    heading was renamed since) is swapped for ``〔L1〕`` and returned, to be
    put back untouched after the model has answered. Links inside the note
    (``[[#…]]``) stay as they are."""
    by_link = {row["link"]: row["id"] for row in table if row["link"]}
    kept: dict[str, str] = {}

    def swap(match: re.Match[str]) -> str:
        link, moment = match.group("link"), match.group("time")
        found = by_link.get(link)
        if found is not None:
            return f"〔{found}{' ' + moment if moment else ''}〕"
        if link.startswith("[[#"):
            return match.group(0)
        key = f"〔L{len(kept) + 1}〕"
        kept[key] = link + (f" {moment}" if moment else "")
        return (match.group("lead") or "") + key

    collapsed = _LINK.sub(swap, text)
    labels = [row["label"] for row in table]
    plain = {row["label"]: row["id"] for row in table if not row["link"] and labels.count(row["label"]) == 1}
    if plain:
        pattern = re.compile(
            rf"(?P<lead>(?<=\])，)?(?P<label>{'|'.join(map(re.escape, sorted(plain, key=len, reverse=True)))}) (?P<time>{_TIME})"
        )
        collapsed = pattern.sub(lambda m: f"〔{plain[m.group('label')]} {m.group('time')}〕", collapsed)
    return collapsed, kept


class ReviewNoteError(RuntimeError):
    """A reason the review note was not written, in words for the user.
    ``code`` is ``already_running`` when another run on the same note is under
    way, else ``review_note_failed``."""

    def __init__(self, message: str, code: str = "review_note_failed") -> None:
        super().__init__(message)
        self.code = code


_running: set[str] = set()
_running_lock = threading.Lock()


def _job_note_path(job: dict[str, Any]) -> Path | None:
    record = (job.get("result") or {}).get("output_folder")
    path = str((record or {}).get("md_path") or "") if isinstance(record, dict) else ""
    return Path(path) if path and Path(path).is_file() else None


def _real_md(raw: str | Path) -> Path:
    """The file a path really points at, which must be an md in the user's
    home: an agent names a path, and the note's whole text goes to the
    model and a file is written beside it."""
    try:
        path = Path(raw).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ReviewNoteError(f"找不到这个文件：{raw}") from exc
    if not path.is_file() or path.suffix.lower() != ".md":
        raise ReviewNoteError(f"不是一份 md 文件：{path}")
    if not path.is_relative_to(Path.home().resolve()):
        raise ReviewNoteError(f"只能用你用户目录里的笔记：{path}")
    return path


def _checked_base(raw: str | Path) -> Path:
    """A follow-along note: named the way the output folder names one, or in
    a folder FluentFlow made (it holds ``.fluentflow-task``)."""
    from backend.core.output_folder import MARKER_NAME

    path = _real_md(raw)
    if _REVIEW_NAME.match(path.stem):
        raise ReviewNoteError("这是一份复习笔记，底稿要用旁边的随看笔记（文件名以「_笔记.md」结尾的那份）。")
    if not _NOTE_NAME.match(path.stem) and not (path.parent / MARKER_NAME).is_file():
        raise ReviewNoteError(f"这不像 FluentFlow 的随看笔记（文件名以「_笔记.md」结尾，或在 FluentFlow 建的文件夹里）：{path}")
    return path


def _task_beside(note_path: Path) -> dict[str, Any] | None:
    """The task this note belongs to, from the folder's ``.fluentflow-task``
    marker (it survives a move). Each line names a task and the suffix its
    file names carry, so the second task on a recording that sits in its own
    folder (``课_笔记 (2).md``) is never taken for the first. A note whose
    name says nothing is matched only in a folder with a single task."""
    from backend.core.output_folder import _owners

    owners = _owners(note_path.parent)
    named = _NOTE_NAME.match(note_path.stem)
    if named:
        candidates = [task for task, suffix in owners if suffix == named["suffix"]]
    else:
        candidates = [task for task, _suffix in owners] if len(owners) == 1 else []
    for task_id in candidates:
        job = job_store.get_job(task_id)
        if job:
            return job
    return None


def locate(task_id: str | None = None, note_path: str | None = None) -> tuple[Path, dict[str, Any] | None]:
    """The follow-along md to write from and its task, if one is known.

    ``note_path`` is how an agent points at a folder the user moved, which
    FluentFlow no longer finds (decision 6.34). Given with a task, the two
    must be the same note."""
    job = job_store.get_job(task_id) if task_id else None
    if task_id and not job:
        raise ReviewNoteError("找不到这条任务。")
    if note_path:
        path = _checked_base(note_path)
        beside = _task_beside(path)
        if job is None:
            return path, beside
        known = _job_note_path(job)
        if (beside or {}).get("task_id") == task_id or (known is not None and known.resolve() == path):
            return path, job
        raise ReviewNoteError("给的任务编号和底稿对不上：这份随看笔记不是这条任务的。只传其中一个，或核对一下。")
    if not job:
        raise ReviewNoteError("要给出任务编号，或底稿 md 的路径。")
    path = _job_note_path(job)
    if path is None:
        raise ReviewNoteError(
            "找不到这条任务产出文件夹里的随看笔记（文件夹可能被挪走了）。"
            "把随看笔记 md 现在的路径作为 note_path 传进来即可。"
        )
    return _checked_base(path), job


def _segments(job: dict[str, Any] | None, note_path: Path) -> list[dict[str, Any]]:
    if job:
        segments = canonical_display_segments(job.get("result") or {})
        if segments:
            return segments
    folder = note_path.parent / "过程文件"
    for srt in sorted(folder.glob("*.srt")) if folder.is_dir() else []:
        segments = srt_segments(srt)
        if segments:
            return segments
    return []


def _playback_name(note_path: Path) -> str:
    cut = sorted(note_path.parent.glob("*_去气口.*"))
    return cut[0].name if cut else "剪后视频"


class _Ask:
    """The model call, and which channel really answered it: the
    subscription's name was recorded even after the key wrote the note."""

    def __init__(self, chat: Chat, name: str, fallback_name: str | None) -> None:
        self.chat, self.name, self.fallback_name = chat, name, fallback_name

    def __call__(self, system: str, user: str) -> str:
        return self.chat(system, user)

    @property
    def channel(self) -> str:
        fell_back = getattr(self.chat, "fell_back", None)
        if self.fallback_name and fell_back is not None and fell_back.is_set():
            return self.fallback_name
        return self.name


def default_chat() -> tuple[Chat, str] | None:
    """The Claude subscription when this machine opted into it, the user's
    key taking over when it fails at run time (decisions 4.12, 4.14); else
    Claude through the user's Anthropic key; else the text model."""
    from backend.core import ai_summarizer, claude_text
    from backend.core.local_config import resolve_secret
    from backend.core.local_entry_guards import local_ai_kwargs

    settings = local_ai_kwargs()
    anthropic = claude_text.claude_chat(resolve_secret(None, "anthropic_api_key"), max_tokens=ANSWER_MAX_TOKENS)
    keyed = ai_summarizer.text_model_chat(**settings)
    text_name = f"text_model:{ai_summarizer.text_model_name(**settings)}"
    if anthropic and anthropic[1] != claude_text.CHANNEL_SUBSCRIPTION:
        fallback, fallback_name = anthropic[0], anthropic[1]
    else:
        fallback, fallback_name = keyed, (text_name if keyed else None)
    subscribed = claude_text.subscription_first(fallback)
    if subscribed is not None:
        return _Ask(subscribed[0], subscribed[1], fallback_name), subscribed[1]
    if anthropic is not None:
        return anthropic
    if keyed is not None:
        return keyed, text_name
    return None


def _usage_scope(job: dict[str, Any] | None) -> Any:
    """The review note's model call, recorded as part of the task's note
    (``model_usage``); a note with no task beside it belongs to none."""
    return model_usage.scope(str((job or {}).get("task_id") or "") or None, task_steps.NOTE, "复习笔记")


def _answered_by(ask: Chat, channel: str) -> str:
    return ask.channel if isinstance(ask, _Ask) else channel


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
    return text.strip()


def _with_base_line(text: str, version: dict[str, Any]) -> str:
    """The base version as FluentFlow read it, under 出处信息: the model's own
    copy of it is not trusted to be exact."""
    line = f"- 底稿版本：{Path(version['path']).name}，修改于 {version['modified_at']}，内容指纹 {version['sha256'][:12]}"
    return text.rstrip() + "\n" + line + "\n"


def _keep_previous(target: Path) -> None:
    """Copy the review note about to be replaced into 「过程文件/历史版本」,
    made when the folder has none: an earlier version is never just lost."""
    if not target.is_file():
        return
    history = target.parent / "过程文件" / "历史版本"
    history.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(target, history / f"{target.stem}_{stamp}_重新生成前.md")


def _write_in_place(target: Path, text: str) -> None:
    """Keep the old one, then the whole new text in a file beside it, then
    swapped in: a failure partway leaves the old note as it was."""
    try:
        _keep_previous(target)
        handle, temporary = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=".md", dir=target.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(text)
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise ReviewNoteError(f"复习笔记没能写进文件夹（{exc}），原来的那份没动。") from exc


def _running_key(path: Path) -> str:
    return str(path.resolve())


def generate(task_id: str | None = None, note_path: str | None = None, *, chat: tuple[Chat, str] | None = None) -> dict[str, Any]:
    """Write ``<名>_复习笔记.md`` and its PDF beside the follow-along note.

    Raises ``ReviewNoteError`` with the reason when nothing was written; the
    task itself is never touched beyond the record of this run."""
    path, job = locate(task_id, note_path)
    key = _running_key(path)
    with _running_lock:
        if key in _running:
            raise ReviewNoteError("这份笔记的复习笔记已经在生成了。", code="already_running")
        _running.add(key)
    try:
        return _generate(path, job, chat)
    finally:
        with _running_lock:
            _running.discard(key)


def _generate(path: Path, job: dict[str, Any] | None, chat: tuple[Chat, str] | None) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ReviewNoteError("底稿是空的。")
    version = base_version(path, text)
    result = (job or {}).get("result") or {}
    rows = (result.get(note_section_times.RESULT_KEY) or {}).get("sections")
    table = section_table(text, path.stem, rows if isinstance(rows, list) else None)
    transcript = note_section_times.transcript_lines(_segments(job, path))
    system, user = build_prompt(
        note_markdown=text, note_name=path.name, table=table, transcript=transcript,
        version=version, playback_name=_playback_name(path), glossary=correction_notes.for_path(path),
    )
    found = chat if chat is not None else default_chat()
    if found is None:
        raise ReviewNoteError("这台电脑上没有能写复习笔记的模型：没登录 Claude 订阅，也没有配置 Key。")
    ask, channel = found
    started = datetime.now()
    try:
        with _usage_scope(job):
            answer = _strip_fence(ask(system, user))
    except Exception as exc:  # noqa: BLE001 - the reason goes back to the agent
        raise ReviewNoteError(f"模型调用失败：{exc}") from exc
    missing = [heading for heading in REQUIRED_HEADINGS if heading not in answer]
    if missing:
        raise ReviewNoteError(f"模型的回答不完整（缺「{'」「'.join(h[3:] for h in missing)}」），没有写入，可以再试一次。")
    note, unknown = expand_sources(answer, table)
    note = _with_base_line(note, version)
    target = review_path_for(path)
    _write_in_place(target, note)
    pdf = _pdf(note, target)
    record = {
        "path": str(target),
        "pdf_path": pdf.get("pdf_path"),
        "pdf_error": pdf.get("error"),
        "base": version,
        "written_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "written_by": _answered_by(ask, channel),
        "seconds": round((datetime.now() - started).total_seconds(), 1),
        "unknown_sources": unknown,
        "timed_sections": sum(1 for row in table if row["start"] is not None),
        "sections": len(table),
    }
    if job:
        record["record_error"] = _record(str(job.get("task_id") or ""), record)
    return {"task_id": (job or {}).get("task_id"), **record}


_BASE_LINE = re.compile(r"(?m)^- 底稿版本：.*\n?")

REVISE_RULES = (
    "\n\n## 这次是修改一份已经写好的复习笔记\n\n"
    "用户读过下面这份按上面写法写的复习笔记，提了意见。只按意见改；意见没提到的内容"
    "（知识点的正文、出处、「我的笔记」框、判断框、术语表、自测题、还没弄清的）原样保留，"
    "只在结构变了需要跟着改时才改（比如三级标题的编号和顺序变了，自测题、知识地图和"
    "「还没弄清的」里链到它的地方逐字跟着改）。出处照原样写成〔编号 时间〕；"
    "〔L1〕〔L2〕这样的记号是要原样保留的旧链接，照抄在原处。"
    "只输出改好的整篇笔记，不加说明。"
)


def revise(
    note_path: str | None, instruction: str, *, task_id: str | None = None, chat: tuple[Chat, str] | None = None,
) -> dict[str, Any]:
    """Change an existing review note as the user asked, without writing it
    again from the base (decision 8.8: a structural or wording change rewrites
    only what it touches). ``note_path`` is the review note or the
    follow-along note beside it; or give the task."""
    if not instruction.strip():
        raise ReviewNoteError("要说明这次按什么意见改。")
    review: Path | None = None
    if note_path and _REVIEW_NAME.match(Path(note_path).stem):
        review = _real_md(note_path)
        bases = [candidate for candidate in base_paths_for(review) if candidate.is_file()]
        if not bases:
            raise ReviewNoteError(f"找不到它旁边的随看笔记：{base_paths_for(review)[0]}")
        base, job = locate(task_id, str(bases[0]))
        if review_path_for(base) != review:
            raise ReviewNoteError(f"这份复习笔记和旁边的随看笔记对不上名字：{review.name}")
    else:
        base, job = locate(task_id, note_path)
        review = review_path_for(base)
    if not review.is_file():
        raise ReviewNoteError(f"还没有复习笔记：{review}")
    key = _running_key(base)
    with _running_lock:
        if key in _running:
            raise ReviewNoteError("这份笔记的复习笔记已经在生成了。", code="already_running")
        _running.add(key)
    try:
        return _revise(base, review, job, instruction, chat)
    finally:
        with _running_lock:
            _running.discard(key)


def _revise(
    base: Path, review: Path, job: dict[str, Any] | None, instruction: str, chat: tuple[Chat, str] | None,
) -> dict[str, Any]:
    rows = ((job or {}).get("result") or {}).get(note_section_times.RESULT_KEY) or {}
    table = section_table(base.read_text(encoding="utf-8"), base.stem, rows.get("sections") if isinstance(rows, dict) else None)
    current = review.read_text(encoding="utf-8")
    base_line = next(iter(_BASE_LINE.findall(current)), "")
    draft, kept = collapse_sources(_BASE_LINE.sub("", current), table)
    found = chat if chat is not None else default_chat()
    if found is None:
        raise ReviewNoteError("这台电脑上没有能写复习笔记的模型：没登录 Claude 订阅，也没有配置 Key。")
    ask, channel = found
    started = datetime.now()
    user = f"# 用户的意见\n\n{instruction.strip()}\n\n# 现在的复习笔记\n\n{draft.strip()}\n"
    try:
        with _usage_scope(job):
            answer = _strip_fence(ask(skill_text() + REVISE_RULES, user))
    except Exception as exc:  # noqa: BLE001 - the reason goes back to the agent
        raise ReviewNoteError(f"模型调用失败：{exc}") from exc
    missing = [heading for heading in REQUIRED_HEADINGS if heading not in answer]
    if missing:
        raise ReviewNoteError(f"模型的回答不完整（缺「{'」「'.join(h[3:] for h in missing)}」），没有写入，可以再试一次。")
    note, unknown = expand_sources(answer, table)
    lost = [kept[key] for key in kept if key not in note]
    for key, original in kept.items():
        note = note.replace(key, original)
    note = note.rstrip() + "\n" + (base_line if base_line.endswith("\n") or not base_line else base_line + "\n")
    _write_in_place(review, note)
    pdf = _pdf(note, review)
    record = {
        "path": str(review),
        "pdf_path": pdf.get("pdf_path"),
        "pdf_error": pdf.get("error"),
        "revised_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "revised_by": _answered_by(ask, channel),
        "instruction": instruction.strip(),
        "seconds": round((datetime.now() - started).total_seconds(), 1),
        "unknown_sources": unknown,
        "lost_links": lost,
    }
    if job:
        before = (job.get("result") or {}).get(RESULT_KEY) or {}
        record["record_error"] = _record(str(job.get("task_id") or ""), {**before, "last_revision": record})
    return {"task_id": (job or {}).get("task_id"), **record}


def _pdf(note: str, target: Path) -> dict[str, Any]:
    from backend.core import note_pdf

    title = next((line.lstrip("#").strip() for line in note.splitlines() if line.startswith("# ")), target.stem)
    stale = "旁边那份 PDF 还是上一版。" if target.with_suffix(".pdf").is_file() else ""
    try:
        made = note_pdf.markdown_to_pdf(note, title, target.parent, target.stem, keep_html_without_browser=False)
    except Exception as exc:  # noqa: BLE001 - the md is already written
        return {"pdf_path": None, "error": f"PDF 没生成（{exc}），md 已写好。{stale}"}
    if not made.get("pdf_path"):
        return {"pdf_path": None, "error": f"{made.get('reason') or 'PDF 没生成。'}md 已写好。{stale}"}
    return {"pdf_path": made["pdf_path"], "error": None}


def _record(task_id: str, record: dict[str, Any]) -> str | None:
    """Note on the task what was written; a failure here is reported, the
    file is already in place."""
    try:
        job = job_store.get_job(task_id)
        if not job:
            return None
        before = dict(job.get("result") or {})
        # Only this key changes; the note and the task's state stay as they are.
        job_store.save_result_changes(task_id, before, {**before, RESULT_KEY: record}, touch_updated_at=False)
    except Exception as exc:  # noqa: BLE001 - the note is written; say what was not recorded
        logger.warning("review note written but not recorded on task %s: %s", task_id, exc)
        return f"复习笔记已写好，但没能记到任务上（{exc}）。"
    return None


__all__ = [
    "ANSWER_MAX_TOKENS",
    "RESULT_KEY",
    "ReviewNoteError",
    "default_chat",
    "expand_sources",
    "generate",
    "locate",
    "revise",
    "collapse_sources",
    "REVIEW_SUFFIX",
    "SKILL_PATH",
    "UNKNOWN_SOURCE",
    "base_paths_for",
    "base_version",
    "build_prompt",
    "review_path_for",
    "section_table",
    "skill_text",
    "srt_segments",
]
