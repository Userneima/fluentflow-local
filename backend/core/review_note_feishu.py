"""The review note goes to Feishu above its follow-along note.

Requirement (owner, 2026-11-06): a lesson with a review note shows the review
note as the lesson's page in the Feishu library, with the follow-along note as
its child; the review note is what he comes back to, the follow-along note is
the detail under it. A lesson with no review note stays as it was.

How:

- Only a follow-along note kept in step with Feishu (``feishu_sync``, exported
  with the user's own identity) has a place to go next to; otherwise nothing
  is done.
- The first push creates the review note's document where the follow-along
  note is now (same parent node), titled after the follow-along note's title
  in Feishu as he named it there, with ``TITLE_SUFFIX``; then moves the
  follow-along note under it. Feishu puts a new node last among its siblings,
  so an earlier lesson's page ends up at the end of the list.
- Afterwards each change of the review note md (written, revised, replaced,
  or edited in Obsidian) rewrites the chapters that changed in that document.
  It goes one way: the md is the review note. A document edited in Feishu
  since the last push is kept in 「过程文件/历史版本」 before it is written over.
- Pushes run on ``feishu_sync``'s single worker, so they never overlap a push
  or pull of the follow-along note. The watch loop calls ``check`` to catch
  review notes changed outside FluentFlow and ones written before this.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from backend.core import job_store

logger = logging.getLogger(__name__)

RESULT_KEY = "review_note_feishu"
TITLE_SUFFIX = "｜复习笔记"
# Prefix of the files kept about the review note's document beside the
# follow-along note's own (``lark_cli_exporter`` ``state``).
STATE = "review_"
QUIET_SECONDS = 20.0

_IMAGE = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)(\))")


def _artifact_root() -> Path:
    from backend.core.storage_paths import _artifact_storage_dir

    return Path(_artifact_storage_dir())


def _with_task_images(task_id: str, markdown: str) -> str:
    """Point each local screenshot of the review note (a path beside the md,
    such as ``过程文件/截图/x.jpg``) at the task's frame, which is what the
    exporter uploads; one the task does not have stays as it is and is named
    in the document as not uploaded."""
    frames = _artifact_root() / task_id / "frames"

    def swap(match: re.Match[str]) -> str:
        src = match.group(2)
        if urlparse(src).scheme or src.startswith("/jobs/"):
            return match.group(0)
        name = Path(src).name
        if not (frames / name).is_file():
            return match.group(0)
        return f"{match.group(1)}/jobs/{task_id}/artifacts/frame?file={quote(name)}{match.group(3)}"

    return _IMAGE.sub(swap, markdown)


def _node(runner: Any, bin_path: str, doc_id: str) -> dict[str, Any]:
    from backend.core import lark_cli_exporter as cli

    data = cli._run(
        runner,
        [bin_path, "wiki", "+node-get", "--node-token", doc_id, "--obj-type", "docx", "--as", "user"],
        timeout=60,
    )
    node = data.get("node") if isinstance(data.get("node"), dict) else data
    if not node.get("node_token"):
        raise cli.LarkCliError(f"飞书没说这篇文档在知识库的哪里（{doc_id}）。")
    return node


def _keep_feishu_version(review: Path) -> Any:
    def keep(text: str) -> None:
        history = review.parent / "过程文件" / "历史版本"
        history.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (history / f"{review.stem}_{stamp}_飞书上改过_被覆盖前.md").write_text(text, encoding="utf-8")

    return keep


def _record(task_id: str, **fields: Any) -> None:
    job = job_store.get_job(task_id)
    if not isinstance(job, dict):
        return
    before = dict(job.get("result") or {})
    current = before.get(RESULT_KEY) if isinstance(before.get(RESULT_KEY), dict) else {}
    job_store.save_result_changes(task_id, before, {**before, RESULT_KEY: {**current, **fields}},
                                  touch_updated_at=False)


def _review_path(result: dict[str, Any]) -> Path | None:
    record = result.get("review_note")
    raw = record.get("path") if isinstance(record, dict) else None
    path = Path(str(raw)) if raw else None
    return path if path is not None and path.is_file() else None


def pending(result: dict[str, Any] | None) -> bool:
    """Whether this task's review note is not in Feishu as it is now."""
    from backend.core import feishu_sync
    from backend.core.lark_cli_exporter import note_digest

    if not isinstance(result, dict) or feishu_sync.recorded_sync(result) is None:
        return False
    review = _review_path(result)
    if review is None:
        return False
    record = result.get(RESULT_KEY) if isinstance(result.get(RESULT_KEY), dict) else {}
    if record.get("stopped"):
        return False
    if _move_owed(record):
        return True
    # The md as it was when pushed (the document holds it with its
    # screenshots pointed at the task's frames, so its own digest differs).
    return note_digest(review.read_text(encoding="utf-8")) != record.get("md_digest")


MOVE_TRIES = 3


def _move_owed(record: dict[str, Any]) -> bool:
    """The review note is in Feishu but the follow-along note is not under it
    yet (a move that failed, or a push cut off before it): tried again a few
    times, then left to the user."""
    return (isinstance(record.get("sync"), dict) and record.get("moved") is False
            and int(record.get("move_tries") or 0) < MOVE_TRIES)


def push(task_id: str, *, runner: Any = subprocess.run) -> dict[str, Any] | None:
    """Bring the review note's Feishu document up to date, creating it above
    the follow-along note the first time. Returns the export response, or
    None when there was nothing to do. Never raises: a failure is recorded on
    the task under ``RESULT_KEY``."""
    from backend.core import feishu_sync, test_copy
    from backend.core import lark_cli_exporter as cli

    if test_copy.enabled():
        return None
    job = job_store.get_job(task_id)
    result = job.get("result") if isinstance(job, dict) and isinstance(job.get("result"), dict) else None
    if not pending(result):
        return None
    follow = feishu_sync.recorded_sync(result)
    review = _review_path(result)
    record = result.get(RESULT_KEY) if isinstance(result.get(RESULT_KEY), dict) else {}
    previous = record.get("sync") if isinstance(record.get("sync"), dict) and record["sync"].get("doc_id") else None
    text = review.read_text(encoding="utf-8")
    markdown = _with_task_images(task_id, text)
    bin_path = cli._resolve_lark_cli_bin()
    if not bin_path:
        _record(task_id, error="没找到 lark-cli，复习笔记没发到飞书。")
        return None
    try:
        if previous is not None:
            keep = None
            modified = cli.modified_times(runner, bin_path, [str(previous["doc_id"])]).get(str(previous["doc_id"]))
            # Not knowing when it last changed counts as changed: keeping a
            # copy too many is cheap, losing an edit made in Feishu is not.
            if modified is None or modified > int(record.get("feishu_modified_at") or 0):
                keep = _keep_feishu_version(review)
            response = cli.export_markdown_via_lark_cli(
                str(record.get("title") or review.stem), markdown, task_id=task_id,
                artifact_root=_artifact_root(), previous=previous, keep_feishu=keep, runner=runner, state=STATE,
            )
            moved = record.get("moved")
            if _move_owed(record):
                _record(task_id, move_tries=int(record.get("move_tries") or 0) + 1)
                node = _node(runner, bin_path, str(follow["doc_id"]))
                moved = _move_under(runner, bin_path, node, str(previous["doc_id"]), task_id)
        else:
            node = _node(runner, bin_path, str(follow["doc_id"]))
            title = f"{node.get('title') or result.get('lark_doc_title') or review.stem}{TITLE_SUFFIX}"
            parent = str(node.get("parent_node_token") or "")
            if not parent and not _at_my_library_root(runner, bin_path, node):
                raise cli.LarkCliError("随看笔记在另一个知识库的最上层，复习笔记只能建在某篇文档下面或「我的文档库」最上层。"
                                       "把随看笔记挪进一个目录后会自动补上")
            response = cli.export_markdown_via_lark_cli(
                title, markdown, task_id=task_id, artifact_root=_artifact_root(), runner=runner, state=STATE,
                parent_token=parent or None,
                previous=_made_unrecorded(runner, bin_path, node, title, str(follow.get("url") or ""), task_id),
            )
            # Recorded before the move, so a failed move never makes a second document.
            _record(task_id, sync=response.get("sync"), url=response.get("url"), title=title, moved=False)
            moved = _move_under(runner, bin_path, node, str(response["doc_id"]), task_id)
    except Exception as exc:  # noqa: BLE001 - the review note itself is fine
        if previous is not None and feishu_sync._gone(exc):
            _record(task_id, stopped="飞书那份复习笔记已被删除或打不开了，不再同步。", error=None)
            return None
        logger.warning("review note of %s not pushed to Feishu: %s", task_id, exc)
        _record(task_id, error=f"复习笔记没发到飞书（{exc}）。笔记本身没受影响，下次改动时会再试。")
        return None
    sync = response.get("sync") if isinstance(response.get("sync"), dict) else {}
    modified_at = cli.modified_times(runner, bin_path, [str(sync.get("doc_id"))]).get(str(sync.get("doc_id")))
    _record(task_id, sync=sync, url=response.get("url"), moved=moved, md_digest=cli.note_digest(text),
            feishu_modified_at=modified_at or 0, pushed_at=datetime.now(timezone.utc).isoformat(),
            error=None if moved else _MOVE_FAILED)
    return response


def _at_my_library_root(runner: Any, bin_path: str, node: dict[str, Any]) -> bool:
    from backend.core import lark_cli_exporter as cli

    data = cli._run(runner, [bin_path, "wiki", "+node-list", "--space-id", "my_library", "--page-all", "--as", "user"],
                    timeout=60)
    items = data.get("items") or data.get("nodes") or []
    return any(isinstance(item, dict) and item.get("node_token") == node.get("node_token") for item in items)


def _made_unrecorded(
    runner: Any, bin_path: str, follow_node: dict[str, Any], title: str, follow_url: str, task_id: str,
) -> dict[str, Any] | None:
    """A review note document made beside the follow-along note by a push cut
    off before the task recorded it: found by its title among the
    follow-along note's siblings and written into again, not made twice. Its
    chapters are read again from it (``sections`` None)."""
    from backend.core import lark_cli_exporter as cli

    # Only a push of this task leaves its pushed copy behind: a document of
    # the same name that this task never made is not taken over.
    pushed = cli.pushed_note_path(_artifact_root(), task_id, STATE)
    if pushed is None or not pushed.is_file():
        return None
    command = [bin_path, "wiki", "+node-list", "--space-id", str(follow_node.get("space_id") or ""), "--as", "user"]
    if follow_node.get("parent_node_token"):
        command[5:5] = ["--parent-node-token", str(follow_node["parent_node_token"])]
    data = cli._run(runner, command, timeout=60)
    items = data.get("items") or data.get("nodes") or []
    found = next((item for item in items if isinstance(item, dict) and item.get("title") == title
                  and item.get("obj_token")), None)
    if found is None:
        return None
    doc_id = str(found["obj_token"])
    url = follow_url.replace(str(follow_node.get("obj_token") or "\0"), doc_id) if follow_url else ""
    return {"doc_id": doc_id, "url": url, "images": [], "headings": {}, "sections": None, "link_errors": []}


_MOVE_FAILED = "复习笔记已发到飞书，但随看笔记没能挪到它下面，要手动拖一下。"


def _move_under(runner: Any, bin_path: str, follow_node: dict[str, Any], review_doc_id: str, task_id: str) -> bool:
    from backend.core import lark_cli_exporter as cli

    try:
        review_node = _node(runner, bin_path, review_doc_id)
        cli._run(
            runner,
            [bin_path, "wiki", "+move", "--node-token", str(follow_node["node_token"]),
             "--target-parent-token", str(review_node["node_token"]), "--as", "user"],
            timeout=60,
        )
    except Exception as exc:  # noqa: BLE001 - the review note is in Feishu either way
        logger.warning("follow-along note of %s not moved under its review note: %s", task_id, exc)
        return False
    return True


_TIMERS: dict[str, threading.Timer] = {}
_LOCK = threading.Lock()


def schedule(task_id: str | None, delay: float | None = None) -> None:
    """Push once the review note has not changed for ``QUIET_SECONDS`` (an
    agent replacing passages one call at a time makes one push)."""
    if not task_id:
        return
    from backend.core import feishu_sync

    def fire() -> None:
        with _LOCK:
            if _TIMERS.get(task_id) is timer:
                _TIMERS.pop(task_id, None)
        feishu_sync._WORKER.submit(push, task_id)

    timer = threading.Timer(QUIET_SECONDS if delay is None else delay, fire)
    timer.daemon = True
    with _LOCK:
        previous = _TIMERS.get(task_id)
        if previous is not None:
            previous.cancel()
        _TIMERS[task_id] = timer
    timer.start()


def check() -> list[str]:
    """Schedule a push for each review note that is not in Feishu as it is
    now: edited in Obsidian, or written before review notes went to Feishu."""
    queued = []
    for row in job_store.list_feishu_synced():
        task_id = row[0]
        job = job_store.get_job(task_id)
        try:
            if pending(job.get("result") if isinstance(job, dict) else None):
                schedule(task_id, delay=0)
                queued.append(task_id)
        except OSError as exc:
            logger.warning("review note of %s not readable: %s", task_id, exc)
    return queued


__all__ = ["RESULT_KEY", "TITLE_SUFFIX", "check", "pending", "push", "schedule"]
