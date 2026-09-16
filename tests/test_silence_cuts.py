"""Mechanical breath-gap removal.

Every guard tested here exists because output was already broken once. The
recurring shape of those failures: a cut range that reaches past the end of the
video stream, a filter expression too large for ffmpeg, or a check with an
absolute threshold that condemned a faithful render. So the assertions are less
about "does it cut" and more about "does it still refuse the things that broke".
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backend.core import silence_cuts as sc
from backend.core.silence_cuts import (
    SilenceCutError,
    TimeRange,
    build_cut_plan,
    parse_silencedetect,
)


def _silences(*pairs: tuple[float, float]) -> list[TimeRange]:
    return [TimeRange(start, end) for start, end in pairs]


# ── reading ffmpeg's output ─────────────────────────────────────────────────

def test_silence_lines_are_paired_into_ranges():
    stderr = (
        "[silencedetect @ 0x1] silence_start: 1.5\n"
        "[silencedetect @ 0x1] silence_end: 2.75 | silence_duration: 1.25\n"
        "[silencedetect @ 0x1] silence_start: 10.0\n"
        "[silencedetect @ 0x1] silence_end: 11.5 | silence_duration: 1.5\n"
    )
    assert parse_silencedetect(stderr) == [TimeRange(1.5, 2.75), TimeRange(10.0, 11.5)]


def test_a_silence_that_never_ends_is_dropped_not_guessed():
    """ffmpeg omits the final silence_end when a file ends mid-silence.

    Inventing an end would place a cut past the last frame, which is the whole
    family of bugs this module is built around.
    """
    stderr = "silence_start: 1.0\nsilence_end: 2.0\nsilence_start: 9.0\n"
    assert parse_silencedetect(stderr) == [TimeRange(1.0, 2.0)]


def test_unreadable_numbers_are_skipped_without_raising():
    stderr = "silence_start: nope\nsilence_start: 1.0\nsilence_end: 2.0\n"
    assert parse_silencedetect(stderr) == [TimeRange(1.0, 2.0)]


def test_a_window_offset_is_added_back_to_both_ends():
    assert parse_silencedetect("silence_start: 1.0\nsilence_end: 2.0\n", offset=60.0) == [
        TimeRange(61.0, 62.0)
    ]


# ── planning the cut ────────────────────────────────────────────────────────

def test_padding_leaves_a_sliver_of_each_gap_at_both_ends():
    plan = build_cut_plan(30.0, _silences((10.0, 12.0)), padding_seconds=0.1)
    assert plan.cuts == [TimeRange(10.1, 11.9)]
    assert plan.keeps == [TimeRange(0.0, 10.1), TimeRange(11.9, 30.0)]
    assert plan.kept_seconds == 28.2


def test_a_gap_too_short_to_survive_padding_is_not_a_cut():
    plan = build_cut_plan(30.0, _silences((10.0, 10.15)), padding_seconds=0.1)
    assert plan.cuts == []
    assert plan.keeps == [TimeRange(0.0, 30.0)], "the whole file is kept"


def test_overlapping_cuts_merge_instead_of_producing_a_backwards_keep():
    plan = build_cut_plan(30.0, _silences((5.0, 9.0), (8.0, 12.0)), padding_seconds=0.1)
    assert plan.cuts == [TimeRange(5.1, 11.9)]
    assert plan.keeps == [TimeRange(0.0, 5.1), TimeRange(11.9, 30.0)]


def test_silence_reaching_past_the_video_never_produces_a_range_beyond_it():
    """The 3.128x bug, in plan form.

    Silence is detected on audio, and these recordings carry audio that outlasts
    their video by up to 1.1s, so the last silence can end after the final frame.
    A keep range starting 0.036s past the video end rendered as an audio-only
    part, and concatenating a part with a missing stream inflated the whole
    output's duration.
    """
    plan = build_cut_plan(30.0, _silences((28.0, 31.1)), padding_seconds=0.1)

    assert all(keep.end <= 30.0 for keep in plan.keeps)
    assert all(keep.start < 30.0 for keep in plan.keeps)
    assert all(cut.end <= 30.0 for cut in plan.cuts)


def test_a_keep_fragment_of_a_few_frames_is_dropped():
    """Two gaps 0.05s apart leave a sliver with no useful picture in between."""
    plan = build_cut_plan(30.0, _silences((10.0, 12.0), (12.05, 14.0)), padding_seconds=0.0)
    assert TimeRange(12.0, 12.05) not in plan.keeps
    assert plan.keeps == [TimeRange(0.0, 10.0), TimeRange(14.0, 30.0)]


def test_the_plan_reports_what_it_would_remove():
    plan = build_cut_plan(100.0, _silences((10.0, 20.0), (50.0, 55.0)), padding_seconds=0.0)
    assert plan.removed_seconds == 15.0
    assert plan.kept_seconds == 85.0
    assert plan.silence_ratio_percent == 15.0
    assert plan.silences_found == 2


# ── warnings report, they do not refuse ─────────────────────────────────────

def test_an_implausibly_quiet_result_warns_and_still_returns_the_plan():
    plan = build_cut_plan(100.0, _silences((10.0, 80.0)), padding_seconds=0.0)
    assert plan.cuts, "the plan is still returned"
    assert any("高于" in warning for warning in plan.warnings)


def test_finding_almost_no_silence_warns_about_the_threshold():
    plan = build_cut_plan(1000.0, _silences((10.0, 12.0)), padding_seconds=0.0)
    assert plan.cuts == [TimeRange(10.0, 12.0)]
    assert any("低于" in warning for warning in plan.warnings)


def test_finding_nothing_says_so_instead_of_looking_successful():
    plan = build_cut_plan(100.0, [], padding_seconds=0.1)
    assert plan.cuts == []
    assert any("没有找到可剪的气口" in warning for warning in plan.warnings)


# ── is the threshold separating speech from silence at all ──────────────────

class _FakeLevels:
    """Answers volumedetect with a level chosen per requested start time."""

    def __init__(self, levels: dict[float, float], duration: float = 100.0) -> None:
        self.levels = levels
        self.duration = duration
        self.measured: list[float] = []

    def __call__(self, command):
        command = [str(part) for part in command]
        if "ffprobe" in command[0]:
            return subprocess.CompletedProcess(command, 0, f"{self.duration}\n", "")
        start = float(command[command.index("-ss") + 1])
        self.measured.append(start)
        level = self.levels.get(round(start, 3), -60.0)
        return subprocess.CompletedProcess(command, 0, "", f"mean_volume: {level} dB\n")


def test_a_cut_barely_quieter_than_the_audio_beside_it_is_reported(tmp_path, fake_tools):
    """The gap the silent-ratio band cannot see.

    A phone recording of a classroom marked 48% of its runtime silent — inside
    the plausible band — while in its faint stretches the removed pieces measured
    within 2-5dB of the audio right next to them.
    """
    media = tmp_path / "faint.m4a"
    media.write_bytes(b"x")
    plan = build_cut_plan(100.0, _silences((10.0, 12.0), (40.0, 42.0)), padding_seconds=0.0)
    runner = _FakeLevels({10.0: -39.0, 40.0: -40.0, 0.0: -37.0, 12.0: -37.5, 42.0: -38.0})

    separation = sc.measure_level_separation(media, plan, runner=runner)

    assert separation["measured"] is True
    assert separation["separated"] is False
    assert separation["narrow_cut_count"] == 2
    assert separation["narrowest_db"] < 5.0


def test_a_clean_recording_measures_as_separated(tmp_path, fake_tools):
    media = tmp_path / "clean.m4a"
    media.write_bytes(b"x")
    plan = build_cut_plan(100.0, _silences((10.0, 12.0), (40.0, 42.0)), padding_seconds=0.0)
    runner = _FakeLevels({10.0: -58.0, 40.0: -60.0, 0.0: -26.0, 12.0: -27.0, 42.0: -25.0})

    separation = sc.measure_level_separation(media, plan, runner=runner)

    assert separation["separated"] is True
    assert separation["narrow_cut_count"] == 0
    assert separation["separation_db"] > 25.0


def test_one_bad_stretch_is_not_averaged_away_by_a_clean_file(tmp_path, fake_tools):
    """Why this is measured per cut and not as one median over the file.

    The first version of this check compared all cuts against all keeps and
    reported a comfortable 20dB margin on the very recording whose quiet stretches
    were the problem. Most of that file really was clean; the median said so, and
    said nothing useful.
    """
    media = tmp_path / "mostly-clean.m4a"
    media.write_bytes(b"x")
    silences = tuple((float(i * 100 + 10), float(i * 100 + 20)) for i in range(6))
    plan = build_cut_plan(600.0, _silences(*silences), padding_seconds=0.0)
    levels = {span.start: -60.0 for span in plan.cuts}
    levels.update({span.start: -30.0 for span in plan.keeps})
    levels[plan.cuts[3].start] = -32.0  # one faint stretch among five clean ones

    separation = sc.measure_level_separation(media, plan, samples=6, runner=_FakeLevels(levels, duration=600.0))

    assert separation["separation_db"] >= 25.0, "the file as a whole still looks clean"
    assert separation["separated"] is False, "and the one bad stretch is still reported"
    assert separation["narrow_cut_count"] == 1


def test_the_sample_is_spread_across_the_file_not_taken_from_the_front(tmp_path, fake_tools):
    """Silence at the head of a recording is not representative: a room goes
    quiet before anyone speaks."""
    media = tmp_path / "long.m4a"
    media.write_bytes(b"x")
    silences = tuple((float(i * 100), float(i * 100 + 2)) for i in range(50))
    plan = build_cut_plan(5000.0, _silences(*silences), padding_seconds=0.0)
    runner = _FakeLevels({}, duration=5000.0)

    sc.measure_level_separation(media, plan, samples=5, runner=runner)

    cut_starts = [start for start in runner.measured if start % 100 == 0]
    assert max(cut_starts) > 2000.0, "the sample must reach the far end of the file"


def test_an_unmeasurable_file_says_so_instead_of_claiming_separation(tmp_path, fake_tools):
    media = tmp_path / "odd.m4a"
    media.write_bytes(b"x")
    plan = build_cut_plan(100.0, _silences((10.0, 12.0)), padding_seconds=0.0)

    def silent_runner(command):
        return subprocess.CompletedProcess([str(part) for part in command], 0, "", "")

    assert sc.measure_level_separation(media, plan, runner=silent_runner) == {"measured": False}


def test_the_separation_check_can_be_skipped(tmp_path, fake_tools):
    """It costs a couple of dozen short ffmpeg probes, so a caller that already
    knows its material can turn it off."""
    media = tmp_path / "clip.m4a"
    media.write_bytes(b"x")
    runner = _FakeSilenceProbe()

    plan = sc.plan_silence_cuts(media, check_separation=False, runner=runner)

    assert plan.cuts and plan.level_separation == {}
    # The separation check measures each sampled cut, so its probes carry -ss. The
    # threshold measurement is one whole-file pass and carries none; asserting on
    # "no volumedetect at all" would now be asserting the threshold was never
    # measured, which is a different thing than what this test is about.
    assert not any("-ss" in command for command in runner.commands)


class _FakeSilenceProbe:
    """A 100s file with one 10s silence in the middle."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, command):
        command = [str(part) for part in command]
        self.commands.append(command)
        if "ffprobe" in command[0]:
            if "stream=codec_type" in " ".join(command):
                return subprocess.CompletedProcess(command, 0, "audio\n", "")
            return subprocess.CompletedProcess(command, 0, "100.0\n", "")
        if "silencedetect" in " ".join(command):
            return subprocess.CompletedProcess(
                command, 0, "", "silence_start: 40.0\nsilence_end: 50.0\n"
            )
        return subprocess.CompletedProcess(command, 0, "", "")


# ── verification measures against the source, not an absolute number ────────

class _FakeProbe:
    """Answers ffprobe queries; records every command it was given."""

    def __init__(self, **values: object) -> None:
        self.values = values
        self.commands: list[list[str]] = []

    def __call__(self, command):
        command = [str(part) for part in command]
        self.commands.append(command)
        joined = " ".join(command)
        for key, value in self.values.items():
            if key in joined:
                return subprocess.CompletedProcess(command, 0, f"{value}\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")


@pytest.fixture()
def fake_tools(monkeypatch):
    """Let the module run without ffmpeg installed."""
    monkeypatch.setattr(sc, "_ffmpeg_path", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(sc, "_ffprobe_path", lambda: "/fake/ffprobe")


def test_a_render_that_inherits_its_sources_skew_is_not_condemned(tmp_path, fake_tools):
    """Two real recordings shipped with 1.09s of audio/video skew of their own.

    Demanding 0.5s of the output demanded better than the input, and threw away
    faithful renders.
    """
    output = tmp_path / "out.mp4"
    output.write_bytes(b"x")
    probe = _FakeProbe(**{
        "format=duration": 480.0,
        "stream=duration": 480.0,
        "nb_frames": 14400,
    })
    report = sc.verify_render(
        output, expected_seconds=480.0, source_skew_seconds=1.09, runner=probe
    )
    assert report.ok and report.failed_checks == []


def test_slow_motion_output_is_caught(tmp_path, fake_tools):
    """The failure worth catching: frames covering a third of the container."""
    output = tmp_path / "out.mp4"
    output.write_bytes(b"x")
    probe = _FakeProbe(**{
        "format=duration": 480.0,
        "stream=duration": 480.0,
        "nb_frames": 4800,  # 160s of frames stretched across 480s
    })
    report = sc.verify_render(output, expected_seconds=480.0, runner=probe)
    assert "frame_count_matches_duration" in report.failed_checks


def test_a_failed_check_never_removes_the_render(tmp_path, fake_tools):
    """A check I had not validated was given authority to delete, and used it on
    three faithful renders. Reporting costs a second look; deleting cost an hour
    of re-encoding."""
    output = tmp_path / "out.mp4"
    output.write_bytes(b"x")
    probe = _FakeProbe(**{"format=duration": 1.0, "stream=duration": 1.0, "nb_frames": 0})
    report = sc.verify_render(output, expected_seconds=480.0, runner=probe)
    assert not report.ok
    assert output.is_file(), "the render survives its own verdict"


def test_missing_frame_metadata_does_not_pass_by_default(tmp_path, fake_tools):
    output = tmp_path / "out.mp4"
    output.write_bytes(b"x")
    probe = _FakeProbe(**{"format=duration": 480.0, "stream=duration": 480.0})
    report = sc.verify_render(output, expected_seconds=480.0, runner=probe)
    assert report.frame_count == 0
    assert "frame_count_matches_duration" in report.failed_checks


# ── rendering keeps the filter graph small ──────────────────────────────────

class _FakeRender:
    """Pretends to render: writes each requested output and answers probes."""

    def __init__(self, *, streams_per_part: str = "2") -> None:
        self.streams_per_part = streams_per_part
        self.filters: list[str] = []
        self.concat_lists: list[str] = []

    def __call__(self, command):
        command = [str(part) for part in command]
        if "ffprobe" in command[0]:
            if "format=nb_streams" in command:
                return subprocess.CompletedProcess(command, 0, f"{self.streams_per_part}\n", "")
            return subprocess.CompletedProcess(command, 0, "0\n", "")
        if "-vf" in command:
            self.filters.append(command[command.index("-vf") + 1])
        if "concat" in command:
            listing = Path(command[command.index("-i") + 1])
            self.concat_lists.append(listing.read_text(encoding="utf-8"))
        Path(command[-1]).write_bytes(b"rendered")
        return subprocess.CompletedProcess(command, 0, "", "")


def test_many_ranges_render_in_batches_so_the_filter_never_grows_unbounded(tmp_path, fake_tools):
    """167 `between()` terms in one select expression made ffmpeg die with
    "Cannot allocate memory"; 17 was fine. Batching is the fix, and it is not a
    cap on how many cuts a plan may hold."""
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    keeps = [TimeRange(float(i * 10), float(i * 10 + 5)) for i in range(60)]
    runner = _FakeRender()

    batches = sc.render_keeps(source, keeps, tmp_path / "out.mp4", ranges_per_batch=25, runner=runner)

    assert batches == 3
    assert len(runner.filters) == 3
    assert max(expression.count("between(") for expression in runner.filters) <= 25
    assert len(runner.concat_lists) == 1
    assert runner.concat_lists[0].count("file '") == 3


def test_a_part_missing_a_stream_stops_the_render_instead_of_being_concatenated(tmp_path, fake_tools):
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    runner = _FakeRender(streams_per_part="1")

    with pytest.raises(SilenceCutError, match="expected 2"):
        sc.render_keeps(source, [TimeRange(0.0, 5.0)], tmp_path / "out.mp4", runner=runner)


def test_render_scratch_files_do_not_survive_the_call(tmp_path, fake_tools):
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    out = tmp_path / "out.mp4"
    sc.render_keeps(source, [TimeRange(0.0, 5.0)], out, runner=_FakeRender())
    assert sorted(child.name for child in tmp_path.iterdir()) == ["in.mp4", "out.mp4"]


def test_rendering_nothing_is_an_error_not_an_empty_file(tmp_path, fake_tools):
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    with pytest.raises(SilenceCutError, match="keeps no ranges"):
        sc.render_keeps(source, [], tmp_path / "out.mp4", runner=_FakeRender())


def test_normalize_refuses_output_that_is_not_constant_frame_rate(tmp_path, fake_tools):
    """Cutting renumbers frames by count, which is only valid at the declared
    rate. If normalization did not take, cutting must not proceed."""
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")

    class _VariableRate(_FakeRender):
        def __call__(self, command):
            command = [str(part) for part in command]
            if "ffprobe" in command[0] and "stream=avg_frame_rate" in command:
                return subprocess.CompletedProcess(command, 0, "30000/1001\n", "")
            return super().__call__(command)

    with pytest.raises(SilenceCutError, match="constant 30fps"):
        sc.normalize_to_constant_frame_rate(source, tmp_path / "norm.mp4", runner=_VariableRate())


# ── audio-only sources take the shorter path ────────────────────────────────

def test_audio_only_rendering_asks_for_no_picture(tmp_path, fake_tools):
    """The video guards protect against failures that cannot happen without a
    video stream, and applying them anyway is what broke audio first: `-vf
    fps=30` on a file with no picture produced nothing, and the constant-frame-
    rate check then rejected its own output."""
    source = tmp_path / "in.m4a"
    source.write_bytes(b"x")

    class _AudioRender(_FakeRender):
        def __init__(self) -> None:
            super().__init__(streams_per_part="1")
            self.commands: list[list[str]] = []

        def __call__(self, command):
            command = [str(part) for part in command]
            if "ffprobe" not in command[0]:
                self.commands.append(command)
            return super().__call__(command)

    runner = _AudioRender()
    sc.render_keeps(
        source, [TimeRange(0.0, 5.0)], tmp_path / "out.m4a", audio_only=True, runner=runner
    )

    render = runner.commands[0]
    assert "-vf" not in render and "-vn" in render
    assert "-af" in render
    assert not runner.filters


def test_audio_only_verification_does_not_invent_a_sync_problem(tmp_path, fake_tools):
    output = tmp_path / "out.m4a"
    output.write_bytes(b"x")
    probe = _FakeProbe(**{"format=duration": 480.0})

    report = sc.verify_render(output, expected_seconds=480.0, audio_only=True, runner=probe)

    assert report.ok
    assert list(report.checks) == ["duration_matches_plan"]
    payload = report.as_dict()
    assert payload["audio_only"] is True
    assert "audio_video_skew_ms" not in payload and "frame_count" not in payload


def test_the_encoder_follows_the_container_being_written(tmp_path):
    """AAC into an .mp3 file fails outright, so this cannot be one fixed setting."""
    assert "libmp3lame" in sc.default_encode_args(tmp_path / "out.mp3", audio_only=True)
    assert "aac" in sc.default_encode_args(tmp_path / "out.m4a", audio_only=True)
    assert "libx264" in sc.default_encode_args(tmp_path / "out.mp4", audio_only=False)


def test_faststart_is_only_asked_of_containers_that_accept_it(tmp_path, fake_tools):
    source = tmp_path / "in.mp3"
    source.write_bytes(b"x")
    runner = _FakeRender(streams_per_part="1")
    concat_commands: list[list[str]] = []

    class _Recording(_FakeRender):
        def __call__(self, command):
            command = [str(part) for part in command]
            if "concat" in command:
                concat_commands.append(command)
            return runner(command)

    sc.render_keeps(source, [TimeRange(0.0, 5.0)], tmp_path / "out.mp3", audio_only=True, runner=_Recording())
    assert "-movflags" not in concat_commands[0]


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg/ffprobe not installed",
)
@pytest.mark.parametrize("suffix", [".m4a", ".mp3"])
def test_a_real_audio_recording_loses_its_silence(tmp_path, suffix):
    source = tmp_path / f"clip{suffix}"
    built = subprocess.run(
        [
            shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i",
            "sine=frequency=440:duration=9:sample_rate=48000,"
            "volume=enable='between(t,3,6)':volume=0",
            "-y", str(source),
        ],
        capture_output=True, text=True, timeout=180,
    )
    assert built.returncode == 0, built.stderr[-400:]

    plan = sc.plan_silence_cuts(source, min_silence_seconds=0.5)
    assert plan.cuts, f"no silence found: {plan.warnings}"

    report = sc.render_cut_plan(source, plan, tmp_path / f"cut{suffix}")

    assert report.audio_only and report.ok, report.as_dict()
    assert abs(report.actual_seconds - plan.kept_seconds) < 0.3, report.as_dict()


# ── the cut list is the deliverable ─────────────────────────────────────────

def test_the_cut_list_carries_everything_needed_to_re_render_it():
    plan = build_cut_plan(
        100.0, _silences((10.0, 20.0)), noise_db=-32.0, min_silence_seconds=0.3, padding_seconds=0.1
    )
    payload = plan.as_dict()

    assert payload["cut_list_version"] == "1"
    assert payload["cuts"] == [{"start": 10.1, "end": 19.9}]
    assert payload["keeps"][0] == {"start": 0.0, "end": 10.1}
    assert payload["detection"] == {
        "method": "acoustic_silence",
        "noise_db": -32.0,
        "min_silence_seconds": 0.3,
        "padding_seconds": 0.1,
    }
    assert payload["removed_seconds"] == 9.8 and payload["cut_count"] == 1


# ── one real pass through ffmpeg ────────────────────────────────────────────

@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg/ffprobe not installed",
)
def test_a_real_clip_loses_its_silence_and_passes_its_own_verification(tmp_path):
    """Tone, silence, tone. The middle should be gone and the result should
    measure as the plan predicted."""
    source = tmp_path / "clip.mp4"
    built = subprocess.run(
        [
            shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30:d=9",
            "-f", "lavfi", "-i",
            "sine=frequency=440:duration=9:sample_rate=48000,"
            "volume=enable='between(t,3,6)':volume=0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", "-y", str(source),
        ],
        capture_output=True, text=True, timeout=180,
    )
    assert built.returncode == 0, built.stderr[-400:]

    plan = sc.plan_silence_cuts(source, min_silence_seconds=0.5)
    assert plan.cuts, f"no silence found: {plan.warnings}"
    assert 2.0 < plan.removed_seconds < 3.0, plan.as_dict()

    report = sc.render_cut_plan(source, plan, tmp_path / "cut.mp4", workers=2)
    assert report.output_path.is_file()
    assert report.ok, report.as_dict()
    assert abs(report.actual_seconds - plan.kept_seconds) < 0.5, report.as_dict()


# ── analysis must not decode video ────────────────────────────────────────

def test_audio_analysis_never_decodes_the_video_stream(monkeypatch):
    """Mechanically checkable, and it cost 16 seconds an upload before it was.

    Every measurement here asks a question about audio. Without `-vn` ffmpeg
    decodes the video stream anyway: measured on a 15 minute recording, the
    whole-file level took 16.4s without the flag and 0.50s with it, for the same
    -22.5dB. The spot check pays it again per sampled cut. This test exists because
    the mistake is invisible in the result — the number is correct either way.
    """
    calls: list[list[str]] = []

    def runner(command, **_kwargs):
        calls.append([str(part) for part in command])
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="mean_volume: -20.0 dB\n")

    # The fake runner means no ffmpeg is executed, but building the command still
    # resolves the binary, so without this the test asks a machine without ffmpeg
    # installed — CI — a question about flag order and gets "ffmpeg not found".
    monkeypatch.setattr(sc, "_ffmpeg_path", lambda: "ffmpeg")

    sc.overall_mean_volume_db("in.mov", runner=runner)
    sc.mean_volume_db("in.mov", TimeRange(10.0, 10.5), runner=runner)

    assert calls, "nothing ran"
    for command in calls:
        joined = " ".join(command)
        assert "volumedetect" in joined
        assert "-vn" in command, f"decodes video to measure audio: {joined}"


# ── the render must not cost more per second than the source ────────────────

class _SourceProbe(_FakeRender):
    """A fake render whose probes answer with the numbers a real file declares.

    Keyed by file name, because the whole point of the ceiling is *which* file it
    was read from: the original, never the intermediate the normalize pass wrote.
    """

    def __init__(
        self,
        *,
        bitrates=None,
        widths=None,
        has_video=True,
        frame_rates=None,
        channels=0,
        sample_rate=0,
        second_bytes=None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        # Bytes of picture per whole second of the source, the reading a real
        # packet table gives. None means the container declares none.
        self.second_bytes = dict(second_bytes or {})
        self.frame_rates = dict(frame_rates or {})
        self.channels = channels
        self.sample_rate = sample_rate
        self.bitrates = dict(bitrates or {})
        self.widths = dict(widths or {})
        self.has_video = has_video
        self.commands: list[list[str]] = []

    def __call__(self, command):
        command = [str(part) for part in command]
        if "ffprobe" in command[0]:
            name = Path(command[-1]).name
            answer = None
            if "stream=bit_rate" in command:
                stream = command[command.index("-select_streams") + 1]
                answer = self.bitrates.get((name, stream), 0)
            elif "format=bit_rate" in command:
                answer = self.bitrates.get((name, "format"), 0)
            elif "stream=width" in command:
                answer = self.widths.get(name, 0)
            elif "stream=codec_type" in command:
                answer = "video" if self.has_video else "audio"
            elif "stream=avg_frame_rate" in command:
                answer = self.frame_rates.get(name, "30/1")
            elif "stream=channels" in command:
                answer = self.channels
            elif "stream=sample_rate" in command:
                answer = self.sample_rate
            elif "packet=pts_time,size" in command:
                rows = self.second_bytes.get(name)
                if rows is None:
                    return subprocess.CompletedProcess(command, 0, "", "")
                answer = "\n".join(f"{second},{count}" for second, count in sorted(rows.items()))
            if answer is not None:
                return subprocess.CompletedProcess(command, 0, f"{answer}\n", "")
        else:
            self.commands.append(command)
        return super().__call__(command)


def _render_command(runner: _SourceProbe) -> list[str]:
    return next(command for command in runner.commands if "-af" in command)


def test_the_encode_ceiling_is_read_off_the_source_not_fixed_in_advance(tmp_path, fake_tools):
    """A 1080p meeting recording carrying 752 kbps came back over 957 kbps at the
    fixed -crf 20: 13.6% of the runtime removed, and the file still cost more per
    second than the one it was cut from."""
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    runner = _SourceProbe(bitrates={("in.mp4", "v:0"): 752000})

    sc.render_keeps(source, [TimeRange(0.0, 5.0)], tmp_path / "out.mp4", runner=runner)

    command = _render_command(runner)
    assert command[command.index("-maxrate:v") + 1] == "714400", "752 kbps, less the 5% margin"
    assert command[command.index("-bufsize:v") + 1] == "714400", (
        "one second of buffer: the average may exceed the ceiling by bufsize/duration"
    )
    assert "-crf" in command, "quality is still the target; the bitrate is only a ceiling"


def test_a_source_that_declares_no_bitrate_is_encoded_exactly_as_before(tmp_path, fake_tools):
    """Matroska routinely records nothing. An unreadable number caps nothing."""
    source = tmp_path / "in.mkv"
    source.write_bytes(b"x")
    runner = _SourceProbe()

    args = sc.encode_args_for_source(source, tmp_path / "out.mkv", audio_only=False, runner=runner)

    assert args == sc.DEFAULT_ENCODE_ARGS


def test_a_container_that_reports_only_a_total_pays_for_the_audio_out_of_it(tmp_path, fake_tools):
    source = tmp_path / "in.mkv"
    source.write_bytes(b"x")
    runner = _SourceProbe(bitrates={("in.mkv", "format"): 900000})

    args = sc.encode_args_for_source(source, tmp_path / "out.mp4", audio_only=False, runner=runner)

    assert args[args.index("-maxrate:v") + 1] == "733400", (
        "900k total, minus the 128k of audio written, minus the 5% margin"
    )


def test_audio_is_not_re_encoded_above_what_the_source_carried(tmp_path, fake_tools):
    """128k AAC cannot put back what a 64k recording never held; it only doubles
    the bytes. Same argument as the picture, applied to the sound."""
    source = tmp_path / "in.m4a"
    source.write_bytes(b"x")
    runner = _SourceProbe(bitrates={("in.m4a", "a:0"): 64000}, has_video=False)

    args = sc.encode_args_for_source(source, tmp_path / "out.m4a", audio_only=True, runner=runner)

    assert args[args.index("-b:a") + 1] == "64000"


def test_a_generously_recorded_source_keeps_the_default_audio_bitrate(tmp_path, fake_tools):
    source = tmp_path / "in.m4a"
    source.write_bytes(b"x")
    runner = _SourceProbe(bitrates={("in.m4a", "a:0"): 256000}, has_video=False)

    args = sc.encode_args_for_source(source, tmp_path / "out.m4a", audio_only=True, runner=runner)

    assert args[args.index("-b:a") + 1] == "128k", "the ceiling is a ceiling, not a target"


def test_the_ceiling_is_measured_before_the_normalize_pass_inflates_it(tmp_path, fake_tools):
    """The intermediate is itself a re-encode. Reading its bitrate would measure
    the inflation this ceiling exists to prevent and then permit it."""
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    runner = _SourceProbe(bitrates={
        ("in.mp4", "v:0"): 600000,
        ("normalized.mp4", "v:0"): 1500000,
    })

    sc.render_cut_plan(
        source,
        build_cut_plan(30.0, _silences((10.0, 12.0))),
        tmp_path / "out.mp4",
        runner=runner,
    )

    command = _render_command(runner)
    assert command[command.index("-maxrate:v") + 1] == "570000", "600 kbps, less the 5% margin"


# ── the render follows the recording's own frame rate ───────────────────────

def test_a_screen_recording_is_rendered_at_the_rate_it_actually_holds():
    """The measurement that settled this. A 2560x1440 capture announced 60fps and
    held 30.3 frames a second — a screen recorder writes a frame when the picture
    changes and declares a nominal ceiling. Rendering the announced 60 duplicated
    every frame: 2.1x the render time, 0.2 MB more file, no new picture."""
    assert sc._rounded_frame_rate("3718500/122711") == 30


def test_material_that_really_is_sixty_is_kept_at_sixty():
    assert sc._rounded_frame_rate("60/1") == 60
    assert sc._rounded_frame_rate("50/1") == 50


def test_film_rates_stop_having_frames_invented_to_reach_thirty():
    """A fixed 30 resampled in both directions: 23.976 and 25fps material was
    being padded up to it."""
    assert sc._rounded_frame_rate("24000/1001") == 24
    assert sc._rounded_frame_rate("25/1") == 25
    assert sc._rounded_frame_rate("30000/1001") == 30


def test_an_absurd_or_unreadable_rate_is_not_followed():
    """Falls back to the fixed rate rather than handing ffmpeg a rate that would
    take hours or fail outright."""
    assert sc._rounded_frame_rate("1000/1") == 0
    assert sc._rounded_frame_rate("0/0") == 0
    assert sc._rounded_frame_rate("") == 0
    assert sc._rounded_frame_rate("not a rate") == 0


def test_the_render_is_normalized_to_the_rate_the_source_holds(tmp_path, fake_tools):
    """End to end: nothing names a rate, so the pass follows the recording."""
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    runner = _SourceProbe(frame_rates={"in.mp4": "24000/1001", "normalized.mp4": "24/1"})

    sc.render_cut_plan(
        source,
        build_cut_plan(30.0, _silences((10.0, 12.0))),
        tmp_path / "out.mp4",
        runner=runner,
    )

    normalize = next(command for command in runner.commands if "-vf" in command and "fps=" in " ".join(command))
    assert "fps=24" in normalize[normalize.index("-vf") + 1]


def test_a_caller_that_names_a_rate_still_wins(tmp_path, fake_tools):
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    runner = _SourceProbe(frame_rates={"in.mp4": "24000/1001", "normalized.mp4": "30/1"})

    sc.render_cut_plan(
        source,
        build_cut_plan(30.0, _silences((10.0, 12.0))),
        tmp_path / "out.mp4",
        fps=30,
        runner=runner,
    )

    normalize = next(command for command in runner.commands if "-vf" in command and "fps=" in " ".join(command))
    assert "fps=30" in normalize[normalize.index("-vf") + 1]


# ── the sound comes back the shape it was recorded in ───────────────────────

def test_a_recording_in_one_channel_does_not_come_back_in_two(tmp_path, fake_tools):
    """The fixed settings were inventing a second channel and resampling upward.
    Neither adds anything that was recorded."""
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    runner = _SourceProbe(bitrates={("in.mp4", "v:0"): 234962}, channels=1, sample_rate=32000)

    args = sc.encode_args_for_source(source, tmp_path / "out.mp4", audio_only=False, runner=runner)

    assert args[args.index("-ac") + 1] == "1"
    assert args[args.index("-ar") + 1] == "32000"


def test_an_unusual_audio_shape_keeps_the_settings_that_were_there_before(tmp_path, fake_tools):
    """A rate AAC will not write, or a channel count above stereo, is left to the
    fixed settings rather than passed on for ffmpeg to refuse."""
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    runner = _SourceProbe(bitrates={("in.mp4", "v:0"): 234962}, channels=6, sample_rate=37000)

    args = sc.encode_args_for_source(source, tmp_path / "out.mp4", audio_only=False, runner=runner)

    assert args[args.index("-ac") + 1] == "2"
    assert args[args.index("-ar") + 1] == "48000"


def test_an_unreadable_bitrate_is_refused_as_a_ceiling_rather_than_enforced(tmp_path, fake_tools):
    """A cap of a few kbps would wreck a file. A probe that reports one is wrong,
    not strict."""
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    runner = _SourceProbe(bitrates={("in.mp4", "v:0"): 900})

    args = sc.encode_args_for_source(source, tmp_path / "out.mp4", audio_only=False, runner=runner)

    assert "-maxrate:v" not in args


# ── the Mac's media engine, only when asked ─────────────────────────────────

def test_hardware_encoding_is_off_unless_asked(tmp_path, fake_tools, monkeypatch):
    """A shared module does not swap encoders for a caller who never asked: this
    file is used by the hosted edition too, so it is an opt-in rather than "we are
    on a Mac, use the Mac encoder"."""
    monkeypatch.delenv("FLUENTFLOW_DEBREATH_HARDWARE_ENCODE", raising=False)
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    runner = _SourceProbe(bitrates={("in.mp4", "v:0"): 400000})

    args = sc.encode_args_for_source(source, tmp_path / "out.mp4", audio_only=False, runner=runner)

    assert "libx264" in args and "h264_videotoolbox" not in args


def test_the_media_engine_is_aimed_at_what_the_recording_carried(tmp_path, fake_tools, monkeypatch):
    """The hardware encoder has no crf, so it gets the source's own bitrate.

    Measured the hard way: asking videotoolbox for 1200k on a source carrying
    about 400 produced a file five times larger than the software one. It spends
    whatever it is given, on detail the recording never had.
    """
    monkeypatch.setenv("FLUENTFLOW_DEBREATH_HARDWARE_ENCODE", "1")
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    runner = _SourceProbe(bitrates={("in.mp4", "v:0"): 400000})

    args = sc.encode_args_for_source(source, tmp_path / "out.mp4", audio_only=False, runner=runner)

    assert args[args.index("-c:v") + 1] == "h264_videotoolbox"
    assert args[args.index("-b:v") + 1] == "380000", "400 kbps, less the same 5% margin"
    assert "-crf" not in args, "the media engine has no quality target to give"
    assert args[args.index("-maxrate:v") + 1] == "380000", "and still capped"


def test_an_unmeasurable_source_stays_on_software(tmp_path, fake_tools, monkeypatch):
    """No trusted bitrate means no hardware encode, rather than a guessed one.
    Guessing for a file whose own rate could not be read is how a lecture comes
    back unreadable — and the screens in it are what the note gets written from."""
    monkeypatch.setenv("FLUENTFLOW_DEBREATH_HARDWARE_ENCODE", "1")
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")

    unreadable = sc.encode_args_for_source(
        source, tmp_path / "out.mp4", audio_only=False, runner=_SourceProbe(),
    )
    implausible = sc.encode_args_for_source(
        source, tmp_path / "out.mp4", audio_only=False,
        runner=_SourceProbe(bitrates={("in.mp4", "v:0"): 1000}),
    )

    assert "libx264" in unreadable
    assert "libx264" in implausible, "an implausible probe is refused"


def test_the_media_engine_is_told_where_to_put_keyframes(tmp_path, fake_tools, monkeypatch):
    """Left to itself videotoolbox writes one about every twelve frames, and under
    a bitrate ceiling that costs the picture rather than bytes.

    Measured on 60 seconds of a 1920x1080 60fps screen recording carrying 517
    kbps, held to its own rate: the default put 301 keyframes in the file and the
    text in it could not be read at any magnification, because a keyframe is a
    whole picture paid for out of the ceiling and the ceiling was buying hundreds
    of bad ones. Five seconds put in 13, matched the source at 3x, and came out
    37% smaller.

    That size figure is a screen recording's, where a keyframe is nearly all
    redundant. Two live-action recordings measured the same day came out 8% and
    3.5% smaller with no visible difference either way, so the setting is right
    on anything and the large win is a screen recording's specifically.
    """
    monkeypatch.setenv("FLUENTFLOW_DEBREATH_HARDWARE_ENCODE", "1")
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    runner = _SourceProbe(bitrates={("in.mp4", "v:0"): 400000})

    args = sc.encode_args_for_source(
        source, tmp_path / "out.mp4", audio_only=False, fps=60, runner=runner,
    )

    assert args[args.index("-g") + 1] == "300", "five seconds at the rate being written"


def test_the_keyframe_interval_follows_the_rate_the_render_writes(tmp_path, fake_tools, monkeypatch):
    """`-g` counts frames, so the same interval in seconds is a different number
    at every rate. Both encodes are checked, because the normalize pass writes the
    intermediate that every batch then reads."""
    monkeypatch.setenv("FLUENTFLOW_DEBREATH_HARDWARE_ENCODE", "1")
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    runner = _SourceProbe(
        bitrates={("in.mp4", "v:0"): 400000},
        frame_rates={"in.mp4": "60/1", "normalized.mp4": "60/1"},
    )

    sc.render_cut_plan(
        source,
        build_cut_plan(30.0, _silences((10.0, 12.0))),
        tmp_path / "out.mp4",
        runner=runner,
    )

    encodes = [command for command in runner.commands if "h264_videotoolbox" in command]
    assert encodes, "the hardware encoder was asked for"
    assert all(command[command.index("-g") + 1] == "300" for command in encodes)


def test_software_encoding_is_left_to_its_own_keyframe_spacing(tmp_path, fake_tools, monkeypatch):
    """x264's default keyint of 250 frames is already in the range this is aiming
    for, and it inserts one at a scene change besides. The setting is there to
    correct videotoolbox, not to override an encoder that had it right."""
    monkeypatch.delenv("FLUENTFLOW_DEBREATH_HARDWARE_ENCODE", raising=False)
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    runner = _SourceProbe(bitrates={("in.mp4", "v:0"): 400000})

    args = sc.encode_args_for_source(
        source, tmp_path / "out.mp4", audio_only=False, fps=60, runner=runner,
    )

    assert "-g" not in args


# ── downscaling, for the sources where bitrate alone cannot help ────────────

def test_downscaling_is_paid_once_in_the_normalize_pass(tmp_path, fake_tools):
    """A 2940x1718 60fps screen recording went 726MB -> 66MB at 1470 wide. Doing
    it here means every later batch reads and writes the smaller picture."""
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    runner = _SourceProbe(widths={"in.mp4": 2940})

    sc.render_cut_plan(
        source,
        build_cut_plan(30.0, _silences((10.0, 12.0))),
        tmp_path / "out.mp4",
        scale_width=1470,
        runner=runner,
    )

    assert runner.filters[0] == "fps=30,scale=1470:-2"
    assert all("scale=" not in expression for expression in runner.filters[1:]), (
        "the cutting pass reads frames that are already the delivered size"
    )


def test_scaling_a_batch_leaves_the_select_expression_alone(tmp_path, fake_tools):
    """The fps -> select -> setpts chain is the one that survived the timestamp
    inflation bug. A resize is appended after it, never woven into it."""
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    runner = _SourceProbe(widths={"in.mp4": 1920})

    sc.render_keeps(
        source, [TimeRange(0.0, 5.0)], tmp_path / "out.mp4", scale_width=960, runner=runner
    )

    assert runner.filters == ["fps=30,select='between(t,0.000,5.000)',setpts=N/30/TB,scale=960:-2"]


def test_an_odd_width_is_rounded_down_to_one_h264_can_write(tmp_path, fake_tools):
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    runner = _SourceProbe(widths={"in.mp4": 2940})
    assert sc.resolve_scale_width(source, 1471, runner=runner) == 1470


def test_a_request_to_upscale_is_refused(tmp_path, fake_tools):
    """Spending bits inventing detail the source never had is the same defect as
    encoding above its bitrate, wearing a different hat."""
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    runner = _SourceProbe(widths={"in.mp4": 1280})
    assert sc.resolve_scale_width(source, 1920, runner=runner) is None
    assert sc.resolve_scale_width(source, None, runner=runner) is None


def test_an_audio_only_render_ignores_a_scale_request(tmp_path, fake_tools):
    source = tmp_path / "in.m4a"
    source.write_bytes(b"x")
    runner = _SourceProbe(streams_per_part="1", has_video=False)

    sc.render_keeps(
        source, [TimeRange(0.0, 5.0)], tmp_path / "out.m4a",
        audio_only=True, scale_width=960, runner=runner,
    )

    assert "-vf" not in _render_command(runner)


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg/ffprobe not installed",
)
def test_a_real_render_never_costs_more_per_second_than_its_source(tmp_path):
    """The whole defect in one assertion, on a source encoded the way the broken
    material was: hard-limited, well below what -crf 20 would ask for.

    Half a minute rather than the nine seconds the other real-ffmpeg tests use,
    because the claim is about recordings. The encoder may average up to one
    buffer above its ceiling, which is a third of the budget across six seconds
    and a rounding error across a lecture.
    """
    source = tmp_path / "clip.mp4"
    built = subprocess.run(
        [
            shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=s=640x480:r=30:d=30",
            "-f", "lavfi", "-i",
            "sine=frequency=440:duration=30:sample_rate=48000,"
            "volume=enable='between(t,3,6)':volume=0",
            "-c:v", "libx264", "-preset", "veryfast",
            "-b:v", "200k", "-maxrate", "200k", "-bufsize", "400k",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", "-shortest",
            "-y", str(source),
        ],
        capture_output=True, text=True, timeout=180,
    )
    assert built.returncode == 0, built.stderr[-400:]
    source_bps = sc.container_bitrate_bps(source)
    assert source_bps > 0

    plan = sc.plan_silence_cuts(source, min_silence_seconds=0.5)
    assert plan.cuts, f"no silence found: {plan.warnings}"

    report = sc.render_cut_plan(source, plan, tmp_path / "cut.mp4")
    assert report.ok, report.as_dict()
    assert sc.container_bitrate_bps(report.output_path) <= source_bps

    # The control: the fixed default is what made the cut copy bigger than the
    # original. Without it this test would pass even if the ceiling did nothing.
    uncapped = tmp_path / "uncapped.mp4"
    sc.render_cut_plan(source, plan, uncapped, encode_args=sc.DEFAULT_ENCODE_ARGS)
    assert sc.container_bitrate_bps(uncapped) > source_bps


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg/ffprobe not installed",
)
def test_a_real_downscaled_render_still_passes_its_own_verification(tmp_path):
    """Scaling is appended to the same filter chain that produces the cuts, so it
    has to be shown that the chain still cuts and still measures up to its plan."""
    source = tmp_path / "clip.mp4"
    built = subprocess.run(
        [
            shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=s=640x480:r=30:d=9",
            "-f", "lavfi", "-i",
            "sine=frequency=440:duration=9:sample_rate=48000,"
            "volume=enable='between(t,3,6)':volume=0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", "-y", str(source),
        ],
        capture_output=True, text=True, timeout=180,
    )
    assert built.returncode == 0, built.stderr[-400:]

    plan = sc.plan_silence_cuts(source, min_silence_seconds=0.5)
    assert plan.cuts, f"no silence found: {plan.warnings}"

    out = tmp_path / "cut.mp4"
    report = sc.render_cut_plan(source, plan, out, scale_width=320)

    assert report.ok, report.as_dict()
    assert sc.video_width(out) == 320
    assert abs(report.actual_seconds - plan.kept_seconds) < 0.5, report.as_dict()
    assert out.stat().st_size < source.stat().st_size


def test_a_recording_with_lifted_gaps_gets_a_higher_threshold(monkeypatch, tmp_path):
    """Enhancement lifts the noise floor and the whole-file average cannot see it.

    Measured on one talk against the original it was made from: -35.3dB of quiet
    floor against -45.1dB, while both averaged about -20dB overall. At the default
    -30dB the enhanced file found 6 gaps and removed 0.0%; the original found 771
    and removed 11.6%. At floor+15 the enhanced file removed 10.9% with 15.2dB of
    separation.
    """
    monkeypatch.setattr(sc, "overall_mean_volume_db", lambda *_a, **_k: -20.9)
    monkeypatch.setattr(sc, "loudness_range_db", lambda *_a, **_k: (-35.3, -16.8))

    chosen, why = sc.suggest_noise_db(tmp_path / "enhanced.m4a")

    assert chosen == -20.3
    assert why["adapted"] is True and why["quiet_floor_db"] == -35.3
    assert "安静的地方" in why["reason"]


def test_a_quiet_floor_far_below_the_default_leaves_it_alone(monkeypatch, tmp_path):
    """The material the default was validated on measures -64dB in its gaps.

    That batch removes 18.7% at 34.5dB of separation with the default, and this
    check exists to fix other material without touching it.
    """
    monkeypatch.setattr(sc, "overall_mean_volume_db", lambda *_a, **_k: -24.2)
    monkeypatch.setattr(sc, "loudness_range_db", lambda *_a, **_k: (-64.0, -20.0))

    chosen, why = sc.suggest_noise_db(tmp_path / "meeting.mov")

    assert chosen == sc.DEFAULT_NOISE_DB and why["adapted"] is False


def test_the_plan_measures_its_own_threshold_and_records_why(tmp_path, fake_tools, monkeypatch):
    """The decision used to live one layer up, in the job pipeline.

    Every caller that did not go through that pipeline — a script, a test, a batch
    entry, the Agent API — silently received the constant, and on material with
    lifted gaps that means cutting 0.0% and reporting success.
    """
    media = tmp_path / "clip.m4a"
    media.write_bytes(b"x")
    runner = _FakeSilenceProbe()
    monkeypatch.setattr(sc, "suggest_noise_db",
                        lambda *_a, **_k: (-20.3, {"adapted": True, "noise_db": -20.3}))

    plan = sc.plan_silence_cuts(media, check_separation=False, runner=runner)

    assert plan.noise_db == -20.3
    assert plan.threshold_choice == {"adapted": True, "noise_db": -20.3}
    assert plan.as_dict()["threshold_choice"]["adapted"] is True


def test_an_explicit_threshold_is_not_second_guessed(tmp_path, fake_tools, monkeypatch):
    """An explicit number is the caller's judgement, and measuring over it would
    silently override a setting the user chose."""
    media = tmp_path / "clip.m4a"
    media.write_bytes(b"x")
    runner = _FakeSilenceProbe()

    def _refuse(*_a, **_k):
        raise AssertionError("the caller picked a threshold; it must not be measured")

    monkeypatch.setattr(sc, "suggest_noise_db", _refuse)

    plan = sc.plan_silence_cuts(media, noise_db=-25.0, check_separation=False, runner=runner)

    assert plan.noise_db == -25.0 and plan.threshold_choice == {}


def test_speech_well_above_the_threshold_keeps_the_default_however_low_the_average(monkeypatch, tmp_path):
    """A lecture with real gaps averages low without its speech being faint.

    Measured: 11.6% of gaps pulled the average to -29.1dB while the speech sat at
    -25.2dB, still 4.8dB clear of the default. Reading the average as "the speech is
    faint" dropped the threshold to -44.1dB and took 771 gaps down to one.
    """
    monkeypatch.setattr(sc, "overall_mean_volume_db", lambda *_a, **_k: -29.1)
    monkeypatch.setattr(sc, "loudness_range_db", lambda *_a, **_k: (-45.1, -25.2))

    chosen, why = sc.suggest_noise_db(tmp_path / "lecture.m4a")

    assert chosen == sc.DEFAULT_NOISE_DB
    assert why["adapted"] is False


def test_the_average_still_lowers_the_threshold_when_the_distribution_is_unreadable(
    monkeypatch, tmp_path
):
    """The phone recording of a classroom, whose -31.1dB average is all that is known.

    That recording is not on this machine any more, so its loudness distribution
    cannot be measured and this asserts the fallback rather than pretending the
    window-level numbers are known. -46.1dB is what it was validated at: 642 cuts
    with 26.8dB of separation, where the default declined and cut nothing.
    """
    monkeypatch.setattr(sc, "overall_mean_volume_db", lambda *_a, **_k: -31.1)
    monkeypatch.setattr(sc, "loudness_range_db", lambda *_a, **_k: None)

    chosen, why = sc.suggest_noise_db(tmp_path / "meeting.m4a")

    assert chosen == -46.1
    assert why["adapted"] is True
    assert "把说话声当成静音" in why["reason"]


def test_gaps_and_speech_too_close_together_protect_the_speech(monkeypatch, tmp_path):
    """Real numbers from a conference recording: gaps -40.0dB, speech -31.6dB.

    8.4dB apart, so a threshold cannot be both 15dB above the gaps and 3dB below the
    speech. Measured on this file and four like it: no threshold removed more than
    0.2%, and the midpoint between the two ends removed 0.0%. The bound that protects
    speech wins, and build_cut_plan's own warning reports the empty result — a number
    that cuts into speech would be worse than a cut that finds nothing.
    """
    monkeypatch.setattr(sc, "overall_mean_volume_db", lambda *_a, **_k: -33.3)
    monkeypatch.setattr(sc, "loudness_range_db", lambda *_a, **_k: (-40.0, -31.6))

    chosen, why = sc.suggest_noise_db(tmp_path / "conference.m4a")

    assert chosen == -34.6
    assert why["separable"] is False
    assert "放不下一条干净的分界线" in why["reason"]


def test_a_threshold_that_would_cut_speech_is_pulled_under_it(monkeypatch, tmp_path):
    """Gaps far below the default, speech only just above it.

    The default would sit 1.6dB under the speech, closer than any cut should be, so it
    comes down to the speech bound instead of staying put.
    """
    monkeypatch.setattr(sc, "overall_mean_volume_db", lambda *_a, **_k: -33.5)
    monkeypatch.setattr(sc, "loudness_range_db", lambda *_a, **_k: (-60.0, -28.4))

    chosen, why = sc.suggest_noise_db(tmp_path / "quiet-speaker.m4a")

    assert chosen == -31.4
    assert why["adapted"] is True and why.get("separable") is not False
    assert "会切进说话声" in why["reason"]


def _render_commands(runner: _SourceProbe) -> list[list[str]]:
    return [command for command in runner.commands if "-af" in command]


def _ceiling_of(command: list[str]) -> int:
    return int(command[command.index("-maxrate:v") + 1])


def test_each_part_is_held_to_what_the_source_spent_on_the_frames_it_keeps(tmp_path, fake_tools):
    """A recording whose content changes gets one ceiling per part, not one average.

    Meeting 15 of a training-camp archive: eighty-five minutes of black screen at
    14 kbps, then a dense document at 200-278 kbps. Its declared average is
    67 kbps, and the document was rendered at a quarter of what the source had
    spent on it — unreadable. The parts are separate ffmpeg calls, so each is
    capped by its own stretch of the source.
    """
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    quiet = {second: 2_000 for second in range(0, 10)}        # 16 kbps
    busy = {second: 35_000 for second in range(10, 20)}       # 280 kbps
    runner = _SourceProbe(
        bitrates={("in.mp4", "v:0"): 67_000},
        second_bytes={"in.mp4": {**quiet, **busy}},
    )

    sc.render_keeps(
        source,
        [TimeRange(0.0, 9.0), TimeRange(10.0, 19.0)],
        tmp_path / "out.mp4",
        ranges_per_batch=1,
        workers=1,
        runner=runner,
    )

    commands = _render_commands(runner)
    assert len(commands) == 2, "one part per batch"
    assert _ceiling_of(commands[0]) == 63_650, "the file average: the quiet stretch is below the trust floor"
    assert _ceiling_of(commands[1]) == 266_000, "280 kbps, less the 5% margin — what the document itself carried"
    assert _ceiling_of(commands[1]) > _ceiling_of(commands[0]), (
        "the point of the change: the readable part is no longer paying for the black one"
    )


def test_the_part_ceiling_moves_the_buffer_with_it(tmp_path, fake_tools):
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    runner = _SourceProbe(
        bitrates={("in.mp4", "v:0"): 67_000},
        second_bytes={"in.mp4": {second: 35_000 for second in range(0, 10)}},
    )

    sc.render_keeps(
        source, [TimeRange(0.0, 9.0)], tmp_path / "out.mp4",
        ranges_per_batch=1, workers=1, runner=runner,
    )

    command = _render_commands(runner)[0]
    assert command[command.index("-bufsize:v") + 1] == "266000", (
        "a buffer left at the file average would throttle the raised ceiling anyway"
    )


def test_a_container_with_no_packet_table_is_rendered_exactly_as_before(tmp_path, fake_tools):
    """Matroska declares no packet sizes. Nothing measurable means nothing changes."""
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    runner = _SourceProbe(bitrates={("in.mp4", "v:0"): 752_000})

    sc.render_keeps(
        source, [TimeRange(0.0, 5.0), TimeRange(6.0, 9.0)], tmp_path / "out.mp4",
        ranges_per_batch=1, workers=1, runner=runner,
    )

    for command in _render_commands(runner):
        assert _ceiling_of(command) == 714_400, "752 kbps, less the margin, on every part"


def test_encode_args_the_caller_chose_are_not_re_pointed_per_part(tmp_path, fake_tools):
    """Passing encode_args explicitly is how a caller opts out of source-derived
    settings; measuring the source anyway would take that choice back."""
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    runner = _SourceProbe(
        bitrates={("in.mp4", "v:0"): 67_000},
        second_bytes={"in.mp4": {second: 35_000 for second in range(0, 20)}},
    )
    chosen = ("-c:v", "libx264", "-crf", "18", "-maxrate:v", "111000", "-bufsize:v", "111000")

    sc.render_keeps(
        source, [TimeRange(0.0, 9.0), TimeRange(10.0, 19.0)], tmp_path / "out.mp4",
        ranges_per_batch=1, workers=1, encode_args=chosen, runner=runner,
    )

    for command in _render_commands(runner):
        assert _ceiling_of(command) == 111_000


def test_a_part_that_measures_below_the_trust_floor_keeps_the_file_ceiling(tmp_path, fake_tools):
    """A few kbps is read as a bad probe, not as a budget — the same judgement the
    whole-file ceiling already makes."""
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    runner = _SourceProbe(
        bitrates={("in.mp4", "v:0"): 600_000},
        second_bytes={"in.mp4": {second: 100 for second in range(0, 10)}},
    )

    sc.render_keeps(
        source, [TimeRange(0.0, 9.0)], tmp_path / "out.mp4",
        ranges_per_batch=1, workers=1, runner=runner,
    )

    assert _ceiling_of(_render_commands(runner)[0]) == 570_000, "600 kbps, less the margin"


def test_ranges_bitrate_reads_only_the_kept_stretches(tmp_path):
    """Silence is cheap to encode, so averaging across the gaps drags the answer
    back towards the file average this exists to get away from."""
    seconds = [1_000] * 10 + [50_000] * 10
    across_both = sc.ranges_bitrate_bps(seconds, [TimeRange(0.0, 19.0)])
    busy_only = sc.ranges_bitrate_bps(seconds, [TimeRange(10.0, 19.0)])

    assert busy_only > across_both * 1.9
    assert sc.ranges_bitrate_bps((), [TimeRange(0.0, 19.0)]) == 0.0
    assert sc.ranges_bitrate_bps(seconds, []) == 0.0


def test_the_pipeline_path_actually_varies_the_part_ceilings(tmp_path, fake_tools):
    """render_cut_plan hands down its own encode args, and an earlier version of
    this feature read that as "the caller chose these, leave them alone" — so the
    per-part ceiling never once ran where it was needed. Rendering a plan is the
    only path a user's recording takes."""
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    quiet = {second: 2_000 for second in range(0, 10)}
    busy = {second: 35_000 for second in range(10, 20)}
    runner = _SourceProbe(
        bitrates={("in.mp4", "v:0"): 67_000},
        second_bytes={"in.mp4": {**quiet, **busy}},
    )
    plan = sc.CutPlan(
        source_duration_seconds=20.0,
        cuts=[TimeRange(9.0, 10.0)],
        keeps=[TimeRange(0.0, 9.0), TimeRange(10.0, 19.0)],
        silences_found=1,
        noise_db=-30.0,
        min_silence_seconds=0.25,
        padding_seconds=0.1,
    )

    sc.render_cut_plan(
        source, plan, tmp_path / "out.mp4",
        ranges_per_batch=1, workers=1, runner=runner,
    )

    ceilings = [_ceiling_of(command) for command in _render_commands(runner)]
    assert 266_000 in ceilings, (
        "the document stretch is held to what the source spent on it, not to the file average"
    )


def test_the_part_ceiling_is_measured_on_the_original_not_the_intermediate(tmp_path, fake_tools):
    """The batches are cut from a normalized copy that has already been re-encoded.
    Measuring that copy would read the normalize pass's output back as if it were
    the recording."""
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    runner = _SourceProbe(
        bitrates={("in.mp4", "v:0"): 67_000},
        second_bytes={
            "in.mp4": {second: 35_000 for second in range(0, 20)},
            "normalized.mp4": {second: 8_000 for second in range(0, 20)},
        },
    )
    plan = sc.CutPlan(
        source_duration_seconds=20.0,
        cuts=[],
        keeps=[TimeRange(0.0, 19.0)],
        silences_found=0,
        noise_db=-30.0,
        min_silence_seconds=0.25,
        padding_seconds=0.1,
    )

    sc.render_cut_plan(
        source, plan, tmp_path / "out.mp4",
        ranges_per_batch=25, workers=1, runner=runner,
    )

    assert _ceiling_of(_render_commands(runner)[0]) == 266_000, (
        "280 kbps off the original, not 64 kbps off the intermediate"
    )
