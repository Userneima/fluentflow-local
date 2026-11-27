"""Requirements for the guided first use (decisions 7.16, 1.22; 2026-11-27).

- A machine is guided when its record is not done and it has no real task; the
  MCP self-check's task is not a real one, and a machine with tasks from before
  (an old user updating) is never guided.
- record_first_use saves need (1 to 6, or the user's own words), material and done
  in the first_use section of fluentflow_config.json, keeping every other section;
  get_status carries the record and should_onboard; while the guide is due it also
  says whether the speech model is on disk.
- wait_task tells "downloading the speech model" apart from transcribing.
- The install instructions and the skill carry the trigger phrase, the six options
  and the instruction not to make the user restart.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import first_use, job_store, local_config, runtime_paths
from backend.core.local_request_scope import LOCAL_OWNER_ID as OWNER
from backend.routers.local_agent import router
from scripts import fluentflow_mcp_server as mcp
from scripts.check_mcp_server import run_check  # noqa: F401 - the self-check module the title comes from
from scripts.fluentflow_mcp import core

ROOT = Path(__file__).resolve().parents[1]
REAL_EARLIER_INSTALL_CHECK = first_use._earlier_install_had_tasks
TOKEN = "first-use-token"


@pytest.fixture
def no_tasks(monkeypatch, tmp_path):
    """An empty task database of its own: the shared one holds other tests' tasks."""
    db = tmp_path / "jobs.sqlite"
    real = job_store.has_job_not_named
    monkeypatch.setattr(job_store, "has_job_not_named",
                        lambda names, db_path=db, **kw: real(names, db_path=db_path, **kw))
    # The platform's own data folder is the real user's: never read it here.
    monkeypatch.setattr(first_use, "_earlier_install_had_tasks", lambda started: False)
    monkeypatch.setattr(first_use, "_speech_model_ready", lambda: False)
    return db


@pytest.fixture
def client(monkeypatch, own_empty_settings_file, no_tasks):
    app = FastAPI()
    app.include_router(router)
    test_client = TestClient(app)
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", TOKEN)
    headers = {"x-fluentflow-access-token": TOKEN}

    def bridge(method, path, **kw):
        response = test_client.request(method, path, json=kw.get("payload"), headers=headers)
        if response.status_code >= 400:
            return {"ok": False, "status": response.status_code, "error": response.json().get("detail")}
        return response.json()

    monkeypatch.setattr(core, "_agent_request", bridge)
    return test_client, headers


def _tool(name: str, arguments: dict | None = None) -> dict:
    return json.loads(mcp._call_tool(name, arguments or {})["content"][0]["text"])


# --- who is guided ------------------------------------------------------------

def test_a_new_machine_is_guided_and_says_whether_the_model_is_there(own_empty_settings_file, no_tasks):
    status = first_use.status()
    assert status["should_onboard"] is True
    assert status["done"] is False and status["need"] is None and status["material"] == ""
    assert status["speech_model_ready"] is False


def test_the_self_check_task_is_not_a_real_one(own_empty_settings_file, no_tasks):
    job_store.upsert_job(task_id=uuid.uuid4().hex, status="completed", client_id=OWNER,
                         source_filename=first_use.SELF_CHECK_TITLES[0], db_path=no_tasks)
    assert first_use.status()["should_onboard"] is True


def test_the_self_check_still_submits_under_that_title():
    text = (ROOT / "scripts" / "check_mcp_server.py").read_text(encoding="utf-8")
    assert all(f'"{title}"' in text for title in first_use.SELF_CHECK_TITLES)


def test_an_old_user_with_tasks_is_never_guided(own_empty_settings_file, no_tasks):
    job_store.upsert_job(task_id=uuid.uuid4().hex, status="completed", client_id=OWNER,
                         source_filename="第三课.mp4", db_path=no_tasks)
    status = first_use.status()
    assert status["should_onboard"] is False
    assert "speech_model_ready" not in status, "only said while the guide is due"


def test_done_ends_the_guide(own_empty_settings_file, no_tasks):
    first_use.record({"done": True})
    assert first_use.status()["should_onboard"] is False


# --- the record -----------------------------------------------------------------

def test_recording_keeps_every_other_setting(own_empty_settings_file, no_tasks):
    local_config.save_section("secrets", {"DEEPSEEK_API_KEY": "kept"})
    local_config.save_preferences({"cpu_usage": "light"})

    first_use.record({"need": "3"})
    first_use.record({"material": " https://v.douyin.com/abc/ "})
    saved = first_use.record({"done": True})

    assert saved["need"] == 3 and saved["material"] == "https://v.douyin.com/abc/" and saved["done"] is True
    assert saved["updated_at"]
    config = local_config.load_config()
    assert config["secrets"] == {"DEEPSEEK_API_KEY": "kept"}
    assert config["preferences"]["cpu_usage"] == "light"
    assert config["first_use"]["need"] == 3


def test_need_is_one_to_six_or_the_users_words(own_empty_settings_file, no_tasks):
    assert first_use.record({"need": 6})["need"] == 6
    assert first_use.record({"need": "想把播客做成文字稿"})["need"] == "想把播客做成文字稿"
    for wrong in (0, 7, "9", True, "", 2.5):
        with pytest.raises(ValueError):
            first_use.record({"need": wrong})


def test_an_empty_material_clears_it_and_unknown_fields_are_refused(own_empty_settings_file, no_tasks):
    first_use.record({"material": "/Users/me/课.mp4"})
    assert first_use.record({"material": ""})["material"] == ""
    with pytest.raises(ValueError):
        first_use.record({"needs": 1})
    with pytest.raises(ValueError):
        first_use.record({})
    with pytest.raises(ValueError):
        first_use.record({"done": "yes"})


# --- through the service and the MCP tools -------------------------------------

def test_get_status_and_record_first_use_through_the_tools(client):
    assert _tool("get_status")["first_use"]["should_onboard"] is True

    saved = _tool("record_first_use", {"need": 2, "material": "https://v.douyin.com/x/"})
    assert saved["ok"] is True
    assert saved["first_use"]["need"] == 2 and saved["first_use"]["material"] == "https://v.douyin.com/x/"

    after = _tool("record_first_use", {"done": True})
    assert after["first_use"]["done"] is True and after["first_use"]["need"] == 2, "a field left out keeps its value"
    assert _tool("get_status")["first_use"]["should_onboard"] is False


def test_a_wrong_need_is_said_plainly(client):
    answer = _tool("record_first_use", {"need": 9})
    assert answer["ok"] is False and "1 到 6" in str(answer.get("error"))


def test_the_command_line_lists_record_first_use():
    assert "record_first_use" in {d["name"] for d in mcp.TOOL_DEFINITIONS}


# --- the model download while waiting -------------------------------------------

def _waiting_answer(client, stt_status: str) -> dict:
    test_client, headers = client
    task_id = uuid.uuid4().hex
    job_store.upsert_job(task_id=task_id, status="running", stage="stt", progress=22, client_id=OWNER,
                         metadata={"stt_status": stt_status})
    try:
        response = test_client.post(f"/agent/v1/tasks/{task_id}/wait", json={"timeout_seconds": 0}, headers=headers)
        assert response.status_code == 200, response.text
        return response.json()
    finally:
        job_store.delete_jobs([task_id])


def test_wait_task_says_the_speech_model_is_downloading(client):
    answer = _waiting_answer(client, "downloading_model")
    assert answer["done"] is False
    assert answer["task"]["downloading_model"] is True


def test_wait_task_does_not_say_so_while_transcribing(client):
    answer = _waiting_answer(client, "transcribing_segments")
    assert "downloading_model" not in answer["task"]


# --- what the agents read --------------------------------------------------------

SIX_OPTIONS = [
    "1. 网课、讲座视频，变成能跟着学的笔记",
    "2. 抖音、B 站、YouTube 收藏的视频，变成能直接看的文字笔记",
    "3. 会议、访谈的录音录像，变成逐字稿和整理好的纪要",
    "4. 只要逐字稿或字幕",
    "5. 剪掉录音里的空白和气口，顺便压小体积",
    "6. 其他（说说你的情况）",
]
SKILL_DIR = ROOT / "agent" / "skills" / "fluentflow"


def test_the_guide_carries_the_six_options_word_for_word():
    guide = (SKILL_DIR / "first-use.md").read_text(encoding="utf-8")
    wording = (ROOT / "docs" / "方案" / "2026-11-27-上手引导话术.md").read_text(encoding="utf-8")
    for option in SIX_OPTIONS:
        assert option in guide and option in wording, option
    assert "整个过程你只打了" in guide and "学完了" in guide


def test_the_skill_is_found_by_the_phrase_and_points_at_the_guide():
    skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    description = re.search(r"^description: (.*)$", skill, re.M).group(1)
    assert "带我用一次 FluentFlow" in description
    assert len(description) <= 1024, "Claude Code cuts a skill description at 1,024 characters"
    assert "first-use.md" in skill and "should_onboard" in skill


def test_the_install_conversation_does_not_send_the_user_away():
    text = (ROOT / "AGENT_INSTALL.md").read_text(encoding="utf-8")
    section = text[text.index("## First use"):text.index("## Tell the user how to use it")]
    assert "do not ask them to restart" in section
    assert "first-use.md" in section and "record_first_use" in section and "should_onboard" in section
    assert "带我用一次 FluentFlow" in section


# --- review of 3b677fa (2026-11-27) -----------------------------------------------

def _stamp(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).astimezone().isoformat(timespec="seconds")


def test_the_guides_own_task_does_not_end_it(monkeypatch, own_empty_settings_file, no_tasks):
    monkeypatch.setattr(first_use, "_now", lambda: _stamp(-30))
    first_use.record({"need": 1, "material": "/Users/me/课.mp4"})
    job_store.upsert_job(task_id=uuid.uuid4().hex, status="running", client_id=OWNER,
                         source_filename="课.mp4", db_path=no_tasks)

    status = first_use.status()

    assert status["should_onboard"] is True, "submitted after the guide started: still the guide's"
    assert "speech_model_ready" in status and "guide_path" in status


def test_a_task_from_before_the_guide_started_still_counts(monkeypatch, own_empty_settings_file, no_tasks):
    job_store.upsert_job(task_id=uuid.uuid4().hex, status="completed", client_id=OWNER,
                         source_filename="旧课.mp4", db_path=no_tasks)
    monkeypatch.setattr(first_use, "_now", lambda: _stamp(30))
    first_use.record({"need": 2})
    assert first_use.status()["should_onboard"] is False


def test_started_at_never_moves(monkeypatch, own_empty_settings_file, no_tasks):
    monkeypatch.setattr(first_use, "_now", lambda: "2026-11-27T10:00:00+08:00")
    first_use.record({"need": 1})
    monkeypatch.setattr(first_use, "_now", lambda: "2026-11-27T11:00:00+08:00")
    saved = first_use.record({"material": "x"})
    assert saved["started_at"] == "2026-11-27T10:00:00+08:00" and saved["updated_at"] == "2026-11-27T11:00:00+08:00"


def test_get_status_points_any_agent_at_the_guide(own_empty_settings_file, no_tasks):
    status = first_use.status()
    path = Path(status["guide_path"])
    assert path.is_absolute() and path.is_file() and path.name == "first-use.md"
    definition = next(d for d in mcp.TOOL_DEFINITIONS if d["name"] == "get_status")
    assert "guide_path" in definition["description"]


def test_a_null_need_is_refused_not_stored(client):
    test_client, headers = client
    response = test_client.post("/agent/v1/first-use", json={"need": None}, headers=headers)
    assert response.status_code == 400
    assert "first_use" not in local_config.load_config()


def test_concurrent_saves_keep_both_sections(own_empty_settings_file):
    def save(name: str) -> None:
        for i in range(40):
            local_config.save_section(name, {"i": i})

    threads = [threading.Thread(target=save, args=(name,)) for name in ("alpha", "beta", "gamma")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    config = local_config.load_config()
    assert config["alpha"] == {"i": 39} and config["beta"] == {"i": 39} and config["gamma"] == {"i": 39}


def test_a_broken_settings_file_is_kept_aside_not_wiped(own_empty_settings_file):
    target = local_config.config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"secrets": {"DEEPSEEK_API_KEY": "sk-keep"', encoding="utf-8")

    local_config.save_section("first_use", {"done": True})

    backups = list(target.parent.glob(f"{target.name}.broken-*"))
    assert len(backups) == 1
    assert "sk-keep" in backups[0].read_text(encoding="utf-8")
    assert local_config.load_config() == {"first_use": {"done": True}}
    assert not list(target.parent.glob(f"{target.name}.*.tmp")), "no temporary file left"


def test_the_install_checks_for_an_earlier_task_database_before_asking():
    text = (ROOT / "AGENT_INSTALL.md").read_text(encoding="utf-8")
    section = text[text.index("## First use"):text.index("## Tell the user how to use it")]
    assert runtime_paths.default_job_db_path().name == "fluentflow_jobs.sqlite"
    assert runtime_paths.APP_NAME == "FluentFlow"
    for place in ("~/Library/Application Support/FluentFlow/fluentflow_jobs.sqlite",
                  "<folder>\\data\\fluentflow_jobs.sqlite", "%APPDATA%\\FluentFlow\\fluentflow_jobs.sqlite"):
        assert place in section, place
    assert "processed as an ordinary task" in section
    assert "no `first_use`" in section, "an older release without the record skips the guide"
    assert "<folder>\\app" in section


def test_the_guide_never_promises_a_vtt_in_the_folder():
    guide = (SKILL_DIR / "first-use.md").read_text(encoding="utf-8")
    assert "txt、srt、vtt" not in guide and "srt、vtt 字幕文件" not in guide
    assert "一两个 G" not in guide and "1.5 到 3 个 G" in guide


# --- second review (2026-11-27) ----------------------------------------------------

def test_a_delivered_guide_never_marked_done_does_not_start_again(monkeypatch, own_empty_settings_file, no_tasks):
    monkeypatch.setattr(first_use, "_now", lambda: _stamp(-30))
    first_use.record({"need": 2, "material": "https://v.douyin.com/abc/"})
    task_id = uuid.uuid4().hex
    job_store.upsert_job(task_id=task_id, status="running", client_id=OWNER, source_filename="abc", db_path=no_tasks)
    assert first_use.status()["should_onboard"] is True, "still running: another conversation carries it on"

    job_store.upsert_job(task_id=task_id, status="completed", client_id=OWNER, db_path=no_tasks)

    assert first_use.status()["should_onboard"] is False, "delivered: the material is not submitted again"


def test_a_failed_guide_task_does_not_end_the_guide(monkeypatch, own_empty_settings_file, no_tasks):
    monkeypatch.setattr(first_use, "_now", lambda: _stamp(-30))
    first_use.record({"need": 2})
    job_store.upsert_job(task_id=uuid.uuid4().hex, status="failed", client_id=OWNER, source_filename="x",
                         db_path=no_tasks)
    assert first_use.status()["should_onboard"] is True


def test_tasks_left_by_an_earlier_install_count(monkeypatch, tmp_path, own_empty_settings_file, no_tasks):
    # The fixture hides the platform data folder; this test points it at its own copy.
    monkeypatch.setattr(first_use, "_earlier_install_had_tasks", REAL_EARLIER_INSTALL_CHECK)
    earlier = tmp_path / "AppData" / "FluentFlow"
    earlier.mkdir(parents=True)
    monkeypatch.setattr(runtime_paths, "default_app_data_root", lambda: earlier)
    assert first_use.status()["should_onboard"] is True, "no earlier database: guided"

    job_store.upsert_job(task_id=uuid.uuid4().hex, status="completed", client_id=OWNER,
                         source_filename="旧课.mp4", db_path=earlier / "fluentflow_jobs.sqlite")
    assert first_use.status()["should_onboard"] is False


def test_an_earlier_database_is_only_read(tmp_path):
    db = tmp_path / "fluentflow_jobs.sqlite"
    assert job_store.has_job_not_named(first_use.SELF_CHECK_TITLES, db_path=db, read_only=True) is False
    assert not db.exists(), "reading never creates it"
    db.write_text("not a database", encoding="utf-8")
    assert job_store.has_job_not_named(first_use.SELF_CHECK_TITLES, db_path=db, read_only=True) is False
    assert db.read_text(encoding="utf-8") == "not a database"


def test_a_settings_file_that_cannot_be_read_fails_the_save_and_stays(monkeypatch, own_empty_settings_file):
    target = local_config.config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"secrets": {"K": "v"}}', encoding="utf-8")
    real_read = Path.read_bytes

    def locked(self):
        if self == target:
            raise PermissionError("another program has it open")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", locked)
    with pytest.raises(PermissionError):
        local_config.save_section("first_use", {"done": True})
    monkeypatch.undo()

    assert target.read_text(encoding="utf-8") == '{"secrets": {"K": "v"}}'
    assert not list(target.parent.glob(f"{target.name}.broken-*")), "not damaged, so no backup"


def test_the_settings_path_is_resolved_inside_the_lock(monkeypatch, tmp_path):
    held = {"now": False, "seen": None}

    class Lock:
        def __enter__(self):
            held["now"] = True

        def __exit__(self, *exc):
            held["now"] = False

    def path():
        held["seen"] = held["now"]
        return tmp_path / "fluentflow_config.json"

    monkeypatch.setattr(local_config, "_WRITE_LOCK", Lock())
    monkeypatch.setattr(local_config, "config_path", path)
    local_config.save_section("x", 1)
    assert held["seen"] is True, "the copy from the old place runs under the lock"


def test_a_cut_reminder_choice_keeps_one_saved_at_the_same_moment(monkeypatch, own_empty_settings_file):
    from backend.core import cut_trust
    from tests.test_cut_trust import _qualified, _task

    judge = _qualified()
    task = _task(judge, asked=1)
    real_update = local_config.update_config

    def with_another(change, path=None):
        # Another judge's choice lands between this choice's start and its write.
        return real_update(lambda config: change({**config, cut_trust.CHOICES_KEY: {"someone-else": {"kept_at": "x"}}}),
                           path)

    monkeypatch.setattr(local_config, "update_config", with_another)
    cut_trust.choose(job_store.get_job(task), "keep")

    saved = local_config.load_config()[cut_trust.CHOICES_KEY]
    assert "someone-else" in saved and judge in saved


def test_no_install_time_promise_and_deliveries_say_only_what_exists():
    guide = (SKILL_DIR / "first-use.md").read_text(encoding="utf-8")
    assert "「收到。等装好我就直接开始处理这条。」" in guide
    assert guide.count("不用再发一遍") == 1, "only in the line for a recorded choice"
    recorded = guide.index("- Recorded:")
    assert recorded < guide.index("不用再发一遍") < guide.index("- Not recorded:")
    assert "only when the task's result has it" in guide
    assert "PDF 这次没生成出来" in guide and "这次没能分出不同的人" in guide
    assert "「过程文件」文件夹" in guide.split("- 4: 「转好了")[1].split("\n")[0]
    assert "- 6: 「做好了。" in guide and "- 6: 「以后也是跟我说话就行。" in guide


def test_windows_command_lines_quote_paths_and_send_json_on_stdin():
    text = (ROOT / "AGENT_INSTALL.md").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if "python.exe" in line and "fluentflow_mcp_server.py" in line]
    assert lines
    for line in lines:
        assert '& "' in line and '"<folder>\\app\\scripts\\fluentflow_mcp_server.py"' in line, line
        if "call get_task" in line:
            assert line.rstrip().endswith(" -") and line.lstrip().startswith("'{"), line
    assert "$OutputEncoding = [System.Text.UTF8Encoding]::new($false)" in text


def test_the_install_skips_a_machine_whose_settings_say_done():
    text = (ROOT / "AGENT_INSTALL.md").read_text(encoding="utf-8")
    section = text[text.index("## First use"):text.index("## Tell the user how to use it")]
    assert "fluentflow_config.json" in section and '"done": true' in section
