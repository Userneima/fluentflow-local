"""Coverage for the streaming regenerate route.

Regeneration used to be one blocking POST: the button spun for minutes with no
signal, so a slow run and a hung run looked identical. `/regenerate-summary`
and `/regenerate-summary/stream` now share one generator — these tests pin that
the stream reports steps and that both routes still agree on the outcome.

The whole route runs against a temp database, redirected by the two runtime
path variables. `job_store`/`event_logger` resolve their path per call, so one
environment patch is enough — no rebinding of the callables the router holds.
"""

import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.ai_summarizer import SummaryResult
from backend.core.job_store import get_job, upsert_job
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
        # Windows keeps the sqlite file handle alive past the last connection,
        # so teardown must not treat a locked temp file as a test failure.
        self._tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "jobs.sqlite"
        self._start(patch.dict(os.environ, {
            "FLUENTFLOW_JOB_DB_PATH": str(self.db_path),
            "FLUENTFLOW_EVENT_DB_PATH": str(Path(self._tmp.name) / "events.sqlite"),
        }))

        upsert_job(
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

    def _stored(self):
        return get_job("task-a", client_id=CLIENT)["result"]

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
            upsert_job(
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

    def test_a_client_that_hangs_up_does_not_cancel_the_generation(self):
        # Switching windows or closing the tab used to tear the generator down
        # mid-run: the note was discarded and nothing was even logged. The work
        # is a job task now; the stream is only a view of it.
        from backend.core.local_job_runtime import JOB_EVENTS

        async def drive():
            await JOB_EVENTS.reset("task-a")
            upsert_job(task_id="task-a", status="running", client_id=CLIENT,
                       stage="summary_regenerate", progress=0)
            events = regen._regenerate_summary_events(
                target=regen._RegenTarget(
                    "task-a", CLIENT, get_job("task-a", client_id=CLIENT),
                    get_job("task-a", client_id=CLIENT)["result"], None),
                transcript=TRANSCRIPT, deepseek_api_key=None, openai_api_key=None,
                qwen_api_key=None, anthropic_api_key=None,
                ai_provider=None, ai_model=None, note_mode=None,
                system_prompt=None, prompt_preset=None, prompt_preset_label=None,
                source_type=None, source_filename=None, source_duration_seconds=None,
            )

            async def worker():
                async for event in events:
                    await JOB_EVENTS.publish("task-a", event)

            await JOB_EVENTS.start("task-a", worker)
            # Read two frames, then walk away without draining the rest.
            subscription = JOB_EVENTS.subscribe("task-a")
            await subscription.__anext__()
            await subscription.__anext__()
            await subscription.aclose()
            for _ in range(200):
                if not await JOB_EVENTS.has_running_task("task-a"):
                    break
                await asyncio.sleep(0.01)

        with patch.object(regen, "summarize_transcript_with_metadata", _fake_summarize):
            asyncio.run(drive())

        self.assertEqual(self._stored()["summary_markdown"], REGENERATED)

    def test_re_attaching_replays_from_the_requested_event_index(self):
        from backend.core.local_job_runtime import JOB_EVENTS

        async def drive():
            await JOB_EVENTS.reset("task-b")
            for index in range(4):
                await JOB_EVENTS.publish("task-b", {"stage": "summary", "progress": index * 10})
            await JOB_EVENTS.publish("task-b", {"stage": "done", "progress": 100, "result": {}})
            seen = []
            async for chunk in JOB_EVENTS.subscribe("task-b", since=3):
                seen.append(json.loads(chunk[6:]))
            return seen

        seen = asyncio.run(drive())
        self.assertEqual([event["event_index"] for event in seen], [3, 4])
        self.assertEqual(seen[-1]["stage"], "done")

    def test_a_second_run_does_not_replay_the_previous_runs_result(self):
        # A record keeps its id across regenerations, so stale history would end
        # a new subscriber immediately with the old note.
        from backend.core.local_job_runtime import JOB_EVENTS

        async def drive():
            await JOB_EVENTS.reset("task-c")
            await JOB_EVENTS.publish("task-c", {"stage": "done", "progress": 100,
                                                "result": {"summary_markdown": "# 上一轮"}})
            await JOB_EVENTS.reset("task-c")
            return await JOB_EVENTS.cached_events("task-c")

        self.assertEqual(asyncio.run(drive()), [])

    def test_a_real_edit_during_generation_still_wins(self):
        def _edit_then_summarize(transcript, *, on_progress=None, **kwargs):
            upsert_job(
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
