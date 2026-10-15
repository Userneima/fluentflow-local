"""When the original is not where it was, or is not the same file any more.

Requirements (owner, 2026-10-15):
1. A recording in place and as long as the one processed is used.
2. A recording moved to another folder, same name, is found and the task
   follows it.
3. A different file under the same name is refused, with both lengths.
4. A missing recording is refused with where it was and what to do.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from backend.core import job_store, original_locator, silence_cuts


@pytest.fixture
def lengths(monkeypatch):
    table: dict[str, float] = {}
    monkeypatch.setattr(silence_cuts, "media_duration_seconds", lambda path, *a, **k: table[str(path)])
    return table


def _task(path: Path, seconds: float = 600.0) -> str:
    task_id = f"ol-{uuid.uuid4().hex[:8]}"
    job_store.upsert_job(
        task_id=task_id, status="completed",
        metadata={"folder_intake": {"original_path": str(path)}},
        result={"debreath": {"survey": {"report": {"before_seconds": seconds}}}},
    )
    return task_id


def test_a_recording_in_place_is_used(tmp_path, lengths):
    path = tmp_path / "会议.mp4"
    path.write_bytes(b"x")
    lengths[str(path)] = 600.4

    assert original_locator.locate(_task(path), search=lambda name: []) == path


def test_a_moved_recording_is_found_and_followed(tmp_path, lengths):
    old, new = tmp_path / "a" / "会议.mp4", tmp_path / "b" / "会议.mp4"
    new.parent.mkdir()
    new.write_bytes(b"x")
    other = tmp_path / "c" / "会议.mp4"
    other.parent.mkdir()
    other.write_bytes(b"y")
    lengths.update({str(new): 600.0, str(other): 1200.0})
    task = _task(old)

    found = original_locator.locate(task, search=lambda name: [other, new])

    assert found == new
    assert job_store.get_job(task)["metadata"]["folder_intake"]["original_path"] == str(new)


def test_a_different_file_under_the_same_name_is_refused(tmp_path, lengths):
    path = tmp_path / "会议.mp4"
    path.write_bytes(b"x")
    lengths[str(path)] = 1500.0

    with pytest.raises(original_locator.OriginalMissing) as error:
        original_locator.locate(_task(path), search=lambda name: [])

    assert "10:00" in str(error.value) and "25:00" in str(error.value)


def test_a_missing_recording_says_where_it_was(tmp_path, lengths):
    path = tmp_path / "会议.mp4"

    with pytest.raises(original_locator.OriginalMissing) as error:
        original_locator.locate(_task(path), search=lambda name: [])

    assert str(path) in str(error.value) and "放回原处" in str(error.value)
