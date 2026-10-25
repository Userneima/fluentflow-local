"""Which way a note goes to Feishu (the user's lark-cli login or their Feishu
app), and keeping an export's outcome on its task. Shared by the export
route and the Agent API."""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import HTTPException

from backend.core.job_store import get_job, update_job_result
from backend.core.lark_cli_exporter import lark_cli_ready

logger = logging.getLogger(__name__)

_CLI_ROUTES = {"local_cli", "lark_cli"}
_OPENAPI_ROUTES = {"openapi", "lark_openapi"}
_HOSTED_OAUTH_ROUTES = {"user_oauth", "feishu_user", "feishu_user_oauth", "lark_user_oauth"}
# The default for new users: their own identity when lark-cli is installed and
# signed in, otherwise their Feishu app.
_AUTO_ROUTES = {"auto"}


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _local_lark_export_target(
    lark_export_route: Optional[str],
    lark_via_cli: Optional[str],
) -> str:
    route = (lark_export_route or "").strip().lower()
    if route in _CLI_ROUTES:
        return "lark_cli"
    if route in _HOSTED_OAUTH_ROUTES:
        raise HTTPException(
            status_code=400,
            detail=(
                "本地版不支持飞书账号 OAuth 导出。"
                "请改用本机 lark-cli 登录，或在设置里填写自己的 App ID / App Secret。"
            ),
        )
    if route in _OPENAPI_ROUTES:
        return "lark_openapi"
    if _truthy(lark_via_cli):
        return "lark_cli"
    if route in _AUTO_ROUTES:
        return "lark_cli" if lark_cli_ready() else "lark_openapi"
    return "lark_openapi"


def _record_export_on_task(task_id: str, client_id: Optional[str], **fields: Any) -> None:
    """Keep the export's outcome on the task so reopening it shows the link.

    Only for a task this caller owns; the latest result is re-read so an edit
    saved while Feishu was being called is not overwritten.
    """
    try:
        job = get_job(task_id, client_id=client_id)
        if not job:
            return
        latest = job.get("result") if isinstance(job.get("result"), dict) else {}
        update_job_result(task_id, {**latest, **fields}, client_id=client_id)
    except Exception:  # noqa: BLE001 - the Feishu document exists either way
        logger.warning("Could not record the Feishu export on task %s", task_id, exc_info=True)
