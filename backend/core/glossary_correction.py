"""Fix misheard words in the transcript from the series' word list and the context.

Requirement (owner, 2026-10-25): the transcript is made on this machine and
keeps getting names and terms wrong (「Cloud Cool」 for Claude Code,
「OpenCloud」 for OpenClaw). The series' word list (``FluentFlow 校正文档.md``,
decisions 5.29 and 5.36) used to reach only the model writing the note, so the
transcript, the subtitle files and the follow-along page kept the wrong words.
Now a model reads the transcript with the word list and the sentences around
each one and says which words were misheard; the code holds it to replacing a
word, never rewriting a sentence.

- With a word list a misheard word takes the list's spelling. Without one the
  model may still fix what the context makes certain, and nothing else.
- The same bounds as the subtitle check (``subtitle_correction``, decision
  4.14): what is taken out is short, what goes in is about as long, no bracket
  the sentence did not have, a word said twice is replaced only where the
  model says which one.
- Every change is recorded (old and new text, whether the word list or the
  context decided it, which sentence) and can be undone, one or all.
- Sentences the user edited are left alone. What FluentFlow itself wrote is
  fingerprinted when it writes the transcript (``fingerprints``); a sentence
  whose text no longer matches is someone else's.
- The Claude subscription goes first when this machine opted into it, the
  configured key takes over when it fails (decision 4.12); a model that raises
  ends the step; nothing here fails a task.

It replaces the conservative text-model correction (``transcript_correction``,
off by default, DeepSeek only, no word list, whole sentences rewritten into a
side copy only the note read).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.core import subtitle_correction as sc

logger = logging.getLogger(__name__)

Chat = Callable[[str, str], str]

FIELD = "glossary_corrections"
META = "glossary_correction"
FINGERPRINTS = sc.FINGERPRINTS

# Sentence text sent per request, and the read-only sentences on either side
# of a batch so its first and last sentences have their context too.
BATCH_CHARS = 3000
CONTEXT_SENTENCES = 6
MODEL_WORKERS = 4

BASIS_GLOSSARY = "glossary"
BASIS_CONTEXT = "context"

_OFF = {"0", "false", "no", "off", "disabled"}


def enabled() -> bool:
    """On unless ``FLUENTFLOW_GLOSSARY_CORRECTION`` turns it off."""
    return (os.environ.get("FLUENTFLOW_GLOSSARY_CORRECTION") or "").strip().lower() not in _OFF


def _text(segment: dict[str, Any]) -> str:
    return str(segment.get("text") or "").strip()


# ── whose sentence is it ──────────────────────────────────────────────────

_fingerprint = sc.fingerprint


def fingerprints(segments: list[dict[str, Any]], known: list[str] | None = None) -> list[str]:
    """The fingerprints of the sentences FluentFlow wrote, ``known`` kept."""
    found = list(known or [])
    seen = set(found)
    for seg in segments or []:
        if isinstance(seg, dict) and _text(seg):
            mark = _fingerprint(_text(seg))
            if mark not in seen:
                seen.add(mark)
                found.append(mark)
    return found


def remember(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """``after`` with the sentences FluentFlow changed between ``before`` and
    ``after`` (a correction, an undo) recorded as its own, and only those.

    Recording the whole transcript instead took the user's edited sentences
    for FluentFlow's too, and a second run then corrected them (review,
    2026-10-25). A task from before the fingerprints first gets the sentences
    it holds that the user did not edit."""
    known = before.get(FINGERPRINTS)
    old = [seg for seg in before.get("raw_segments") or [] if isinstance(seg, dict)]
    if not isinstance(known, list) or not known:
        edited = edited_by_user(before)
        known = fingerprints([seg for i, seg in enumerate(old) if i not in edited])
    new = [seg for seg in after.get("raw_segments") or [] if isinstance(seg, dict)]
    changed = [seg for i, seg in enumerate(new) if i >= len(old) or _text(seg) != _text(old[i])]
    updated = dict(after)
    updated[FINGERPRINTS] = fingerprints(changed, known)
    return updated


def edited_by_user(result: dict[str, Any]) -> set[int]:
    """Which sentences of ``raw_segments`` someone other than FluentFlow wrote.

    Tasks from before the fingerprints have only the editor's own records of
    what it changed, which name the sentences of the latest save; that is the
    best there is for them."""
    segments = [seg for seg in result.get("raw_segments") or [] if isinstance(seg, dict)]
    known = result.get(FINGERPRINTS)
    if isinstance(known, list) and known:
        marks = set(known)
        return {i for i, seg in enumerate(segments) if _text(seg) and _fingerprint(_text(seg)) not in marks}
    if not result.get("transcript_edited"):
        return set()
    changed = {str(r.get("after") or "").strip() for r in result.get("transcript_edit_records") or [] if isinstance(r, dict)}
    return {i for i, seg in enumerate(segments) if _text(seg) in changed}


# ── the model's judgement ─────────────────────────────────────────────────

_SYSTEM = """你会收到一段语音识别转录稿，按顺序一句一行。转录是机器做的，常把人名、公司名、产品名、模型名、英文术语和缩写、数字说法听错成发音相近的字或词。你的任务是找出这些听错的词，给出正确写法。

{glossary}

规则：
- 只改听错的词，不润色、不改句子：不补字、不删语气词和重复、不改标点、不改说法、不把口语改成书面语。
- 有词表时，转录里出现词表列的听法或发音相近的写法、而且上下文说的就是这个东西，改成词表的正确写法，basis 写 "glossary"。
- 词表里没有的，只有上下文让你非常确定时才改（同一个名字在别处写对了、说的显然是某个知名产品或术语），basis 写 "context"，why 用中文写一句依据。拿不准就不改。
- 普通的中文词句，就算读着别扭，也不改；只改专有名词、英文、数字说法这类听错的词。
- from：这一句里原样出现的一段文字，只框住听错的部分，尽量短。from 在这句里出现不止一次时，加 nth 写明是第几次出现（从 1 数）。
- to：正确写法。不加括号注释，不加解释。
- context_before 和 context_after 里的句子只供参考，不改。
- 一句里有几处听错，分几条写。

只输出 JSON：{{"fixes": [{{"id": 编号, "from": "...", "to": "...", "basis": "glossary 或 context", "why": "一句依据"}}]}}。不改的不要列出。"""

_GLOSSARY_SECTION = "下面是用户为这一系列录像整理的词表：正确写法、是什么、转录常听成什么。\n\n{text}"
_NO_GLOSSARY = "这一系列还没有词表，只能靠上下文判断。"


def _system(glossary: str) -> str:
    section = _GLOSSARY_SECTION.format(text=glossary.strip()) if glossary.strip() else _NO_GLOSSARY
    return _SYSTEM.format(glossary=section)


def _batches(segments: list[dict[str, Any]]) -> list[list[int]]:
    batches: list[list[int]] = []
    current: list[int] = []
    size = 0
    for index, seg in enumerate(segments):
        text = _text(seg)
        if not text:
            continue
        if current and size + len(text) > BATCH_CHARS:
            batches.append(current)
            current, size = [], 0
        current.append(index)
        size += len(text)
    if current:
        batches.append(current)
    return batches


def _payload(segments: list[dict[str, Any]], batch: list[int]) -> str:
    first, last = batch[0], batch[-1]
    before = [_text(s) for s in segments[max(0, first - CONTEXT_SENTENCES):first] if _text(s)]
    after = [_text(s) for s in segments[last + 1:last + 1 + CONTEXT_SENTENCES] if _text(s)]
    return json.dumps({
        "context_before": before,
        "sentences": [{"id": i, "text": _text(segments[i])} for i in batch],
        "context_after": after,
    }, ensure_ascii=False)


def entry_for(new: str, glossary: str, old: str | None = None) -> str | None:
    """The word-list line whose right spelling ``new`` is, or ``None``.
    ``old`` is the text it replaces: a piece of a Chinese name counts only
    when the line also lists that hearing."""
    target = sc._loose(new)
    if not target:
        return None
    lines = [line.strip() for line in glossary.splitlines() if line.strip().startswith("-")]
    heads = []
    for line in lines:
        head = re.split(r"[：:]", line.lstrip("- ").strip(), maxsplit=1)[0]
        # 「Post-train / Pre-train」 names two spellings in one line.
        heads.append((line, [part.strip() for part in head.split("/") if part.strip()]))
    for line, names in heads:
        if target in [sc._loose(name) for name in names]:
            return line
    # A whole word of a listed name (Claude out of Claude Code, 竞品调研 out
    # of 黑客级竞品调研) is that entry; a piece of a word is not (code inside
    # Codex, Agent inside subagent: Zhang He kickoff, 2026-10-25).
    wanted = new.strip()
    if re.search(r"[A-Za-z0-9]", wanted):
        pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(wanted) + r"(?![A-Za-z0-9])", re.IGNORECASE)
        found = lambda name: bool(pattern.search(name))  # noqa: E731
        listed = lambda line: True  # noqa: E731
    else:
        # 竞品 out of 黑客级竞品调研 is that entry when the line lists the
        # hearing it replaces (黑客级镜头调研 for 镜头); otherwise any two
        # characters of a long name would count as the word list's (review,
        # 2026-10-25).
        heard = (old or "").strip()
        found = lambda name: wanted in name  # noqa: E731
        listed = lambda line: bool(heard) and heard in line  # noqa: E731
    for line, names in heads:
        if any(found(name) for name in names) and listed(line):
            return line
    return None


def _position(sentence: str, old: str, nth: Any) -> int | None:
    places = [m.start() for m in re.finditer(re.escape(old), sentence)]
    if len(places) == 1:
        return places[0]
    try:
        which = int(nth)
    except (TypeError, ValueError):
        return None
    return places[which - 1] if 1 <= which <= len(places) else None


def decide(
    segments: list[dict[str, Any]], glossary: str, chat: Chat, *,
    protected: set[int] | frozenset[int] = frozenset(), stop: threading.Event | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    """The replacements the model makes, held to the bounds; how many
    requests were made and how many came back unreadable.

    A reply that cannot be read is asked once more. A model that raises
    (quota, login, network) ends the step with ``ModelFailed``."""
    system = _system(glossary)
    batches = _batches(segments)
    failed = threading.Event()
    calls = 0
    count = threading.Lock()

    def once(batch: list[int]) -> dict[str, Any] | None:
        nonlocal calls
        sc._stopped(stop)
        if failed.is_set():
            raise sc.ModelFailed("an earlier request failed")
        with count:
            calls += 1
        try:
            reply = chat(system, _payload(segments, batch))
        except Exception as exc:
            failed.set()
            raise sc.ModelFailed(str(exc)[:300]) from exc
        return sc._answer(reply)

    def ask(batch: list[int]) -> list[dict[str, Any]] | None:
        answer = once(batch)
        if answer is None:
            answer = once(batch)
        return answer["fixes"] if answer is not None else None

    with ThreadPoolExecutor(max_workers=MODEL_WORKERS) as pool:
        answers = list(pool.map(ask, batches))
    fixes: list[dict[str, Any]] = []
    for batch, answer in zip(batches, answers):
        allowed = set(batch) - set(protected)
        for fix in answer or []:
            if not isinstance(fix, dict):
                continue
            try:
                index = int(fix.get("id"))
            except (TypeError, ValueError):
                continue
            if index not in allowed:
                continue
            sentence = _text(segments[index])
            old, new = str(fix.get("from") or ""), str(fix.get("to") or "").strip()
            if not sc._acceptable(old, new, sentence):
                continue
            at = _position(sentence, old, fix.get("nth"))
            if at is None:
                continue
            entry = entry_for(new, glossary, old) if glossary.strip() else None
            why = str(fix.get("why") or "").strip()[:200]
            if entry is None and not why:
                # A change the word list does not back and the model gives no
                # reason for is not one it was sure of.
                continue
            record = {"index": index, "segment_index": index, "from": old, "to": new, "at": at,
                      "basis": BASIS_GLOSSARY if entry else BASIS_CONTEXT}
            if entry:
                record["entry"] = entry
            if why:
                record["why"] = why
            fixes.append(record)
    return fixes, calls, sum(1 for answer in answers if answer is None)


# ── the whole step ────────────────────────────────────────────────────────

@dataclass
class GlossaryCorrectionResult:
    status: str
    segments: list[dict[str, Any]]
    corrections: list[dict[str, Any]] = field(default_factory=list)
    sentences: int = 0
    protected: int = 0
    calls: int = 0
    unreadable_batches: int = 0
    glossary_chars: int = 0
    seconds: float = 0.0
    reason: str | None = None

    def meta(self, channel: str | None = None) -> dict[str, Any]:
        by_list = sum(1 for r in self.corrections if r.get("basis") == BASIS_GLOSSARY)
        data: dict[str, Any] = {
            "status": self.status,
            "applied": len(self.corrections),
            "from_glossary": by_list,
            "from_context": len(self.corrections) - by_list,
            "sentences": self.sentences,
            "left_as_edited": self.protected or None,
            "glossary_chars": self.glossary_chars,
            "calls": self.calls,
            "unreadable_batches": self.unreadable_batches or None,
            "seconds": round(self.seconds, 1),
            "channel": channel,
            "reason": self.reason,
        }
        return {key: value for key, value in data.items() if value not in (None, "")}


def correct(
    segments: list[dict[str, Any]], glossary: str, chat: Chat | None, *,
    protected: set[int] | frozenset[int] = frozenset(), stop: threading.Event | None = None,
) -> GlossaryCorrectionResult:
    """The whole step. Never raises: anything missing or failing leaves the
    transcript as it was, with the reason recorded."""
    started = time.perf_counter()
    glossary = (glossary or "").strip()

    def done(status: str, **kwargs: Any) -> GlossaryCorrectionResult:
        kwargs.setdefault("segments", segments)
        return GlossaryCorrectionResult(
            status=status, seconds=time.perf_counter() - started, glossary_chars=len(glossary),
            protected=len(protected), sentences=sum(1 for s in segments if _text(s)), **kwargs,
        )

    if not any(_text(s) for s in segments or []):
        return done("skipped", reason="no_transcript")
    if chat is None:
        return done("skipped", reason="no_model")
    calls = 0
    try:
        fixes, calls, unreadable = decide(segments, glossary, chat, protected=protected, stop=stop)
        fixed, records = sc.apply(segments, fixes)
        return done("completed" if records else "no_changes", segments=fixed, corrections=records,
                    calls=calls, unreadable_batches=unreadable)
    except sc.Cancelled:
        return done("cancelled", reason="task_cancelled")
    except sc.ModelFailed as exc:
        logger.warning("Glossary correction gave up, the model failed: %s", exc)
        return done("failed", reason=f"model_error: {exc}")
    except Exception as exc:  # noqa: BLE001 - this step may never fail a task
        logger.warning("Glossary correction failed: %s", exc)
        return done("failed", reason=str(exc)[:300])


# ── an existing task ──────────────────────────────────────────────────────

_COPIES = ("display_segments", "corrected_segments", "source_raw_segments", "source_display_segments",
           "source_corrected_segments")


def _same_sentence(segments: list[dict[str, Any]], text: str, occurrence: int) -> int | None:
    """The ``occurrence``-th sentence (from 0) reading exactly ``text``, or the
    only one when there is just one."""
    hits = [i for i, seg in enumerate(segments) if _text(seg) == text]
    if len(hits) == 1:
        return hits[0]
    return hits[occurrence] if occurrence < len(hits) else None


def _same_time(segments: list[dict[str, Any]], when: Any, word: str) -> int | None:
    try:
        moment = float(when)
    except (TypeError, ValueError):
        return None
    hits = [i for i, seg in enumerate(segments)
            if abs((sc._seconds(seg.get("start")) or 0.0) - moment) <= 0.05 and _text(seg).count(word) == 1]
    return hits[0] if len(hits) == 1 else None


def onto_result(result: dict[str, Any], fixed: list[dict[str, Any]], records: list[dict[str, Any]]) -> dict[str, Any]:
    """``result`` with the corrected ``raw_segments`` and the same changes made
    in every other copy of the transcript (what the page shows, the copies on
    the original's clock a re-export starts from), the records appended."""
    updated = dict(result)
    raw_before = [dict(s) for s in result.get("raw_segments") or [] if isinstance(s, dict)]
    updated["raw_segments"] = fixed
    for key in _COPIES:
        copy = result.get(key)
        if not isinstance(copy, list) or not copy:
            continue
        segments = [dict(s) for s in copy if isinstance(s, dict)]
        for record in records:
            index = record.get("segment_index")
            if not isinstance(index, int) or index >= len(raw_before):
                continue
            before = record["sentence_before"]
            occurrence = sum(1 for s in raw_before[:index] if _text(s) == before)
            at = _same_sentence(segments, before, occurrence)
            if at is not None:
                segments[at]["text"] = record["sentence_after"]
            elif "corrected" in key:
                # The retired text-model correction rewrote whole sentences
                # into this copy, which the note and an agent read first on
                # the tasks that had it on. The word goes in there too, in the
                # sentence at the same time, when it is there exactly once.
                at = _same_time(segments, record.get("source_start" if key.startswith("source_") else "start"),
                                record["from"])
                if at is None:
                    continue
                segments[at]["text"] = _text(segments[at]).replace(record["from"], record["to"], 1)
            else:
                continue
            if key.startswith("source_"):
                record.setdefault("source_start", segments[at].get("start"))
                record.setdefault("source_end", segments[at].get("end"))
        updated[key] = segments
    updated["transcript_text"] = sc.joined(fixed)
    updated["transcript_text_preview"] = updated["transcript_text"][:200]
    if updated.get("corrected_segments"):
        updated["corrected_transcript_text"] = sc.joined(updated["corrected_segments"])
    updated[FIELD] = [*[r for r in result.get(FIELD) or [] if isinstance(r, dict)], *records]
    return remember(result, updated)


def revert(result: dict[str, Any], ids: list[str] | None) -> tuple[dict[str, Any], list[str], list[str]]:
    undone, reverted, missing = sc.revert(result, ids, FIELD, "这个任务没有按词表或上下文改过的地方。")
    return (remember(result, undone) if reverted else undone), reverted, missing


def listing(result: dict[str, Any]) -> list[dict[str, Any]]:
    """The records with the sentence each sits in now (1-based), ``None``
    when that sentence has been changed since."""
    segments = [s for s in result.get("raw_segments") or [] if isinstance(s, dict)]
    trusted = sc.written_by_fluentflow(result)
    out = []
    for record in result.get(FIELD) or []:
        if not isinstance(record, dict):
            continue
        found = None if record.get("reverted") else sc._find(segments, record, trusted=trusted)
        out.append({**record, "sentence": (found + 1) if found is not None else None})
    return out


__all__ = [
    "BASIS_CONTEXT",
    "BASIS_GLOSSARY",
    "FIELD",
    "FINGERPRINTS",
    "GlossaryCorrectionResult",
    "META",
    "correct",
    "decide",
    "edited_by_user",
    "enabled",
    "entry_for",
    "fingerprints",
    "listing",
    "onto_result",
    "remember",
    "revert",
]
