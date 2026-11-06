"""Retrying a task still at work says what to do, in the user's words.

10-18 review, item 15: an agent retrying a running task got the English
"Cancel the active job before retrying it" to pass on.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import HTTPException

from backend.core import job_store, task_submission


@pytest.mark.parametrize("status", ["queued", "running"])
def test_retrying_a_task_at_work_names_the_cancel(status):
    task_id = f"rt-{uuid.uuid4().hex[:8]}"
    job_store.upsert_job(task_id=task_id, status=status)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(task_submission.retry_task(task_id, client_id=None, local_caller=True))
    assert caught.value.status_code == 409
    assert "先取消" in caught.value.detail and "cancel_task" in caught.value.detail
