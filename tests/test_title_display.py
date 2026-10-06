"""A recording's name is its title, dots and all.

Recordings in this archive are named "5.投资人视角下的AI浪潮.mp4" and
"1.2-1.3 批判性思维_合并.m4a". The user must see the whole name without the
file extension — never "5" or "1.2-1", which is what treating everything after
the last dot as an extension produced. Titles already cut that way by an
earlier version are put back on startup, and nothing else is touched.
"""

from __future__ import annotations

import pytest

from backend.core import job_store
from backend.core.title_display import display_title_for_user, strip_extension


# ── what the user sees for a file name ──────────────────────────────────────

@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("5.投资人视角下的AI浪潮.mp4", "5.投资人视角下的AI浪潮"),
        ("1.2-1.3 批判性思维_合并.m4a", "1.2-1.3 批判性思维_合并"),
        ("lecture.mp4", "lecture"),
        ("lecture.MP4", "lecture"),
    ],
)
def test_a_file_name_becomes_its_title_without_the_extension(filename, expected):
    assert display_title_for_user(filename) == expected
    assert display_title_for_user(filename, filename) == expected


def test_a_title_with_dots_but_no_media_extension_is_shown_whole():
    """A stem that was already stripped once must not lose anything more."""
    assert display_title_for_user("5.投资人视角下的AI浪潮") == "5.投资人视角下的AI浪潮"
    assert display_title_for_user("1.2-1.3 批判性思维_合并") == "1.2-1.3 批判性思维_合并"
    assert display_title_for_user("v2.0 发布会") == "v2.0 发布会"


def test_a_name_with_no_extension_is_unchanged():
    assert display_title_for_user("第三讲 复盘") == "第三讲 复盘"
    assert strip_extension("第三讲 复盘") == "第三讲 复盘"


def test_an_empty_title_falls_back_to_the_file_name():
    assert display_title_for_user("", "lecture.mp4") == "lecture"
    assert display_title_for_user(None, "5.投资人视角下的AI浪潮.mp4") == "5.投资人视角下的AI浪潮"


def test_a_generated_video_id_prefix_is_hidden_but_the_dots_stay():
    """Downloaded links are stored as "<id>-<title>"; the id is not part of the title."""
    assert display_title_for_user("BV1xx411c7mD-5.投资人视角下的AI浪潮.mp4") == "5.投资人视角下的AI浪潮"
    assert display_title_for_user("1700000000000-lecture.mp4") == "lecture"


# ── startup puts back the titles an earlier version cut ────────────────────

def _seed(db, task_id, *, raw_title, display_title, result_title=None):
    job_store.upsert_job(
        task_id=task_id,
        status="completed",
        client_id="me",
        db_path=db,
        metadata={"raw_title": raw_title, "display_title": display_title, "route": "/process"},
        result={
            "task_id": task_id,
            "display_title": result_title if result_title is not None else display_title,
            "summary_markdown": "# 笔记",
            "transcript_text": "正文",
        },
    )


def test_startup_repairs_only_the_titles_that_were_cut_at_the_first_dot(tmp_path):
    """After the repair the truncated task shows its full name everywhere the
    page reads it (metadata and result); a correct title and a title the user
    typed themselves are left exactly as they were."""
    db = tmp_path / "jobs.sqlite"
    _seed(db, "cut", raw_title="5.投资人视角下的AI浪潮", display_title="5")
    _seed(db, "fine", raw_title="1.2-1.3 批判性思维_合并", display_title="1.2-1.3 批判性思维_合并")
    _seed(db, "chosen", raw_title="5.投资人视角下的AI浪潮", display_title="投资人讲座")
    before_fine = job_store.get_job("fine", db_path=db)
    before_chosen = job_store.get_job("chosen", db_path=db)

    changed = job_store.repair_truncated_display_titles(db_path=db)

    assert changed == 1
    repaired = job_store.get_job("cut", db_path=db)
    assert repaired["metadata"]["display_title"] == "5.投资人视角下的AI浪潮"
    assert repaired["result"]["display_title"] == "5.投资人视角下的AI浪潮"
    assert repaired["metadata"]["raw_title"] == "5.投资人视角下的AI浪潮", "the raw title is the source, not a target"
    assert repaired["result"]["transcript_text"] == "正文", "the rest of the result survives the rewrite"
    assert job_store.get_job("fine", db_path=db) == before_fine
    assert job_store.get_job("chosen", db_path=db) == before_chosen


def test_the_repair_is_idempotent(tmp_path):
    db = tmp_path / "jobs.sqlite"
    _seed(db, "cut", raw_title="1.2-1.3 批判性思维_合并", display_title="1")

    assert job_store.repair_truncated_display_titles(db_path=db) == 1
    assert job_store.repair_truncated_display_titles(db_path=db) == 0
    assert job_store.get_job("cut", db_path=db)["metadata"]["display_title"] == "1.2-1.3 批判性思维_合并"


def test_the_repair_follows_the_title_into_the_video_source_record(tmp_path):
    """A downloaded link keeps a second copy of the title under video_source;
    the task list reads that one for link tasks, so it is fixed as well."""
    db = tmp_path / "jobs.sqlite"
    job_store.upsert_job(
        task_id="link",
        status="completed",
        client_id="me",
        db_path=db,
        metadata={
            "raw_title": "5.投资人视角下的AI浪潮",
            "display_title": "5",
            "video_source": {"display_title": "5", "provider": "yt-dlp"},
        },
        result={"task_id": "link", "display_title": "5"},
    )

    job_store.repair_truncated_display_titles(db_path=db)

    metadata = job_store.get_job("link", db_path=db)["metadata"]
    assert metadata["video_source"] == {"display_title": "5.投资人视角下的AI浪潮", "provider": "yt-dlp"}
