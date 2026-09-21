"""Regression coverage for saving a re-selected local source file."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.local_job_source import router


class LocalJobSourceTests(TestCase):
    def test_reselected_video_replaces_the_task_source_and_updates_the_result(self):
        app = FastAPI()
        app.include_router(router)
        job = {
            "task_id": "task-a",
            "client_id": "local-single-user",
            "status": "completed",
            "stage": "done",
            "progress": 100,
            "result": {"task_id": "task-a", "summary_markdown": "# Existing note"},
        }
        with TemporaryDirectory() as temp_dir, patch(
            "backend.core.media_intake._source_storage_dir", return_value=Path(temp_dir)
        ), patch(
            "backend.core.storage_paths._source_storage_dir", return_value=Path(temp_dir)
        ), patch("backend.routers.local_job_source.get_job", return_value=job), patch(
            "backend.routers.local_job_source.upsert_job"
        ) as upsert:
            response = TestClient(app).post(
                "/jobs/task-a/source",
                headers={"X-FluentFlow-Client-Id": "local-single-user"},
                files={"file": ("4.5期从零到一实战kickoff和组队.mp4", b"video-bytes", "video/mp4")},
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["result"]["display_title"], "4.5期从零到一实战kickoff和组队")
            self.assertTrue(payload["result"]["source_file_available"])
            self.assertEqual(
                (Path(temp_dir) / "task-a" / "source.mp4").read_bytes(),
                b"video-bytes",
            )
            self.assertEqual(upsert.call_args.kwargs["source_filename"], "4.5期从零到一实战kickoff和组队.mp4")
