"""Requirements for speaker separation with sherpa-onnx (decision log 4.1).

1. sherpa-onnx installed and both model files in the model dir means speaker
   separation is available, with no Hugging Face token involved.
2. diarize_audio decodes any audio/video file to 16 kHz mono float32 itself and
   returns SpeakerTurn rows.
3. A speaker with under 8 seconds of speech is merged into the established
   speaker whose voice is most similar, never by position; when every speaker
   is small nothing is merged.
4. Model dirs: sherpa in <data>/models/speaker, pyannote stays in
   <data>/models/pyannote.
5. FLUENTFLOW_SPEAKER_BACKEND=pyannote runs pyannote when it can, and falls
   back to sherpa with a stated reason when it cannot; the task never fails.
6. Old config files that still hold pyannote_auth_token keep loading.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from backend.core import speaker_diarization as sd
from backend.core import speaker_diarization_pyannote as pyannote_backend
from backend.core.speaker_turns import SpeakerTurn


def _stage_models(model_dir: Path) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "segmentation.onnx").write_bytes(b"onnx")
    (model_dir / "embedding.onnx").write_bytes(b"onnx")


@pytest.fixture()
def model_dir(monkeypatch, tmp_path):
    target = tmp_path / "speaker"
    monkeypatch.setenv("FLUENTFLOW_DIARIZATION_MODEL_DIR", str(target))
    monkeypatch.delenv("FLUENTFLOW_SPEAKER_BACKEND", raising=False)
    return target


# --- 1. availability -------------------------------------------------------

def test_available_with_package_and_both_models_and_no_token(monkeypatch, model_dir):
    _stage_models(model_dir)
    monkeypatch.setattr(sd, "_sherpa_installed", lambda: True)
    monkeypatch.delenv("PYANNOTE_AUTH_TOKEN", raising=False)

    status = sd.diarization_status()

    assert status["available"] is True
    assert status["backend"] == "sherpa-onnx"
    assert status["models_local"] is True
    assert "auth_configured" not in status


def test_unavailable_when_a_model_file_is_missing(monkeypatch, model_dir):
    _stage_models(model_dir)
    (model_dir / "embedding.onnx").unlink()
    monkeypatch.setattr(sd, "_sherpa_installed", lambda: True)

    status = sd.diarization_status()

    assert status["available"] is False
    assert status["dependency_installed"] is True
    assert status["models_local"] is False


def test_unavailable_without_the_package(monkeypatch, model_dir):
    _stage_models(model_dir)
    monkeypatch.setattr(sd, "_sherpa_installed", lambda: False)

    status = sd.diarization_status()

    assert status["available"] is False
    assert status["dependency_installed"] is False


# --- 4. model dirs -----------------------------------------------------------

def test_default_model_dirs_live_under_the_data_dir(monkeypatch, tmp_path):
    from backend.core.runtime_paths import default_diarization_model_dir, default_pyannote_model_dir

    monkeypatch.setenv("FLUENTFLOW_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("FLUENTFLOW_DIARIZATION_MODEL_DIR", raising=False)
    monkeypatch.delenv("FLUENTFLOW_PYANNOTE_MODEL_DIR", raising=False)

    assert default_diarization_model_dir() == tmp_path / "models" / "speaker"
    assert default_pyannote_model_dir() == tmp_path / "models" / "pyannote"

    monkeypatch.setenv("FLUENTFLOW_DIARIZATION_MODEL_DIR", str(tmp_path / "elsewhere"))
    assert default_diarization_model_dir() == tmp_path / "elsewhere"


# --- 2. decoding and the turn shape -----------------------------------------

@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_decode_audio_gives_16k_mono_float32_from_a_stereo_video(tmp_path):
    source = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=1",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
            "-ac", "2", "-shortest", str(source),
        ],
        check=True,
    )

    samples = sd.decode_audio(source)

    assert samples.dtype == np.float32
    assert samples.ndim == 1
    assert abs(len(samples) - 16000) < 1600
    assert float(np.abs(samples).max()) > 0.1


def test_decode_audio_reports_an_unreadable_file(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not media")
    with pytest.raises(RuntimeError):
        sd.decode_audio(broken)


def _fake_sherpa(segments, voice_of):
    """A stand-in sherpa_onnx: diarizer output is fixed; an embedding is the
    voice vector of the speaker whose sample value fills the chunk."""

    class _Config:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def validate(self):
            return True

    class _Result(list):
        def sort_by_start_time(self):
            return _Result(sorted(self, key=lambda r: r.start))

    class _Diarizer:
        def __init__(self, config):
            self.config = config

        def process(self, samples):
            assert samples.dtype == np.float32
            return _Result(types.SimpleNamespace(start=s, end=e, speaker=k) for s, e, k in segments)

    class _Stream:
        def __init__(self):
            self.chunk = None

        def accept_waveform(self, rate, chunk):
            assert rate == 16000
            self.chunk = chunk

        def input_finished(self):
            pass

    class _Extractor:
        def __init__(self, config):
            pass

        def create_stream(self):
            return _Stream()

        def is_ready(self, stream):
            return stream.chunk is not None

        def compute(self, stream):
            return list(voice_of[round(float(stream.chunk.mean()))])

    module = types.ModuleType("sherpa_onnx")
    for name in (
        "OfflineSpeakerDiarizationConfig", "OfflineSpeakerSegmentationModelConfig",
        "OfflineSpeakerSegmentationPyannoteModelConfig", "SpeakerEmbeddingExtractorConfig",
        "FastClusteringConfig",
    ):
        setattr(module, name, _Config)
    module.OfflineSpeakerDiarization = _Diarizer
    module.SpeakerEmbeddingExtractor = _Extractor
    return module


def test_diarize_audio_returns_turns_and_folds_a_stray_speaker_by_voice(monkeypatch, model_dir):
    # Speaker 0 talks 0-20 s, speaker 1 talks 20-40 s, and a stray 3-second
    # cluster 2 sits right after speaker 1 but sounds like speaker 0. Merging by
    # position would give it to speaker 1; by voice it belongs to speaker 0.
    _stage_models(model_dir)
    segments = [(0.0, 20.0, 0), (20.0, 40.0, 1), (40.0, 43.0, 2)]
    samples = np.concatenate([
        np.full(20 * 16000, 0.0, dtype=np.float32),
        np.full(20 * 16000, 1.0, dtype=np.float32),
        np.full(3 * 16000, 2.0, dtype=np.float32),
    ])
    voice_of = {0: [1.0, 0.0, 0.0], 1: [0.0, 1.0, 0.0], 2: [0.9, 0.1, 0.0]}
    monkeypatch.setitem(sys.modules, "sherpa_onnx", _fake_sherpa(segments, voice_of))
    monkeypatch.setattr(sd, "_sherpa_installed", lambda: True)
    monkeypatch.setattr(sd, "decode_audio", lambda path: samples)

    turns = sd.diarize_audio("meeting.mp4")

    assert all(isinstance(turn, SpeakerTurn) for turn in turns)
    assert [(t.start, t.end) for t in turns] == [(0.0, 20.0), (20.0, 40.0), (40.0, 43.0)]
    assert [t.speaker for t in turns] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"]


def test_diarize_audio_refuses_without_models(monkeypatch, model_dir):
    monkeypatch.setattr(sd, "_sherpa_installed", lambda: True)
    with pytest.raises(RuntimeError, match="fetch_diarization_models"):
        sd.diarize_audio("meeting.mp4")


# --- 3. merging small speakers ------------------------------------------------

def _turns(*rows):
    return [SpeakerTurn(start=s, end=e, speaker=k) for s, e, k in rows]


def test_a_small_speaker_joins_the_most_similar_voice_not_the_neighbour():
    turns = _turns((0, 30, "A"), (30, 32, "S"), (32, 60, "B"))
    embeddings = {"A": np.array([0.0, 1.0]), "B": np.array([1.0, 0.0]), "S": np.array([0.95, 0.1])}

    merged = sd.merge_small_speakers(turns, embeddings)

    assert [t.speaker for t in merged] == ["A", "B", "B"]


def test_a_person_who_only_interjected_stays_their_own_speaker():
    """Requirement (2026-10-15, confirmed by listening): someone who said one
    line in a meeting is a speaker of their own, not folded into whoever their
    voice happens to be nearest. Measured: real interjections scored 0.21 and
    0.41 against their nearest speaker; a stray piece of a speaker 0.57 and 0.76."""
    turns = _turns((0, 30, "A"), (30, 32, "S"), (32, 60, "B"))
    # cosine(S, B) is about 0.41
    embeddings = {"A": np.array([0.0, 1.0, 0.0]), "B": np.array([1.0, 0.0, 0.0]), "S": np.array([0.41, 0.0, 0.91])}

    assert sd.merge_small_speakers(turns, embeddings) == turns


def test_speakers_over_the_threshold_are_left_alone():
    turns = _turns((0, 30, "A"), (30, 39, "C"), (39, 60, "B"))
    embeddings = {"A": np.array([1.0, 0.0]), "B": np.array([0.0, 1.0]), "C": np.array([1.0, 0.0])}

    assert sd.merge_small_speakers(turns, embeddings) == turns


def test_small_speaker_time_is_summed_over_its_turns():
    # Three 3-second turns make 9 seconds: not small.
    turns = _turns((0, 30, "A"), (30, 33, "C"), (33, 36, "C"), (36, 39, "C"))
    embeddings = {"A": np.array([1.0, 0.0]), "C": np.array([1.0, 0.0])}

    assert [t.speaker for t in sd.merge_small_speakers(turns, embeddings)] == ["A", "C", "C", "C"]


def test_nothing_is_merged_when_every_speaker_is_small():
    turns = _turns((0, 3, "A"), (3, 6, "B"), (6, 8, "C"))
    embeddings = {"A": np.array([1.0, 0.0]), "B": np.array([0.9, 0.1]), "C": np.array([0.0, 1.0])}

    assert sd.merge_small_speakers(turns, embeddings) == turns


def test_a_small_speaker_without_an_embedding_stays():
    turns = _turns((0, 30, "A"), (30, 31, "S"))

    assert sd.merge_small_speakers(turns, {"A": np.array([1.0, 0.0])}) == turns


# --- 5. backend choice --------------------------------------------------------

def test_pyannote_chosen_but_not_installed_falls_back_with_a_reason(monkeypatch, model_dir):
    _stage_models(model_dir)
    monkeypatch.setenv("FLUENTFLOW_SPEAKER_BACKEND", "pyannote")
    monkeypatch.setattr(sd, "_sherpa_installed", lambda: True)
    monkeypatch.setattr(
        pyannote_backend, "pyannote_status",
        lambda: {"available": False, "backend": "pyannote", "dependency_installed": False,
                 "auth_configured": False, "models_local": False},
    )

    status = sd.diarization_status()

    assert status["available"] is True
    assert status["backend"] == "sherpa-onnx"
    assert status["requested_backend"] == "pyannote"
    assert "pyannote" in status["fallback_reason"]


def test_pyannote_chosen_and_installed_runs_pyannote(monkeypatch, model_dir):
    monkeypatch.setenv("FLUENTFLOW_SPEAKER_BACKEND", "pyannote")
    monkeypatch.setattr(
        pyannote_backend, "pyannote_status",
        lambda: {"available": True, "backend": "pyannote", "dependency_installed": True,
                 "auth_configured": False, "models_local": True},
    )
    ran = []
    monkeypatch.setattr(pyannote_backend, "diarize_with_pyannote", lambda path: ran.append(path) or [])
    monkeypatch.setattr(sd, "diarize_with_sherpa", lambda path: pytest.fail("sherpa must not run"))

    status = sd.diarization_status()
    sd.diarize_audio("meeting.mp4")

    assert status["backend"] == "pyannote"
    assert status["fallback_reason"] is None
    assert ran == ["meeting.mp4"]


def test_the_default_backend_is_sherpa(monkeypatch, model_dir):
    assert sd.diarization_status()["requested_backend"] == "sherpa"
    monkeypatch.setenv("FLUENTFLOW_SPEAKER_BACKEND", "something-else")
    assert sd.diarization_status()["requested_backend"] == "sherpa"


def test_a_fallback_is_recorded_in_the_result_and_the_task_goes_on(monkeypatch):
    from backend.core import media_job_stages as stages

    monkeypatch.setattr(stages, "log_event", lambda **kw: None)
    monkeypatch.setattr(
        stages, "diarization_status",
        lambda: {"available": True, "backend": "sherpa-onnx", "fallback_reason": "没装 pyannote，改用 sherpa-onnx。"},
    )
    async def child(_path, *, budget_seconds):
        return [SpeakerTurn(0.0, 1.0, "SPEAKER_00")]

    monkeypatch.setattr(stages, "diarize_in_child", child)

    class _Loop:
        async def run_in_executor(self, _executor, func):
            return func()

    ctx = types.SimpleNamespace(
        task_id_value="t", source_type="upload", source_filename="m.mp4", source_file_size_mb=1.0,
        diarization_requested=True, loop=_Loop(),
    )
    segments, payload = asyncio.run(stages.label_speakers(
        ctx, segments_payload=[{"start": 0.0, "end": 1.0, "text": "hi"}], duration_sec=1.0, audio_path="m.wav",
    ))

    assert payload["applied"] is True
    assert payload["backend"] == "sherpa-onnx"
    assert payload["fallback_reason"] == "没装 pyannote，改用 sherpa-onnx。"
    assert segments[0]["speaker"] == "SPEAKER_00"


# --- 6. old config files -------------------------------------------------------

def test_an_old_config_with_a_pyannote_token_still_loads(tmp_path):
    from backend.core.local_config import credential_status, get_sensitive_setting, save_sensitive_settings

    path = tmp_path / "fluentflow_config.json"
    path.write_text(json.dumps({"secrets": {"pyannote_auth_token": "hf_old", "deepseek_api_key": "sk-1"}}), encoding="utf-8")

    assert credential_status(path)["deepseek_api_key_configured"] is True
    save_sensitive_settings({"openai_api_key": "sk-2"}, path)

    assert get_sensitive_setting("deepseek_api_key", path) == "sk-1"
    assert get_sensitive_setting("openai_api_key", path) == "sk-2"
    assert json.loads(path.read_text(encoding="utf-8"))["secrets"]["pyannote_auth_token"] == "hf_old"
