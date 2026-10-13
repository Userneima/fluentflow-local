"""Limits that come from one machine's capacity are chosen for the machine.

Requirement (owner, 2026-10-13): a limit tuned on one 16GB Mac is one path
among several, picked for the machine it runs on and the user's priority, not a
rule of the product. Each choice can still be set directly, and that wins.
"""

from __future__ import annotations

import pytest

from backend.core import device_profile as dp


def _machine(memory_gb: float, cpu_cores: int, hardware: bool = True) -> dp.DeviceProfile:
    return dp.DeviceProfile(
        cpu_cores=cpu_cores, performance_cores=max(1, cpu_cores // 2), memory_gb=memory_gb,
        apple_silicon=True, hardware_video_encoder=hardware,
    )


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "FLUENTFLOW_PROCESSING_PRESET", "FLUENTFLOW_RENDER_WORKERS", "FLUENTFLOW_QUEUE_CONCURRENCY",
        "FLUENTFLOW_DEBREATH_HARDWARE_ENCODE", "FLUENTFLOW_DEBREATH_HARDWARE_BITRATE_FACTOR",
        "FLUENTFLOW_DEBREATH_CAP_VIDEO_BITRATE", "FLUENTFLOW_DEBREATH_AUDIO_FIRST",
        "FLUENTFLOW_CPU_USAGE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_the_machine_this_was_tuned_on_keeps_three_encoders():
    assert dp.render_workers(_machine(16, 10)) == 3


def test_a_larger_machine_runs_more_and_a_smaller_one_fewer():
    assert dp.render_workers(_machine(64, 18)) == 6
    assert dp.render_workers(_machine(8, 8)) == 1


def test_a_setting_of_its_own_wins_over_the_machine(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_RENDER_WORKERS", "2")
    assert dp.render_workers(_machine(64, 12)) == 2


def test_quality_is_the_default_and_changes_nothing(monkeypatch):
    monkeypatch.setattr(dp, "current", lambda: _machine(16, 8))
    assert dp.preset() == dp.PRESET_QUALITY
    assert not dp.hardware_encode() and not dp.cap_video_bitrate() and not dp.audio_first()
    assert dp.hardware_bitrate_factor() == 1.0


def test_speed_uses_the_hardware_encoder_only_where_there_is_one(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_PROCESSING_PRESET", "speed")
    monkeypatch.setattr(dp, "current", lambda: _machine(16, 8, hardware=True))
    assert dp.hardware_encode() and dp.audio_first() and dp.hardware_bitrate_factor() == 2.0

    monkeypatch.setattr(dp, "current", lambda: _machine(16, 8, hardware=False))
    assert not dp.hardware_encode() and not dp.audio_first()


def test_size_caps_the_video_at_the_source_bitrate(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_PROCESSING_PRESET", "size")
    assert dp.cap_video_bitrate()


def test_an_explicit_switch_wins_over_the_preset(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_PROCESSING_PRESET", "speed")
    monkeypatch.setattr(dp, "current", lambda: _machine(16, 8))
    monkeypatch.setenv("FLUENTFLOW_DEBREATH_HARDWARE_ENCODE", "0")
    assert not dp.hardware_encode()


def test_one_task_at_a_time_unless_set(monkeypatch):
    assert dp.queue_concurrency() == 1
    monkeypatch.setenv("FLUENTFLOW_QUEUE_CONCURRENCY", "2")
    assert dp.queue_concurrency() == 2


def test_this_machine_is_read_without_error():
    profile = dp.current()
    assert profile.cpu_cores >= 1 and profile.performance_cores >= 1


# ── CPU share (owner, 2026-10-13) ───────────────────────────────────────────
# Requirements:
# 1. By default a cut leaves the machine room: it is held below all cores and
#    runs below normal priority.
# 2. The user can choose light (about half), balanced (about three quarters)
#    or full; the choice saved on the settings page is what the cut uses, and
#    FLUENTFLOW_CPU_USAGE wins over it.
# 3. Full use behaves as before: commands run unchanged, at normal priority.
# 4. Only ffmpeg is limited; every decoder and encoder in a command gets a share.
# 5. (2026-10-14) A lone ffmpeg (the frame-rate pass) gets the whole budget;
#    only encodes running side by side split it.
# 6. (2026-10-14) Priority is lowered without ``preexec_fn``, which Python
#    documents as able to deadlock when threads start processes.

FFMPEG_CUT = ["/opt/homebrew/bin/ffmpeg", "-y", "-i", "in.mov", "-c:v", "libx264", "-crf", "18", "out.mp4"]


@pytest.fixture
def no_saved_choice(monkeypatch, tmp_path):
    from backend.core import local_config

    target = tmp_path / "config.json"
    monkeypatch.setattr(local_config, "config_path", lambda: target)
    return target


def test_default_leaves_room_and_lowers_priority(no_saved_choice):
    machine = _machine(16, 10)
    assert dp.cpu_usage() == "balanced"
    budget = dp.cpu_thread_budget(machine)
    assert budget is not None and budget < machine.cpu_cores
    assert dp.process_niceness() > 0


def test_light_is_about_half_and_below_balanced(monkeypatch, no_saved_choice):
    machine = _machine(16, 10)
    monkeypatch.setenv("FLUENTFLOW_CPU_USAGE", "balanced")
    balanced = dp.cpu_thread_budget(machine)
    monkeypatch.setenv("FLUENTFLOW_CPU_USAGE", "light")
    assert dp.cpu_thread_budget(machine) == 5
    assert dp.cpu_thread_budget(machine) < balanced


def test_saved_choice_is_used_and_env_wins(monkeypatch, no_saved_choice):
    from backend.core import local_config

    local_config.save_preferences({"cpu_usage": "full"})
    assert dp.cpu_usage() == "full"
    monkeypatch.setenv("FLUENTFLOW_CPU_USAGE", "light")
    assert dp.cpu_usage() == "light"


def test_unknown_choice_is_refused(no_saved_choice):
    from backend.core import local_config

    with pytest.raises(ValueError):
        local_config.save_preferences({"cpu_usage": "turbo"})


def test_full_use_runs_commands_unchanged(monkeypatch, no_saved_choice):
    monkeypatch.setenv("FLUENTFLOW_CPU_USAGE", "full")
    assert dp.limit_ffmpeg_threads(FFMPEG_CUT, _machine(16, 10)) == FFMPEG_CUT
    calls = []
    monkeypatch.setattr(dp.os, "setpriority", lambda *args: calls.append(args))
    dp.lower_priority(12345)
    assert calls == []


def test_every_decoder_and_encoder_gets_a_share(monkeypatch, no_saved_choice):
    monkeypatch.setenv("FLUENTFLOW_CPU_USAGE", "balanced")
    machine = _machine(16, 10)
    command = [*FFMPEG_CUT[:4], "-i", "in2.wav", *FFMPEG_CUT[4:]]
    with dp.parallel_encodes(3):
        limited = dp.limit_ffmpeg_threads(command, machine)
    share = str(dp.cpu_thread_budget(machine) // 3)
    for position, part in enumerate(limited):
        if part == "-i":
            assert limited[position - 2:position] == ["-threads", share]
    encoder = limited.index("libx264")
    assert limited[encoder + 1:encoder + 3] == ["-threads", share]
    assert limited[-1] == "out.mp4"


def test_only_ffmpeg_is_limited(monkeypatch, no_saved_choice):
    monkeypatch.setenv("FLUENTFLOW_CPU_USAGE", "light")
    probe = ["/opt/homebrew/bin/ffprobe", "-i", "in.mov"]
    assert dp.limit_ffmpeg_threads(probe, _machine(16, 10)) == probe


def test_encoders_never_outnumber_the_budget(monkeypatch, no_saved_choice):
    monkeypatch.setenv("FLUENTFLOW_CPU_USAGE", "light")
    assert dp.render_workers(_machine(64, 2)) == 1


def test_a_lone_ffmpeg_gets_the_whole_budget(monkeypatch, no_saved_choice):
    monkeypatch.setenv("FLUENTFLOW_CPU_USAGE", "balanced")
    machine = _machine(16, 10)
    budget = str(dp.cpu_thread_budget(machine))
    lone = dp.limit_ffmpeg_threads(FFMPEG_CUT, machine)
    assert lone[lone.index("libx264") + 2] == budget
    with dp.parallel_encodes(3):
        side_by_side = dp.limit_ffmpeg_threads(FFMPEG_CUT, machine)
    assert int(side_by_side[side_by_side.index("libx264") + 2]) * 3 <= int(budget)
    after = dp.limit_ffmpeg_threads(FFMPEG_CUT, machine)
    assert after[after.index("libx264") + 2] == budget


def test_priority_is_lowered_from_the_parent(monkeypatch, no_saved_choice):
    monkeypatch.setenv("FLUENTFLOW_CPU_USAGE", "balanced")
    monkeypatch.setattr(dp.os, "name", "posix")
    calls = []
    monkeypatch.setattr(dp.os, "setpriority", lambda *args: calls.append(args))
    dp.lower_priority(12345)
    assert calls == [(dp.os.PRIO_PROCESS, 12345, dp.process_niceness())]


def test_no_launcher_uses_preexec_fn():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "backend" / "core"
    for name in ("silence_cuts.py", "task_watch.py", "device_profile.py"):
        source = (root / name).read_text(encoding="utf-8")
        assert "preexec_fn=" not in source and "\"preexec_fn\"" not in source, name


def test_a_real_command_runs_lowered(monkeypatch, no_saved_choice):
    from backend.core import silence_cuts

    monkeypatch.setenv("FLUENTFLOW_CPU_USAGE", "light")
    seen = []
    monkeypatch.setattr(dp, "lower_priority", lambda pid: seen.append(pid))
    done = silence_cuts.run_command(["/bin/echo", "ok"])
    assert done.returncode == 0 and done.stdout.strip() == "ok"
    assert len(seen) == 1
