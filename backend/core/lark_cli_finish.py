"""What a Feishu export through lark-cli does after its writes.

``_finish`` notes each chapter's heading block, points each link to a heading
of the note at that heading, gives back list numbers Feishu restarted at 1
(``_restore_list_numbers``), and records what the document looks like now and
when Feishu last saw it change (``_snapshot``, ``modified_times``), all on the
export's ``sync``: what keeping the two copies in step starts from.
"""

from __future__ import annotations

import html
import json
import logging
import re
from typing import Any, Dict, List, Optional

from backend.core import feishu_note, lark_cli_run
from backend.core.lark_cli_run import LarkCliError, Runner

logger = logging.getLogger(__name__)


def _finish(
    runner: Runner,
    bin_path: str,
    doc_id: str,
    doc_url: str,
    links: List[feishu_note.HeadingLink],
    parts: List[feishu_note.Section],
    sync: dict[str, Any],
) -> None:
    """After the writes: note each chapter's heading block, point each link to
    a heading at that heading, and note the revision, all on ``sync``. A link
    that cannot be fixed keeps its words and is listed, with the ones the
    caller already reported; the document stands either way."""
    try:
        outline = lark_cli_run._run(
            runner,
            [bin_path, "docs", "+fetch", "--doc", doc_id, "--scope", "outline",
             "--detail", "with-ids", "--as", "user"],
            timeout=60,
        )
    except LarkCliError as exc:
        logger.warning("lark-cli outline fetch failed for %s: %s", doc_id, exc)
        # The links this push wrote stay unpointed too, and are listed with
        # the ones already reported: the user is told every placeholder the
        # document still holds.
        sync["link_errors"].extend(link.label for link in links)
        # The document was written; only its headings' ids are not known. The
        # next export reads them again (``reread_sections``) before writing,
        # rather than the note dropping out of sync (owner, 2026-10-24: a
        # failed read here once left a note unsynced with no error shown).
        sync["sections"] = None
        return
    fetched = outline.get("document") if isinstance(outline.get("document"), dict) else outline
    content = str(fetched.get("content") or "")
    sync["headings"] = feishu_note.headings(content)
    sync["sections"] = feishu_note.section_records(parts, feishu_note.heading_list(content))
    if fetched.get("revision_id") is not None:
        sync["revision_id"] = fetched.get("revision_id")
    base = doc_url.split("#", 1)[0]
    for link in links:
        block_id = sync["headings"].get(feishu_note.heading_key(link.heading))
        # A link in a callout's title is bold with the rest of the title, and
        # Feishu shows it as [**label**](...).
        for label in (link.label, f"**{link.label}**"):
            target = f"[{label}]({base}#{block_id})" if block_id else label
            try:
                updated = lark_cli_run._run(
                    runner,
                    [bin_path, "docs", "+update", "--doc", doc_id, "--command", "str_replace",
                     "--doc-format", "markdown", "--pattern", f"[{label}]({link.placeholder})",
                     "--content", target, "--as", "user"],
                    timeout=60,
                )
            except LarkCliError as exc:
                logger.warning("lark-cli could not link %s in %s: %s", link.label, doc_id, exc)
                updated = {"result": "failed"}
            if updated.get("result") in (None, "success"):
                break
        if not block_id or updated.get("result") not in (None, "success"):
            sync["link_errors"].append(link.label)
        revision = (updated.get("document") or {}).get("revision_id") if isinstance(updated.get("document"), dict) else None
        if revision is not None:
            sync["revision_id"] = revision
    _restore_list_numbers(runner, bin_path, doc_id, parts)
    _snapshot(runner, bin_path, doc_id, sync)


_RE_NOTE_ORDERED = re.compile(r"^\s*(\d+)[.）]\s+(.+)$")
_RE_FEISHU_LIST_START = re.compile(r'<ol><li id="([^"]+)"[^>]*\bseq="1"[^>]*>(.*?)</li>', re.S)


_RE_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>")


def _list_item_key(text: str, *, xml: bool = False) -> str:
    # Both sides can hold tags (<b> in a note); Feishu's XML also escapes the
    # & < > that the note writes as they are, as in "> 2 且 < 5".
    plain = _RE_TAG.sub("", text)
    if xml:
        plain = html.unescape(plain)
    plain = re.sub(r"\]\([^)]*\)|[*`_\[\]]", "", plain)
    return re.sub(r"\s+", "", plain)


def _restore_list_numbers(
    runner: Runner, bin_path: str, doc_id: str, parts: List[feishu_note.Section],
) -> None:
    """Give back the number of a list item that Feishu restarted at 1.

    Feishu numbers each run of adjacent list items from 1, and its markdown
    import keeps no number, so an item cut off from its list by a block between
    items (a check quote under item 2, say) showed as 1 instead of 3. Each list
    that starts at 1 in the document while the note numbers that item higher
    gets its number set (checked against lark-cli 1.0.70, 2026-11-05: an
    ``<li seq="3">`` written with ``block_replace`` shows as 3). A failure
    leaves the document as it is."""
    # Only an item numbered past 1 right after something other than a list
    # item can have been restarted; a note without one is not read again.
    # An item is matched by its whole text, and only when no other list item
    # in the note reads the same, so no other list is ever renumbered.
    numbers: dict[str, int] = {}
    seen: dict[str, int] = {}
    previous = ""
    for line in "\n".join(part.text for part in parts).split("\n"):
        if not line.strip():
            continue
        match = _RE_NOTE_ORDERED.match(line)
        if match:
            key = _list_item_key(match.group(2))
            seen[key] = seen.get(key, 0) + 1
            if int(match.group(1)) != 1 and not _RE_NOTE_ORDERED.match(previous):
                numbers[key] = int(match.group(1))
        previous = line
    numbers = {key: number for key, number in numbers.items() if seen[key] == 1}
    if not numbers:
        return
    try:
        fetched = lark_cli_run._run(
            runner,
            [bin_path, "docs", "+fetch", "--doc", doc_id, "--detail", "full", "--as", "user"],
            timeout=60,
        )
    except LarkCliError as exc:
        logger.warning("lark-cli could not read %s to check list numbers: %s", doc_id, exc)
        return
    document = fetched.get("document") if isinstance(fetched.get("document"), dict) else fetched
    for match in _RE_FEISHU_LIST_START.finditer(str(document.get("content") or "")):
        number = numbers.get(_list_item_key(match.group(2), xml=True))
        if number is None:
            continue
        try:
            lark_cli_run._run(
                runner,
                [bin_path, "docs", "+update", "--doc", doc_id, "--command", "block_replace",
                 "--block-id", match.group(1),
                 "--content", f'<ol><li seq="{number}">{match.group(2)}</li></ol>', "--as", "user"],
                timeout=60,
            )
        except LarkCliError as exc:
            logger.warning("lark-cli could not renumber a list item in %s: %s", doc_id, exc)


def _snapshot(runner: Runner, bin_path: str, doc_id: str, sync: dict[str, Any]) -> None:
    """What the document looks like now, chapter by chapter, and when Feishu
    last saw it change: an edit made in Feishu later is anything newer than
    this time, in any chapter that no longer looks like this."""
    try:
        shown = lark_cli_run._run(
            runner,
            [bin_path, "docs", "+fetch", "--doc", doc_id, "--doc-format", "markdown", "--as", "user"],
            timeout=60,
        )
        document = shown.get("document") if isinstance(shown.get("document"), dict) else shown
        sync["feishu_sections"] = feishu_note.feishu_section_records(str(document.get("content") or ""))
        # ``feishu_sections`` moves on when an edit made in Feishu is pulled
        # in; this stays as the push left it, for telling whether the document
        # was edited in Feishu since (``feishu_sync.keeper``).
        sync["pushed_sections"] = sync["feishu_sections"]
        if document.get("revision_id") is not None:
            sync["revision_id"] = document.get("revision_id")
    except LarkCliError as exc:
        logger.warning("lark-cli could not read %s back after the push: %s", doc_id, exc)
        sync["feishu_sections"] = None
        sync["pushed_sections"] = None
    modified = modified_times(runner, bin_path, [doc_id]).get(doc_id)
    if modified is not None:
        sync["feishu_modified_at"] = modified


def modified_times(
    runner: Runner, bin_path: str, doc_ids: List[str], errors: Optional[List[str]] = None,
) -> Dict[str, int]:
    """When Feishu last saw each document change (seconds), for the ones it
    reports; a document it does not report is left out. A request that
    failed is added to ``errors`` when given, so a caller can tell "Feishu
    could not be asked" from "Feishu does not know this document"."""
    times: Dict[str, int] = {}
    for start in range(0, len(doc_ids), 50):
        chunk = doc_ids[start:start + 50]
        body = {"request_docs": [{"doc_token": doc_id, "doc_type": "docx"} for doc_id in chunk]}
        try:
            data = lark_cli_run._run(
                runner,
                [bin_path, "drive", "metas", "batch_query", "--data", json.dumps(body), "--as", "user"],
                timeout=60,
            )
        except LarkCliError as exc:
            logger.warning("lark-cli could not read when documents changed: %s", exc)
            if errors is not None:
                errors.append(str(exc))
            continue
        for meta in data.get("metas") or []:
            try:
                times[str(meta.get("doc_token"))] = int(meta.get("latest_modify_time"))
            except (TypeError, ValueError):
                continue
    return times
