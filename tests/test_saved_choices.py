"""Requirement (owner, 2026-10-23, decision 6.31): the text model that writes
notes, speaker labels and auto export to Feishu are saved in the service, and a
task that does not name them uses the saved ones. The page and an agent
submitting the same recording get the same choices; a task that names its own
value (an agent passing speaker_diarization=false, a provider) keeps it.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.core.local_config import as_recorded, save_preferences, saved_choices
from backend.core.local_entry_guards import local_ai_kwargs
from backend.core.task_submission import _collect_options, _local_media_job_context, _stored_queue_options, local_path_options


@pytest.fixture
def saved(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setenv("FLUENTFLOW_CONFIG_PATH", str(path))
    for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "KIMI_API_KEY", "MOONSHOT_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY", "MIMO_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return lambda **prefs: save_preferences(prefs)


def _ctx(tmp_path, options):
    # The context records the running loop, as it does inside a request.
    async def build():
        return _local_media_job_context(
            task_id="t1", client_id=None, source_type="upload", source_filename="a.mp4",
            raw_title="a", display_title="a", suffix=".mp4", td=str(tmp_path), in_path=tmp_path / "a.mp4",
            content=b"", source_file_size_mb=None, duration_preflight_sec=None, options=options,
        )
    return asyncio.run(build())


def test_the_page_and_an_agent_get_the_same_choices_for_the_same_recording(saved, tmp_path):
    saved(ai_provider="kimi", ai_model="kimi-k9", speaker_diarization=True, export_to_lark=True)
    page_options = _collect_options()  # what the upload route stores when the page sends none
    page = _ctx(tmp_path, page_options)
    agent_options, _ = local_path_options({}, file_count=1)
    agent = _ctx(tmp_path, agent_options)
    assert (page.diarization_requested, page.do_lark) == (True, True)
    assert (agent.diarization_requested, agent.do_lark) == (True, True)
    kwargs = local_ai_kwargs(ai_provider=page.ai_provider, ai_model=page.ai_model)
    assert (kwargs["provider"], kwargs["model"]) == ("kimi", "kimi-k9")
    assert local_ai_kwargs(ai_provider=agent.ai_provider, ai_model=agent.ai_model) == kwargs


def test_what_a_task_names_wins_over_the_saved_choice(saved, tmp_path):
    saved(ai_provider="kimi", ai_model="kimi-k9", speaker_diarization=True, export_to_lark=True)
    agent_options, _ = local_path_options({"speaker_diarization": False, "export_to_lark": "false"}, file_count=1)
    ctx = _ctx(tmp_path, agent_options)
    assert (ctx.diarization_requested, ctx.do_lark) == (False, False)
    named = local_ai_kwargs(ai_provider="deepseek")
    assert named["provider"] == "deepseek" and "model" not in named
    assert local_ai_kwargs(ai_provider="kimi")["model"] == "kimi-k9"


def test_nothing_saved_keeps_the_old_defaults(saved, tmp_path):
    ctx = _ctx(tmp_path, _collect_options())
    assert (ctx.diarization_requested, ctx.do_lark) == (False, False)
    assert saved_choices({}) == {"lark_export_route": "auto"}


def test_an_automatic_export_goes_the_way_settings_says(saved, tmp_path):
    saved(export_to_lark=True, lark_export_route="local_cli", lark_folder_token="fldcnX")
    ctx = _ctx(tmp_path, _collect_options())
    assert (ctx.do_lark, ctx.lark_export_route, ctx.folder_token) == (True, "local_cli", "fldcnX")
    named = _ctx(tmp_path, _collect_options(lark_export_route="openapi"))
    assert named.lark_export_route == "openapi"


def test_saving_refuses_an_unknown_provider(saved):
    with pytest.raises(ValueError):
        saved(ai_provider="nope")
    assert saved(ai_provider="QWEN")["ai_provider"] == "qwen"


# Review 2026-10-23: a task records the choices it ran with, and a retry runs
# with those, not with whatever Settings says at retry time.
def test_a_task_records_the_filled_in_choices(saved):
    saved(speaker_diarization=True, export_to_lark=True)
    stored = _collect_options()
    assert (stored["speaker_diarization"], stored["export_to_lark"]) == ("true", "true")


def test_a_retry_runs_with_what_the_task_recorded(saved, tmp_path):
    saved(speaker_diarization=False, export_to_lark=False)
    recorded = _collect_options()
    saved(speaker_diarization=True, export_to_lark=True)
    job = {"metadata": {"queue_options": recorded}}
    ctx = _ctx(tmp_path, _stored_queue_options(job))
    assert (ctx.diarization_requested, ctx.do_lark) == (False, False)


def test_an_older_task_that_never_recorded_them_retries_with_them_off(saved, tmp_path):
    # Older pages sent these only when on; a missing one meant off.
    saved(speaker_diarization=True, export_to_lark=True)
    ctx = _ctx(tmp_path, _stored_queue_options({"metadata": {"queue_options": {"ai_provider": "deepseek"}}}))
    assert (ctx.diarization_requested, ctx.do_lark) == (False, False)
    assert as_recorded({"export_to_lark": "true"})["export_to_lark"] == "true"



# Review 2026-10-23 (decision 6.31): a saved provider with no key does not stop
# notes while another provider has one; Settings says which one writes.
def test_a_saved_provider_without_a_key_falls_back_to_one_that_has_one(saved, monkeypatch):
    from backend.core.local_entry_guards import text_model_in_use

    saved(ai_provider="deepseek", ai_model="deepseek-x")
    monkeypatch.setenv("KIMI_API_KEY", "sk-kimi")
    kwargs = local_ai_kwargs()
    assert kwargs["provider"] == "kimi" and "model" not in kwargs
    assert text_model_in_use() == {"provider": "kimi", "model": "", "saved_provider": "deepseek", "saved_model": "deepseek-x"}
    # Named by the task, the provider is used as named.
    assert local_ai_kwargs(ai_provider="deepseek")["provider"] == "deepseek"
    # With the saved provider's key there, it writes.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
    assert (local_ai_kwargs()["provider"], local_ai_kwargs()["model"]) == ("deepseek", "deepseek-x")


def test_settings_says_when_the_saved_provider_is_not_the_one_writing(saved, monkeypatch):
    from backend.core import workflow_status as ws

    saved(ai_provider="deepseek")
    monkeypatch.setenv("KIMI_API_KEY", "sk-kimi")
    monkeypatch.setattr(ws.visual_note_channel, "resolve_channel", lambda *a, **k: ws.visual_note_channel.Channel(
        name="anthropic_api_key", label="", model="", unavailable_reason="no", write=lambda *a, **k: None))
    monkeypatch.setattr(ws, "load_project_env", lambda: None)
    monkeypatch.setattr(ws, "_lark_cli_ready", lambda: True)
    monkeypatch.setattr(ws, "diarization_status", lambda: {"available": True})
    notes = next(step for step in ws.workflow_status()["steps"] if step["id"] == "notes")
    assert notes["summary"]["zh"].startswith("设置里存的是 DeepSeek，它没有 Key，现在由Kimi（月之暗面）只按转录稿写")


def test_the_auto_export_line_reads_the_saved_choice(saved, monkeypatch):
    from backend.core import workflow_status as ws

    monkeypatch.setattr(ws, "load_project_env", lambda: None)
    monkeypatch.setattr(ws, "_lark_cli_ready", lambda: True)
    monkeypatch.setattr(ws, "diarization_status", lambda: {"available": True})

    def outputs():
        return next(step for step in ws.workflow_status({"skip_ai_summary": "1"})["steps"] if step["id"] == "outputs")["summary"]["zh"]

    assert "要导出到飞书时交给你的 agent" in outputs()
    saved(export_to_lark=True)
    assert "处理完自动导出到飞书" in outputs()
