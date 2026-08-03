"""Coverage for storing a note an external agent wrote (`PUT /agent/v1/tasks/{id}/note`).

Notes can now come from a model that is not FluentFlow's own pipeline — Claude
Desktop reads the transcript over MCP, writes the note, and puts it back here.
Three things have to hold for that to be safe:

- the stored note is shaped exactly like a hand-edited one, so the editor,
  exports, and artifacts keep working without knowing who wrote it;
- a write can be made conditional, because an agent spends minutes writing and
  the note it read may have been edited by the user in between;
- an agent's note beats an in-flight local regeneration, the same way a human
  edit does.

The route runs against a temp database and artifact directory, redirected by the
runtime path variables — `job_store`/`event_logger` resolve their paths per
call, so patching the environment is enough.
"""

import inspect
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.job_store import (
    finalize_job_result_if_unchanged,
    get_job,
    upsert_job,
)
from backend.core.note_write import NOTE_SOURCE_AGENT, NOTE_SOURCE_EDITOR, apply_summary_edit
from backend.routers.local_agent import router as agent_router
from backend.routers.local_job_edit import router as job_edit_router
from scripts.fluentflow_mcp_server import TOOL_DEFINITIONS, TOOL_FUNCTIONS

TRANSCRIPT = "转录文本。" * 200
OLD_NOTE = "# 旧笔记\n\n流水线生成的正文。"
AGENT_NOTE = "# Claude Desktop 写的笔记\n\n对话打磨过的正文。"
TOKEN = "test-access-token"
CLIENT = "anonymous"
TASK = "task-a"


class _RouteCase(TestCase):
    def setUp(self):
        # Windows keeps the sqlite handle alive past the last connection, so
        # teardown must not treat a locked temp file as a test failure.
        self._tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self._start(patch.dict(os.environ, {
            "FLUENTFLOW_JOB_DB_PATH": str(root / "jobs.sqlite"),
            "FLUENTFLOW_EVENT_DB_PATH": str(root / "events.sqlite"),
            "FLUENTFLOW_ARTIFACT_DIR": str(root / "artifacts"),
            "FLUENTFLOW_ACCESS_TOKEN": TOKEN,
        }))
        upsert_job(
            task_id=TASK,
            status="completed",
            client_id=CLIENT,
            stage="done",
            progress=100,
            result={
                "task_id": TASK,
                "display_title": "组队 kickoff",
                "summary_markdown": OLD_NOTE,
                "transcript_text": TRANSCRIPT,
            },
        )
        app = FastAPI()
        app.include_router(agent_router)
        app.include_router(job_edit_router)
        self.client = TestClient(app)
        self.headers = {"X-FluentFlow-Access-Token": TOKEN}

    def _start(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def _stored(self) -> dict:
        return get_job(TASK, client_id=CLIENT)["result"]

    def _save(self, **payload):
        return self.client.put(f"/agent/v1/tasks/{TASK}/note", json=payload, headers=self.headers)


class SaveAgentNoteTests(_RouteCase):
    def test_the_note_is_stored_and_returned_in_the_task_package(self):
        response = self._save(summary_markdown=AGENT_NOTE, author="claude-desktop")

        self.assertEqual(response.status_code, 200)
        package = response.json()["package"]
        self.assertEqual(package["note"]["markdown"], AGENT_NOTE)
        self.assertEqual(self._stored()["summary_markdown"], AGENT_NOTE)

    def test_the_package_says_who_wrote_the_note(self):
        # Without this an agent re-reading the task cannot tell its own write
        # from a human edit, and would regenerate over hand-done work.
        note = self._save(summary_markdown=AGENT_NOTE, author="claude-desktop").json()["package"]["note"]

        self.assertEqual(note["source"], NOTE_SOURCE_AGENT)
        self.assertEqual(note["source_label"], "claude-desktop")
        self.assertTrue(note["edited"])
        self.assertTrue(note["edited_at"])

    def test_provenance_describes_the_latest_write_not_an_earlier_one(self):
        # A stale label is worse than none: it would credit this note to
        # whoever happened to write the previous one.
        self._save(summary_markdown=AGENT_NOTE, author="claude-desktop")

        note = self._save(summary_markdown=AGENT_NOTE + "\n\n再补一句。").json()["package"]["note"]
        self.assertEqual(note["source"], NOTE_SOURCE_AGENT)
        self.assertIsNone(note["source_label"])

    def test_a_later_hand_edit_takes_the_note_back_from_the_agent(self):
        self._save(summary_markdown=AGENT_NOTE, author="claude-desktop")

        by_hand = AGENT_NOTE + "\n\n用户手写的补充。"
        self.client.patch(f"/jobs/{TASK}/summary", json={"summary_markdown": by_hand})

        stored = self._stored()
        self.assertEqual(stored["summary_markdown"], by_hand)
        self.assertEqual(stored["summary_source"], NOTE_SOURCE_EDITOR)
        self.assertIsNone(stored["summary_source_label"])

    def test_a_note_written_by_an_agent_looks_saved_to_the_editor(self):
        self._save(summary_markdown=AGENT_NOTE)

        stored = self._stored()
        self.assertTrue(stored["summary_edited"])
        self.assertEqual(stored["summary_status"], "completed")
        self.assertFalse(stored["summary_skipped"])
        self.assertIsNone(stored["summary_error"])

    def test_an_empty_note_is_refused_rather_than_blanking_the_stored_one(self):
        for blank in ("", "   \n"):
            with self.subTest(blank=blank):
                self.assertEqual(self._save(summary_markdown=blank).status_code, 400)
        self.assertEqual(self._stored()["summary_markdown"], OLD_NOTE)

    def test_a_missing_or_non_string_note_is_a_bad_request(self):
        self.assertEqual(self._save().status_code, 400)
        self.assertEqual(self._save(summary_markdown={"md": AGENT_NOTE}).status_code, 400)

    def test_an_oversized_note_is_refused(self):
        with patch.dict(os.environ, {"FLUENTFLOW_MAX_SUMMARY_EDIT_CHARS": "50"}):
            self.assertEqual(self._save(summary_markdown="x" * 51).status_code, 413)
        self.assertEqual(self._stored()["summary_markdown"], OLD_NOTE)

    def test_writing_to_an_unknown_task_is_a_404(self):
        response = self.client.put(
            "/agent/v1/tasks/no-such-task/note",
            json={"summary_markdown": AGENT_NOTE},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 404)

    def test_the_whole_agent_surface_stays_disabled_without_an_access_token(self):
        with patch.dict(os.environ, {"FLUENTFLOW_ACCESS_TOKEN": ""}):
            response = self.client.put(f"/agent/v1/tasks/{TASK}/note", json={"summary_markdown": AGENT_NOTE})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._stored()["summary_markdown"], OLD_NOTE)


class WritePreconditionTests(_RouteCase):
    def test_echoing_back_the_note_it_read_lets_the_write_through(self):
        response = self._save(summary_markdown=AGENT_NOTE, expected_summary_markdown=OLD_NOTE)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._stored()["summary_markdown"], AGENT_NOTE)

    def test_a_user_edit_during_writing_turns_a_silent_clobber_into_a_conflict(self):
        edited_by_hand = OLD_NOTE + "\n\n用户手写的补充。"
        self.client.patch(
            f"/jobs/{TASK}/summary",
            json={"summary_markdown": edited_by_hand},
        )

        response = self._save(summary_markdown=AGENT_NOTE, expected_summary_markdown=OLD_NOTE)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._stored()["summary_markdown"], edited_by_hand)

    def test_an_empty_precondition_asserts_the_task_had_no_note(self):
        # Not the same as omitting it — "" means "there was nothing when I read".
        self.assertEqual(self._save(summary_markdown=AGENT_NOTE, expected_summary_markdown="").status_code, 409)

    def test_omitting_the_precondition_writes_unconditionally(self):
        self.assertEqual(self._save(summary_markdown=AGENT_NOTE).status_code, 200)

    def test_a_non_string_precondition_is_a_bad_request(self):
        self.assertEqual(self._save(summary_markdown=AGENT_NOTE, expected_summary_markdown=7).status_code, 400)


class RegenerationLosesToAnAgentNoteTests(_RouteCase):
    def test_a_local_regeneration_in_flight_cannot_overwrite_the_agent_note(self):
        # A regeneration snapshots the result, runs for minutes, then finalizes
        # only if the note has not moved. The agent write must move it.
        snapshot = dict(self._stored())

        self._save(summary_markdown=AGENT_NOTE)

        finalized = finalize_job_result_if_unchanged(
            task_id=TASK,
            expected_result=snapshot,
            result={**snapshot, "summary_markdown": "# 流水线迟到的笔记"},
            status="completed",
            client_id=CLIENT,
            stage="done",
            progress=100,
            summary_status="completed",
        )
        self.assertIsNone(finalized)
        self.assertEqual(self._stored()["summary_markdown"], AGENT_NOTE)


class OneWritePathTests(TestCase):
    """The editor and the agent must not drift into two different field sets."""

    def test_both_writers_produce_the_same_fields(self):
        base = {"task_id": TASK, "summary_markdown": OLD_NOTE}
        editor = apply_summary_edit(base, TASK, AGENT_NOTE, source=NOTE_SOURCE_EDITOR)
        agent = apply_summary_edit(base, TASK, AGENT_NOTE, source=NOTE_SOURCE_AGENT, source_label="claude-desktop")

        self.assertEqual(set(editor), set(agent))
        differing = {key for key in editor if editor[key] != agent[key]}
        self.assertEqual(differing, {"summary_source", "summary_source_label"})

    def test_the_input_result_is_never_mutated(self):
        base = {"task_id": TASK, "summary_markdown": OLD_NOTE}
        apply_summary_edit(base, TASK, AGENT_NOTE, source=NOTE_SOURCE_AGENT)
        self.assertEqual(base, {"task_id": TASK, "summary_markdown": OLD_NOTE})


class ListTasksTests(_RouteCase):
    """Finding the work. Without this an agent can only act on ids pasted by hand."""

    def setUp(self):
        super().setUp()
        upsert_job(task_id="no-note", status="completed", client_id=CLIENT, stage="done",
                   summary_status="skipped",
                   result={"task_id": "no-note", "display_title": "只转录", "transcript_text": TRANSCRIPT})
        upsert_job(task_id="failed-note", status="completed", client_id=CLIENT, stage="done",
                   summary_status="failed",
                   result={"task_id": "failed-note", "display_title": "笔记失败",
                           "transcript_text": TRANSCRIPT, "summary_markdown": ""})
        upsert_job(task_id="running", status="running", client_id=CLIENT, stage="stt",
                   result={"task_id": "running", "display_title": "还在转录"})

    def _list(self, **params):
        response = self.client.get("/agent/v1/tasks", params=params, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _ids(self, **params) -> set[str]:
        return {row["task_id"] for row in self._list(**params)["tasks"]}

    def test_missing_covers_no_note_and_a_failed_note(self):
        self.assertEqual(self._ids(note="missing"), {"no-note", "failed-note", "running"})

    def test_a_task_stops_being_missing_once_a_note_is_written(self):
        # The job's summary_status column stays "skipped" after a note write —
        # only the result moves — so a filter keyed on status reported every
        # written task as still needing work.
        self.assertIn("no-note", self._ids(note="missing"))

        response = self.client.put(
            "/agent/v1/tasks/no-note/note",
            json={"summary_markdown": AGENT_NOTE, "author": "claude-desktop"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)

        self.assertNotIn("no-note", self._ids(note="missing"))
        self.assertIn("no-note", self._ids(note="present"))

    def test_present_is_the_complement_of_missing(self):
        self.assertEqual(self._ids(note="present"), {TASK})
        self.assertEqual(self._ids(note="missing") | self._ids(note="present"), self._ids())

    def test_rows_say_who_wrote_the_note_so_an_agent_can_skip_its_own_work(self):
        # A second "write notes for everything that needs one" pass must be able
        # to tell its own note from a pipeline one, or it rewrites its own work.
        self._save(summary_markdown=AGENT_NOTE, author="claude-desktop")

        row = next(r for r in self._list(note="present")["tasks"] if r["task_id"] == TASK)
        self.assertEqual(row["note"]["source"], NOTE_SOURCE_AGENT)
        self.assertEqual(row["note"]["source_label"], "claude-desktop")
        self.assertTrue(row["note"]["edited"])

    def test_a_row_carries_the_sizes_needed_to_choose_work_but_no_bodies(self):
        row = next(r for r in self._list(note="present")["tasks"] if r["task_id"] == TASK)
        self.assertEqual(row["note"]["chars"], len(OLD_NOTE))
        self.assertEqual(row["transcript_chars"], len(TRANSCRIPT))
        self.assertEqual(row["title"], "组队 kickoff")
        # Listing a hundred tasks must not ship a hundred transcripts.
        self.assertNotIn("summary_markdown", json_dumps(row))
        self.assertNotIn("transcript_text", json_dumps(row))

    def test_status_narrows_to_finished_work(self):
        self.assertNotIn("running", self._ids(status="completed"))
        self.assertEqual(self._ids(status="running"), {"running"})

    def test_an_unknown_note_filter_is_rejected_rather_than_ignored(self):
        response = self.client.get("/agent/v1/tasks", params={"note": "todo"}, headers=self.headers)
        self.assertEqual(response.status_code, 422)

    def test_listing_needs_the_access_token_like_the_rest_of_the_surface(self):
        with patch.dict(os.environ, {"FLUENTFLOW_ACCESS_TOKEN": ""}):
            self.assertEqual(self.client.get("/agent/v1/tasks").status_code, 403)


def json_dumps(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


class McpToolContractTests(TestCase):
    """A tool the model cannot see or call is not shipped."""

    def _definition(self, name: str) -> dict:
        matches = [tool for tool in TOOL_DEFINITIONS if tool["name"] == name]
        self.assertEqual(len(matches), 1, f"expected exactly one {name} tool definition")
        return matches[0]

    def test_save_note_is_advertised_and_dispatchable(self):
        self.assertIn("save_note", TOOL_FUNCTIONS)
        definition = self._definition("save_note")
        self.assertEqual(definition["inputSchema"]["required"], ["task_id", "summary_markdown"])

    def test_every_advertised_tool_is_dispatchable_and_vice_versa(self):
        self.assertEqual({tool["name"] for tool in TOOL_DEFINITIONS}, set(TOOL_FUNCTIONS))

    def test_every_advertised_property_is_a_real_function_parameter(self):
        # _call_tool silently drops arguments the function does not accept, so a
        # schema that advertises a name the function lacks fails at runtime by
        # ignoring the value instead of erroring.
        for tool in TOOL_DEFINITIONS:
            with self.subTest(tool=tool["name"]):
                parameters = set(inspect.signature(TOOL_FUNCTIONS[tool["name"]]).parameters)
                self.assertLessEqual(set(tool["inputSchema"]["properties"]), parameters)
