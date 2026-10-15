"""Whether a finished note still carries every knowledge point it was written from.

Every way FluentFlow gets a note runs this once the note is written: the text
model (DeepSeek, OpenAI, Qwen), Claude from the frames, and the user's own agent
saving through MCP. The check reads the note that will actually be stored, not
an intermediate plan of it: a point counts as covered when the final text says
it, never because it was assigned to a chapter that was then written without it.

Two shapes, one judgement:

- ``check_against_transcript`` hands the model a stretch of transcript and the
  note, has it list what that stretch teaches and mark which of those the note
  never says. Used where no checklist exists yet.
- ``check_against_points`` hands it a checklist that already exists (the
  chapter mode's evidence) and the note, and asks which items are absent.

Both are a model's judgement, so a pass means "a model compared the two and
found nothing missing", not a proof. What is missing is written back in by
whoever wrote the note (``revise``), or, for an agent, handed back to it.
"""

from __future__ import annotations

import json
import logging
import threading
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

Chat = Callable[[str, str], str]

# One stretch of transcript per request, with the whole note beside it. Small
# enough that a text model's window holds both, large enough that a typical
# lecture part is two or three requests.
TRANSCRIPT_CHUNK_CHARS = 24_000
POINTS_BATCH_CHARS = 16_000
_MAX_WORKERS = 4
# A revision only adds; one that comes back much shorter than the note it was
# given rewrote or dropped content, and the note it was given is kept.
MIN_REVISION_RATIO = 0.9

# What a lecture says that is not a knowledge point. Counted as one, the
# check sent a course's attendance rules and a lecturer's war stories back
# into the note (measured 2026-10-12: 60 of 292 "missing" points on eight
# real lectures). The default note skill says the same in its own words.
NOT_KNOWLEDGE = "课程安排、考勤作业和考试说明、讲者的个人经历和闲聊（用来说明某个观点的例子除外）、口头鼓励、没有具体内容的总结和感想、寒暄、口头语、重复前文的话"

_CHECK_TRANSCRIPT_SYSTEM = f"""你是 FluentFlow 的笔记查漏助手。
输入是一段转录文字（原稿的一部分）和根据原稿写成的笔记。

先列出这段原稿讲到的每个知识点：概念和定义、观点和它的依据、方法和步骤、例子、数字、限制条件、讲者特意强调的地方。{NOT_KNOWLEDGE}不算知识点。
再逐条判断笔记里有没有写到它。意思写到了就算写到：换了说法、合并进别的句子、压缩成一句都算。笔记里找不到这个意思才算没写到。
转录里明显是语音识别错误、读不通的话，不要照着错字列成知识点。

输出严格 JSON 对象，不要输出 Markdown 或代码围栏：
{{"points": [{{"point": "知识点，一句话", "in_note": true}}]}}
没写到的项加一个 "source" 字段：原稿里讲这一点的要点或原话，供补写用。"""

_CHECK_POINTS_SYSTEM = f"""你是 FluentFlow 的笔记查漏助手。
输入是一份知识点清单（每项有 id）和一份笔记。逐项判断笔记里有没有写到它。
意思写到了就算写到：换了说法、合并进别的句子、压缩成一句都算。笔记里找不到这个意思才算没写到。
清单里属于{NOT_KNOWLEDGE}的项，不算没写到。

输出严格 JSON 对象，不要输出 Markdown 或代码围栏：
{{"missing_ids": ["没写到的 id"]}}
全部写到时输出 {{"missing_ids": []}}。"""

_RULES_SECTION = """

--- 这份笔记的写法 ---

笔记是按下面这套写法写的。判断漏没漏以它为准：写法要求省略、合并或留给读者看原文的内容，不算没写到。

{rules}"""

_REVISION_REQUEST = """下面是一份已经写好的笔记，以及查漏时发现它没写到的知识点。
把这些知识点写进笔记，读起来要像一开始就写在那里：
- 每一点放进讲它的那一段或那一节，和前后文接上；需要时改写所在句子，不在段落后面另起一行补充。
- 一点在笔记里已经写过（换了说法也算），就不再写。补进去的话不能和附近已有的句子说同一件事，用词也不能让两处说法互相打架。
- 补完通读全篇，顺手把难读的地方理顺：压在一句里的几层意思拆开，省掉的因果、转折补上，错字改对。只改说法，不改意思：事实、数字、人名、讲者的限定词都不变。
- 原稿里读不通、像是语音识别错误的人名、数字，照写法里对拿不准的词的规定标出来，不当成确定的内容写。笔记里已经标了「（存疑）」的词，原样保留这个词和标记，不改成猜的写法。
- 不推翻原结构，不删原有内容，不新增原稿里没有的信息。原有的截图引用 `![图注](文件名)` 原样保留，不新增截图引用。
只输出补完后的完整笔记 Markdown，第一行就是笔记内容，前面不加任何说明。

--- 笔记 ---

{note}

--- 没写到的知识点 ---

{missing}
"""


def _with_rules(system: str, rules: str | None) -> str:
    return system + _RULES_SECTION.format(rules=rules.strip()) if (rules or "").strip() else system


@dataclass(frozen=True)
class CoverageCheck:
    """What one check found. ``checked`` is false when no request gave a usable
    answer; ``failed_requests`` counts the ones that did not, so a partly checked
    note says so instead of reading as fully checked."""

    checked: bool
    point_count: int
    missing: list[dict[str, str]] = field(default_factory=list)
    failed_requests: int = 0
    # Checklist ids whose request failed: neither found nor missing.
    unchecked_ids: list[str] = field(default_factory=list)

    @property
    def missing_count(self) -> int:
        return len(self.missing)

    def as_record(self, *, revised: bool = False) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "point_count": self.point_count,
            "missing_count": self.missing_count,
            "missing_points": [item.get("point", "") for item in self.missing],
            "failed_requests": self.failed_requests,
            "revised": revised,
        }


def unchecked(reason: str) -> dict[str, Any]:
    """The record for a note no check ran on, and why."""
    return {"checked": False, "reason": reason}


def _json_object(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


# The longest one coverage request may take, start to finish. The client's own
# timeout only limits the wait between two pieces of the answer, so a provider
# that keeps a request open by sending blank lines while it queues (DeepSeek
# does this for non-streaming calls) is waited on for ever: on 2026-10-15 a
# 38-minute meeting's note sat in "checking" for over an hour and a half with
# one connection open and nothing arriving. A checklist answer normally takes a
# minute or two; past this the note is saved as written and the check is
# recorded as not run.
REQUEST_DEADLINE_SECONDS = 600.0


class CoverageRequestTimeout(TimeoutError):
    pass


def _within_deadline(chat: Chat, system: str, user: str, seconds: float) -> str:
    """``chat(system, user)``, or ``CoverageRequestTimeout`` after ``seconds``.

    The request runs on a daemon thread that is left behind when it overruns:
    Python cannot interrupt a blocked socket read, and the note must not wait
    for it."""
    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["text"] = chat(system, user)
        except BaseException as exc:  # noqa: BLE001 - handed back to the caller
            outcome["error"] = exc

    worker = threading.Thread(target=run, name="note-coverage-request", daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise CoverageRequestTimeout(f"查漏请求 {seconds:g} 秒没有答完")
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("text", "")


def _ask(chat: Chat, system: str, user: str) -> dict[str, Any] | None:
    """One judgement, asked twice at most: models occasionally answer with
    malformed JSON, and a second try costs less than an unchecked note."""
    for _attempt in range(2):
        try:
            payload = _json_object(_within_deadline(chat, system, user, REQUEST_DEADLINE_SECONDS))
        except Exception as exc:  # noqa: BLE001 - a failed check leaves the note as written
            logger.warning("note coverage check request failed: %s", exc)
            return None
        if payload is not None:
            return payload
    logger.warning("note coverage check returned no usable JSON twice")
    return None


def _split(text: str, max_chars: int) -> list[str]:
    """Line-boundary pieces of at most ``max_chars`` (a single longer line is cut)."""
    text = (text or "").strip()
    pieces: list[str] = []
    while text:
        if len(text) <= max_chars:
            pieces.append(text)
            break
        boundary = text.rfind("\n", 0, max_chars)
        cut = boundary if boundary > max_chars // 2 else max_chars
        pieces.append(text[:cut].rstrip())
        text = text[cut:].lstrip("\n")
    return pieces


def _map(fn: Callable[[Any], Any], items: list[Any]) -> list[Any]:
    if len(items) <= 1:
        return [fn(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(items))) as pool:
        return list(pool.map(fn, items))


def check_against_transcript(
    chat: Chat, transcript: str, note: str, *, rules: str | None = None,
    chunk_chars: int = TRANSCRIPT_CHUNK_CHARS,
) -> CoverageCheck:
    """List what the transcript teaches and which of it the note never says.

    ``rules`` is what the note was written under (the note skill, or the text
    model's prompt): "missing" means missing by those rules, so the check never
    asks for what the writing rules told the writer to leave out."""
    system = _with_rules(_CHECK_TRANSCRIPT_SYSTEM, rules)
    chunks = _split(transcript, chunk_chars)
    if not chunks or not (note or "").strip():
        return CoverageCheck(checked=False, point_count=0)

    def one(chunk: str) -> dict[str, Any] | None:
        user = f"--- 原稿片段 ---\n\n{chunk}\n\n--- 笔记 ---\n\n{note}"
        return _ask(chat, system, user)

    point_count = 0
    missing: list[dict[str, str]] = []
    failed = 0
    for payload in _map(one, chunks):
        points = payload.get("points") if payload else None
        if not isinstance(points, list):
            failed += 1
            continue
        for item in points:
            if not isinstance(item, dict) or not str(item.get("point") or "").strip():
                continue
            point_count += 1
            if item.get("in_note") is False:
                missing.append({
                    "point": str(item.get("point")).strip(),
                    "source": str(item.get("source") or "").strip(),
                })
    return CoverageCheck(
        checked=failed < len(chunks), point_count=point_count, missing=missing, failed_requests=failed,
    )


def check_against_points(
    chat: Chat, points: list[dict[str, str]], note: str, *, rules: str | None = None,
    batch_chars: int = POINTS_BATCH_CHARS,
) -> CoverageCheck:
    """Which items of an existing checklist (``{"id", "text"}``) the note never
    says, judged by ``rules`` as in ``check_against_transcript``."""
    system = _with_rules(_CHECK_POINTS_SYSTEM, rules)
    items = [item for item in points if str(item.get("id") or "") and str(item.get("text") or "").strip()]
    if not items or not (note or "").strip():
        return CoverageCheck(checked=False, point_count=len(items))
    batches: list[list[dict[str, str]]] = [[]]
    size = 0
    for item in items:
        length = len(item["text"]) + len(item["id"]) + 8
        if batches[-1] and size + length > batch_chars:
            batches.append([])
            size = 0
        batches[-1].append(item)
        size += length

    def one(batch: list[dict[str, str]]) -> dict[str, Any] | None:
        listing = json.dumps([{"id": i["id"], "text": i["text"]} for i in batch], ensure_ascii=False)
        return _ask(chat, system, f"--- 知识点清单 ---\n\n{listing}\n\n--- 笔记 ---\n\n{note}")

    by_id = {item["id"]: item for item in items}
    missing: list[dict[str, str]] = []
    unchecked_ids: list[str] = []
    failed = 0
    for batch, payload in zip(batches, _map(one, batches)):
        ids = payload.get("missing_ids") if payload else None
        if not isinstance(ids, list):
            failed += 1
            unchecked_ids.extend(item["id"] for item in batch)
            continue
        in_batch = {item["id"] for item in batch}
        for raw in ids:
            key = str(raw)
            if key in in_batch and key in by_id:
                missing.append({"id": key, "point": by_id[key]["text"], "source": by_id[key].get("source", "")})
                in_batch.discard(key)
    return CoverageCheck(
        checked=failed < len(batches), point_count=len(items), missing=missing, failed_requests=failed,
        unchecked_ids=unchecked_ids,
    )


def missing_listing(missing: list[dict[str, str]]) -> str:
    lines = []
    for item in missing:
        source = str(item.get("source") or "").strip()
        lines.append(f"- {item.get('point', '')}" + (f"\n  原稿：{source}" if source else ""))
    return "\n".join(lines)


def revision_request(note: str, missing: list[dict[str, str]]) -> str:
    return _REVISION_REQUEST.format(note=note, missing=missing_listing(missing))


def accept_revision(note: str, revised: str) -> str | None:
    """The revision when it plausibly kept the note and added to it, else ``None``."""
    candidate = (revised or "").strip()
    if not candidate or len(candidate) < len((note or "").strip()) * MIN_REVISION_RATIO:
        logger.warning(
            "coverage revision returned %d chars for a %d-char note; keeping the note",
            len(candidate), len((note or "").strip()),
        )
        return None
    return candidate


def revise(write: Chat, system: str, note: str, check: CoverageCheck) -> str | None:
    """The note with what ``check`` found missing written back in, by ``write``
    under the writer's own rules; ``None`` when there was nothing to add or the
    rewrite was not usable."""
    if not check.missing:
        return None
    try:
        revised = write(system, revision_request(note, check.missing))
    except Exception as exc:  # noqa: BLE001 - the unrevised note is still a note
        logger.warning("coverage revision request failed: %s", exc)
        return None
    return accept_revision(note, revised)


__all__ = [
    "NOT_KNOWLEDGE",
    "Chat",
    "CoverageCheck",
    "accept_revision",
    "check_against_points",
    "check_against_transcript",
    "missing_listing",
    "revise",
    "revision_request",
    "unchecked",
]
