"""Requirement (owner, 2026-10-23, decision 6.31): the text model that writes
notes, speaker labels and auto export to Feishu are saved in the service, and a
task that does not name them uses the saved ones. The page and an agent
submitting the same recording get the same choices; a task that names its own
value (an agent passing speaker_diarization=false, a provider) keeps it.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.core.local_config import save_preferences, saved_choices
from backend.core.local_entry_guards import local_ai_kwargs
from backend.routers.local_processing import _local_media_job_context, local_path_options


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
    page = _ctx(tmp_path, {})  # the page no longer sends these
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
    ctx = _ctx(tmp_path, {})
    assert (ctx.diarization_requested, ctx.do_lark) == (False, False)
    assert saved_choices({}) == {"lark_export_route": "auto"}


def test_an_automatic_export_goes_the_way_settings_says(saved, tmp_path):
    saved(export_to_lark=True, lark_export_route="local_cli", lark_folder_token="fldcnX")
    ctx = _ctx(tmp_path, {})
    assert (ctx.do_lark, ctx.lark_export_route, ctx.folder_token) == (True, "local_cli", "fldcnX")
    named = _ctx(tmp_path, {"lark_export_route": "openapi"})
    assert named.lark_export_route == "openapi"


def test_saving_refuses_an_unknown_provider(saved):
    with pytest.raises(ValueError):
        saved(ai_provider="nope")
    assert saved(ai_provider="QWEN")["ai_provider"] == "qwen"
