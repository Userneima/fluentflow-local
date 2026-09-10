from __future__ import annotations

import os

import pytest

from types import SimpleNamespace

from backend.core.speaker_diarization import (
    SpeakerTurn,
    _load_pyannote_pipeline,
    assign_speakers_to_segments,
    build_speaker_annotated_transcript,
    diarization_status,
    speaker_display_map,
)


def test_assign_speakers_by_largest_time_overlap() -> None:
    segments = [
        {"start": 0.0, "end": 2.0, "text": "hello"},
        {"start": 2.0, "end": 4.0, "text": "world"},
    ]
    turns = [
        SpeakerTurn(start=0.0, end=1.5, speaker="SPEAKER_00"),
        SpeakerTurn(start=1.5, end=4.0, speaker="SPEAKER_01"),
    ]

    assigned = assign_speakers_to_segments(segments, turns)

    assert assigned[0]["speaker"] == "SPEAKER_00"
    assert assigned[1]["speaker"] == "SPEAKER_01"


def test_diarization_status_is_safe_without_optional_dependency() -> None:
    status = diarization_status()
    assert "available" in status
    assert status["backend"] == "pyannote.audio"


def test_load_pyannote_pipeline_prefers_new_token_argument() -> None:
    class Pipeline:
        @classmethod
        def from_pretrained(cls, model: str, **kwargs):
            return {"model": model, "kwargs": kwargs}

    pipeline = _load_pyannote_pipeline(Pipeline, "hf_token")

    assert pipeline["kwargs"] == {"token": "hf_token"}


def test_load_pyannote_pipeline_compat_translates_legacy_hf_token(monkeypatch) -> None:
    calls = []

    def hf_hub_download(*args, **kwargs):
        calls.append(kwargs)
        if "use_auth_token" in kwargs:
            raise TypeError("hf_hub_download() got an unexpected keyword argument 'use_auth_token'")
        return "downloaded"

    fake_hub = SimpleNamespace(hf_hub_download=hf_hub_download)
    monkeypatch.setitem(__import__("sys").modules, "huggingface_hub", fake_hub)

    class Pipeline:
        @classmethod
        def from_pretrained(cls, model: str, **kwargs):
            if "token" in kwargs:
                raise TypeError("from_pretrained() got an unexpected keyword argument 'token'")
            fake_hub.hf_hub_download(repo_id=model, use_auth_token=kwargs.get("use_auth_token"))
            return {"model": model}

    pipeline = _load_pyannote_pipeline(Pipeline, "hf_token")

    assert pipeline["model"] == "pyannote/speaker-diarization-3.1"
    assert calls == [{"repo_id": "pyannote/speaker-diarization-3.1", "token": "hf_token"}]


def test_annotated_transcript_prefixes_labels_and_merges_runs() -> None:
    segments = [
        {"start": 0.0, "end": 1.0, "text": "先说个事", "speaker": "SPEAKER_01"},
        {"start": 1.0, "end": 2.0, "text": "这个方案下周上线", "speaker": "SPEAKER_01"},
        {"start": 2.0, "end": 3.0, "text": "我有个疑问", "speaker": "SPEAKER_00"},
    ]

    text = build_speaker_annotated_transcript(segments)

    assert text == "说话人 A：先说个事 这个方案下周上线\n说话人 B：我有个疑问"


def test_annotated_transcript_labels_by_first_appearance() -> None:
    segments = [
        {"text": "b first", "speaker": "SPEAKER_09"},
        {"text": "a second", "speaker": "SPEAKER_02"},
    ]

    assert speaker_display_map(segments) == {"SPEAKER_09": "说话人 A", "SPEAKER_02": "说话人 B"}


def test_annotated_transcript_is_skipped_for_one_speaker() -> None:
    assert build_speaker_annotated_transcript([{"text": "只有一个人", "speaker": "SPEAKER_00"}]) is None


def test_annotated_transcript_is_skipped_without_speakers() -> None:
    assert build_speaker_annotated_transcript([{"text": "没有说话人字段"}]) is None


def test_note_prompt_carries_attribution_rule_only_when_labeled() -> None:
    from backend.core.ai_summarizer import _compose_note_system_prompt

    plain = _compose_note_system_prompt(None)
    labeled = _compose_note_system_prompt(None, speaker_labeled=True)

    assert "说话人 A" not in plain
    assert "说话人 A" in labeled
    assert "不要根据对话里的称呼" in labeled


def test_diarization_budget_scales_with_audio_and_stays_bounded() -> None:
    from backend.core.media_job_stages import _diarization_timeout_seconds

    # A short clip still gets a floor: model loading is slower than the audio.
    assert _diarization_timeout_seconds(None) == 600.0
    assert _diarization_timeout_seconds(120) == 600.0
    # A long recording gets room to actually finish.
    assert _diarization_timeout_seconds(1229) == 2458.0
    # And a stalled model download cannot hold a task forever.
    assert _diarization_timeout_seconds(9000) == 2700.0


def test_status_reports_local_models_and_needs_no_token(monkeypatch, tmp_path) -> None:
    from backend.core import speaker_diarization as sd

    (tmp_path / "config.yaml").write_text("version: 3.1.0\n", encoding="utf-8")
    monkeypatch.setattr(sd, "default_diarization_model_dir", lambda: tmp_path)
    monkeypatch.setattr(sd, "get_sensitive_setting", lambda *_args, **_kwargs: "")

    status = sd.diarization_status()

    assert status["models_local"] is True
    assert status["auth_configured"] is False
    # Nothing is fetched at run time, so the token stops being a precondition.
    assert status["available"] is status["dependency_installed"]


def test_status_without_local_models_falls_back_to_the_token(monkeypatch, tmp_path) -> None:
    from backend.core import speaker_diarization as sd

    monkeypatch.setattr(sd, "default_diarization_model_dir", lambda: tmp_path)
    monkeypatch.setattr(sd, "get_sensitive_setting", lambda *_args, **_kwargs: "hf_token")

    status = sd.diarization_status()

    assert status["models_local"] is False
    assert status["auth_configured"] is True


def test_torch_full_load_compat_restores_the_environment(monkeypatch) -> None:
    from backend.core.speaker_diarization import _torch_full_load_compat

    monkeypatch.delenv("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", raising=False)
    with _torch_full_load_compat():
        assert os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] == "1"
    assert "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD" not in os.environ


def test_local_models_are_ignored_when_a_checkpoint_is_missing(monkeypatch, tmp_path) -> None:
    """A half-populated dir must not shadow the token route.

    This directory takes priority, so claiming a pipeline whose checkpoints are
    gone would fail every run on a machine where the token would have worked.
    """
    from backend.core import speaker_diarization as sd

    (tmp_path / "segmentation.bin").write_bytes(b"weights")
    (tmp_path / "config.yaml").write_text(
        "pipeline:\n"
        "  params:\n"
        f"    segmentation: {(tmp_path / 'segmentation.bin').as_posix()}\n"
        f"    embedding: {(tmp_path / 'wespeaker.bin').as_posix()}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sd, "default_diarization_model_dir", lambda: tmp_path)

    assert sd.local_model_config() is None


def test_local_models_are_used_when_every_checkpoint_is_present(monkeypatch, tmp_path) -> None:
    from backend.core import speaker_diarization as sd

    (tmp_path / "segmentation.bin").write_bytes(b"weights")
    (tmp_path / "wespeaker.bin").write_bytes(b"weights")
    (tmp_path / "config.yaml").write_text(
        "pipeline:\n"
        "  params:\n"
        f"    segmentation: {(tmp_path / 'segmentation.bin').as_posix()}\n"
        f"    embedding: {(tmp_path / 'wespeaker.bin').as_posix()}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sd, "default_diarization_model_dir", lambda: tmp_path)

    assert sd.local_model_config() == tmp_path / "config.yaml"


def test_config_referencing_hub_repo_ids_is_still_usable(monkeypatch, tmp_path) -> None:
    """A repo id is fetched at run time and is not this check's business."""
    from backend.core import speaker_diarization as sd

    (tmp_path / "config.yaml").write_text(
        "pipeline:\n"
        "  params:\n"
        "    segmentation: pyannote/segmentation-3.0\n"
        "    embedding: pyannote/wespeaker-voxceleb-resnet34-LM\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sd, "default_diarization_model_dir", lambda: tmp_path)

    assert sd.local_model_config() == tmp_path / "config.yaml"


def test_unparseable_config_falls_back_instead_of_claiming_local_models(monkeypatch, tmp_path) -> None:
    from backend.core import speaker_diarization as sd

    (tmp_path / "config.yaml").write_text("pipeline: [unclosed", encoding="utf-8")
    monkeypatch.setattr(sd, "default_diarization_model_dir", lambda: tmp_path)

    assert sd.local_model_config() is None


def test_torch_compat_serialises_overlapping_loads() -> None:
    """Two loads must not have the first to finish undo the second's relaxation."""
    import threading

    from backend.core.speaker_diarization import _torch_full_load_compat

    seen: list[str | None] = []
    started = threading.Event()

    def inner() -> None:
        with _torch_full_load_compat():
            seen.append(os.environ.get("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"))

    with _torch_full_load_compat():
        worker = threading.Thread(target=lambda: (started.set(), inner()))
        worker.start()
        started.wait(1)
        # The second load is still waiting for this one to release, so it has
        # not cleared the variable underneath it.
        assert os.environ.get("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD") == "1"
        assert seen == []
    worker.join(5)
    assert seen == ["1"]


def test_diarization_runs_off_the_shared_executor() -> None:
    """A timed-out run leaks its thread; it must not be one the pipeline needs."""
    from backend.core.media_job_stages import _diarization_executor

    executor = _diarization_executor()

    assert executor is _diarization_executor()
    assert executor._max_workers == 1


def test_by_path_task_resolves_its_source_where_it_sits(monkeypatch, tmp_path) -> None:
    """A by-path task never copies its file in, and must not be told it expired.

    Reported 2026-09-03: re-running de-breath on a recording under ~/Movies
    answered "源文件已不在本机" while the file was sitting there, which also
    dead-ended the note (the note needs cut media).
    """
    from backend.core import debreath_job
    from backend.core.storage_paths import in_place_source_path

    recording = tmp_path / "skardi cloud v1 测试联调.mov"
    recording.write_bytes(b"video")
    job = {"metadata": {"folder_intake": {"original_path": str(recording)}}}

    assert in_place_source_path(job) == recording

    monkeypatch.setattr(debreath_job, "find_source_file", lambda _task_id: None)
    monkeypatch.setattr(debreath_job, "get_job", lambda _task_id: job)

    assert debreath_job.resolve_source("task-1") == recording


def test_a_genuinely_missing_in_place_source_still_refuses(monkeypatch, tmp_path) -> None:
    from backend.core import debreath_job

    job = {"metadata": {"folder_intake": {"original_path": str(tmp_path / "gone.mov")}}}
    monkeypatch.setattr(debreath_job, "find_source_file", lambda _task_id: None)
    monkeypatch.setattr(debreath_job, "get_job", lambda _task_id: job)

    with pytest.raises(debreath_job.DebreathError):
        debreath_job.resolve_source("task-1")
