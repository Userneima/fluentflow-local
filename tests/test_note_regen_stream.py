"""Coverage for the streaming regenerate route.

Regeneration used to be one blocking POST: the button spun for minutes with no
signal, so a slow run and a hung run looked identical. `/regenerate-summary`
and `/regenerate-summary/stream` now share one generator — these tests pin that
the stream reports steps and that both routes still agree on the outcome.

Every job-store call the routes make is rebound to a temp database here.
`job_store`'s functions take `db_path` as a *default argument*, bound at import
time, so patching the module constant does not redirect them: the only reliable
way to keep a test off the user's real database is to rebind the callables the
router actually holds.
"""

import functools
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.ai_summarizer import SummaryResult
import backend.core.job_store as job_store
import backend.routers.local_note_regen as regen

TRANSCRIPT = "转录文本。" * 200
REGENERATED = "# 重生后的笔记\n\n很长的正文。"
CLIENT = "anonymous"


def _fake_summarize(transcript, *, on_progress=None, **_kwargs):
    """Stand in for the model: report the steps a chapter-coverage run reports."""
    if on_progress:
        for completed in range(4):
            on_progress({"step": "evidence", "completed": completed, "total": 3})
        on_progress({"step": "chapters", "completed": 2, "total": 2})
        on_progress({"step": "coverage", "completed": 1, "total": 1})
    return SummaryResult(
        markdown=REGENERATED,
        requested_mode="chapter_coverage",
        resolved_mode="chapter_coverage",
        transcript_length=len(transcript),
        chunk_count=3,
    )


class RegenerateStreamTests(TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "jobs.sqlite"
        self.store = {
            name: functools.partial(getattr(job_store, name), db_path=self.db_path)
            for name in ("get_job", "upsert_job", "finalize_job_result_if_unchanged")
        }
        for name, bound in self.store.items():
            self._start(patch.object(regen, name, bound))
        # Ownership resolution reaches the store through helpers that hold their
        # own import-time default; the routes only need a resolved target.
        self._start(patch.object(regen, "_resolve_regen_target", self._target))
        self._start(patch.object(regen, "log_event", lambda **_kwargs: None))

        self.store["upsert_job"](
            task_id="task-a",
            status="completed",
            client_id=CLIENT,
            stage="done",
            progress=100,
            result={"task_id": "task-a", "summary_markdown": "# 旧笔记", "transcript_text": TRANSCRIPT},
        )
        app = FastAPI()
        app.include_router(regen.router)
        self.client = TestClient(app)
        self.form = {"transcript": TRANSCRIPT, "task_id": "task-a"}

    def _start(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def _target(self, _request, task_id):
        job = self.store["get_job"](task_id, client_id=CLIENT)
        return regen._RegenTarget(
            task_id=task_id,
            client_id=CLIENT,
            existing_job=job,
            initial_result=job.get("result") if job else None,
            regenerated_from_task_id=None,
        )

    def _stored(self):
        return self.store["get_job"]("task-a", client_id=CLIENT)["result"]

    @staticmethod
    def _events(body: str) -> list[dict]:
        return [
            json.loads(line[6:])
            for chunk in body.split("\n\n")
            for line in chunk.split("\n")
            if line.startswith("data: ")
        ]

    def test_the_stream_names_each_step_and_ends_with_the_note(self):
        with patch.object(regen, "summarize_transcript_with_metadata", _fake_summarize):
            response = self.client.post("/regenerate-summary/stream", data=self.form)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        events = self._events(response.text)
        steps = [event.get("note_step") for event in events if event.get("stage") == "summary"]
        self.assertIn("evidence", steps)
        self.assertIn("chapters", steps)
        self.assertIn("coverage", steps)
        self.assertEqual(events[-1]["stage"], "done")
        self.assertEqual(events[-1]["result"]["summary_markdown"], REGENERATED)
        self.assertEqual(self._stored()["summary_markdown"], REGENERATED)

    def test_each_step_arrives_labelled_and_counted(self):
        with patch.object(regen, "summarize_transcript_with_metadata", _fake_summarize):
            response = self.client.post("/regenerate-summary/stream", data=self.form)

        evidence = [e for e in self._events(response.text) if e.get("note_step") == "evidence"]
        self.assertEqual([e["note_step_completed"] for e in evidence], [0, 1, 2, 3])
        self.assertEqual({e["note_step_total"] for e in evidence}, {3})
        self.assertEqual({e["note_step_label"] for e in evidence}, {"提取要点"})

    def test_progress_never_moves_backwards(self):
        with patch.object(regen, "summarize_transcript_with_metadata", _fake_summarize):
            response = self.client.post("/regenerate-summary/stream", data=self.form)

        values = [event["progress"] for event in self._events(response.text) if "progress" in event]
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[-1], 100.0)

    def test_a_failure_arrives_as_an_error_event_not_a_dropped_connection(self):
        def _boom(*_args, **_kwargs):
            raise RuntimeError("provider exploded")

        with patch.object(regen, "summarize_transcript_with_metadata", _boom):
            response = self.client.post("/regenerate-summary/stream", data=self.form)

        self.assertEqual(response.status_code, 200)
        last = self._events(response.text)[-1]
        self.assertEqual(last["stage"], "error")
        self.assertEqual(last["status"], 500)
        self.assertTrue(last["error"])

    def test_the_json_route_returns_the_same_note(self):
        with patch.object(regen, "summarize_transcript_with_metadata", _fake_summarize):
            response = self.client.post("/regenerate-summary", data=self.form)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary_markdown"], REGENERATED)

    def test_an_autosave_during_generation_no_longer_discards_the_note(self):
        # The observed failure: the editor stored the identical note and only
        # moved `summary_edited_at`, and the finished regeneration was dropped.
        def _autosave_then_summarize(transcript, *, on_progress=None, **kwargs):
            self.store["upsert_job"](
                task_id="task-a",
                status="completed",
                client_id=CLIENT,
                result={
                    "task_id": "task-a",
                    "summary_markdown": "# 旧笔记",
                    "transcript_text": TRANSCRIPT,
                    "summary_edited": True,
                    "summary_edited_at": "2026-08-02T17:22:39+08:00",
                },
            )
            return _fake_summarize(transcript, on_progress=on_progress, **kwargs)

        with patch.object(regen, "summarize_transcript_with_metadata", _autosave_then_summarize):
            response = self.client.post("/regenerate-summary", data=self.form)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._stored()["summary_markdown"], REGENERATED)

    def test_a_real_edit_during_generation_still_wins(self):
        def _edit_then_summarize(transcript, *, on_progress=None, **kwargs):
            self.store["upsert_job"](
                task_id="task-a",
                status="completed",
                client_id=CLIENT,
                result={
                    "task_id": "task-a",
                    "summary_markdown": "# 我手写的笔记",
                    "transcript_text": TRANSCRIPT,
                },
            )
            return _fake_summarize(transcript, on_progress=on_progress, **kwargs)

        with patch.object(regen, "summarize_transcript_with_metadata", _edit_then_summarize):
            response = self.client.post("/regenerate-summary", data=self.form)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._stored()["summary_markdown"], "# 我手写的笔记")
