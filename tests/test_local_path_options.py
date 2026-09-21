"""The by-path routes drop any option missing from local_path_options, without
an error: the run then reports success with the feature never having run. A
2026-09-02 meeting recording came back with requested=false for exactly this.
"""

from __future__ import annotations

from backend.routers.local_processing import local_path_options


def test_speaker_and_voice_switches_reach_the_pipeline() -> None:
    options, _ = local_path_options({
        "speaker_diarization": "1",
        "voice_enhance": "true",
    })

    # Normalized to "true": the switch is read back with _truthy either way, and
    # one spelling in the persisted options is easier to read in a task record.
    assert options["speaker_diarization"] == "true"
    assert options["voice_enhance"] == "true"


def test_existing_options_still_pass_through() -> None:
    options, limit = local_path_options({
        "skip_summary": "true",
        "note_mode": "chapter_coverage",
        "stt_model": "large-v3",
        "stt_speed": "accurate",
        "prompt_preset": "meeting",
        "prompt_preset_label": "会议",
    })

    assert options["skip_summary"] == "true"
    assert options["note_mode"] == "chapter_coverage"
    assert options["stt_model"] == "large-v3"
    assert options["stt_speed"] == "accurate"
    assert options["prompt_preset"] == "meeting"
    assert options["prompt_preset_label"] == "会议"
    assert limit is None or limit > 0


def test_voice_enhance_stays_off_unless_asked() -> None:
    """Unlike speaker separation, enhancement is not a default-on switch."""
    options, _ = local_path_options({})

    assert "voice_enhance" not in options


def test_speaker_separation_is_on_when_the_caller_says_nothing() -> None:
    """The settings-page default never reaches a caller that has no page.

    Reported 2026-09-03: an 8-person meeting submitted through the Agent API
    came back with no speakers, because "default on" lived only in the frontend.
    """
    options, _ = local_path_options({})

    assert options["speaker_diarization"] == "true"


def test_an_explicit_opt_out_still_wins() -> None:
    for value in ("0", "false", False):
        options, _ = local_path_options({"speaker_diarization": value})
        assert "speaker_diarization" not in options, value
