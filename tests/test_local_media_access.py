"""Regression coverage for local video review streaming."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.local_media_access import LocalMediaAccess
from backend.routers.job_read import create_job_read_router


class _NoopEvents:
    async def subscribe(self, task_id: str, *, since: int = 0):  # pragma: no cover - unused by these routes
        yield ""


class LocalMediaAccessTests(TestCase):
    def test_grant_is_task_scoped_and_expires(self):
        access = LocalMediaAccess(ttl_seconds=60, now=lambda: 100)
        grant = access.issue("task-a", "local-single-user")

        self.assertTrue(access.allows(grant.token, "task-a", now=159))
        self.assertFalse(access.allows(grant.token, "task-b", now=159))
        self.assertFalse(access.allows(grant.token, "task-a", now=160))

    def test_media_route_streams_a_byte_range_after_session_is_issued(self):
        access = LocalMediaAccess(ttl_seconds=60)
        app = FastAPI()
        app.include_router(create_job_read_router(
            request_client_scope=lambda request: request.headers.get("x-fluentflow-client-id"),
            job_events=_NoopEvents(),
            media_access=access,
        ))

        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "review.mp4"
            source.write_bytes(b"0123456789")
            with patch("backend.routers.job_read.get_job", return_value={"task_id": "task-a"}), patch(
                "backend.routers.job_read.find_source_file", return_value=source
            ):
                client = TestClient(app)
                issued = client.post(
                    "/jobs/task-a/media-session",
                    headers={"X-FluentFlow-Client-Id": "local-single-user"},
                )

                self.assertEqual(issued.status_code, 200)
                media_url = issued.json()["media_url"]
                streamed = client.get(media_url, headers={"Range": "bytes=2-5"})

        self.assertEqual(streamed.status_code, 206)
        self.assertEqual(streamed.content, b"2345")
        self.assertEqual(streamed.headers["content-range"], "bytes 2-5/10")
        self.assertEqual(streamed.headers["accept-ranges"], "bytes")
