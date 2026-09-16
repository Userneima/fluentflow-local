"""Remove breath gaps from a recording by acoustic silence alone.

No model is involved. Silence detection is measurement, so it belongs in code;
deciding whether a given pause carries meaning does not, and this module does not
try. It removes gaps that are quiet, and it reports what it removed.

The cut list is the deliverable. The rendered video is one rendering of it, at
one padding value, and can be produced again from the same list; the judgement of
where to cut cannot be recovered from an mp4. Callers should persist the plan
even when they also keep the render.

Two behaviours here look like extra work and are not. Both come from real broken
output, and removing either brings the failure straight back:

1. The source is re-encoded to a strictly constant frame rate before any cutting.
   Screen recordings routinely declare a frame rate they do not have (15fps
   running at 29.4, 60fps at 25.3). Cutting renumbers frames by count, which is
   only correct at the declared rate, so cutting an uncorrected file produced
   video that ran at a third of its container length — slow motion against
   normal-speed audio. Correcting inside the cutting pass was not enough once
   many batches were concatenated: three of thirteen files came out with
   timestamps inflated 3.128x, and a short test missed it because the corruption
   only appears at scale.
2. Keep ranges are clamped to the length of the *video* stream, never the
   container. Silence is detected on audio, and these recordings carry audio that
   outlasts their video by up to 1.1s, so the last silence can end past the final
   frame. One keep range began 0.036s after the video ended; ffmpeg rendered it
   as a 4KB audio-only part, and concatenating a part with a missing stream
   inflated the whole output.

Nothing rendered here may cost more per second than the file it was cut from.
Frame-accurate cuts cannot be made by stream copy, so every output is re-encoded,
and a fixed quality target spends whatever that target asks for regardless of what
the source carried: a 1080p meeting recording holding 752 kbps came back over 957
kbps, so removing 13.6% of its runtime still produced a bigger file. The encode
ceiling is therefore read off the source, and callers that need a real reduction
can ask for a smaller picture instead — the one lever that shrinks a screen
recording by an order of magnitude.

Checks report, they never destroy. A render that fails verification is still
written and still returned, flagged. An earlier version of this check was given
authority to delete and used it on three faithful renders — two had inherited
their source's own audio/video skew, one had drifted a few seconds of frame
metadata across 198 concatenated parts. Reporting costs a second look; deleting
cost an hour of re-encoding.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any, Callable, Sequence

logger = logging.getLogger(__name__)

# Material-dependent on purpose, and deliberately not tuned to one number.
# Measured: one lecture gave 197 silent stretches at -30dB and 171 at -35dB;
# another gave 33 at -30dB but only 13 at -35dB, because background music holds
# its noise floor above -35dB. Under-detection is silent, so a caller working on
# unfamiliar material should probe before trusting the default.
DEFAULT_NOISE_DB = -30.0
DEFAULT_MIN_SILENCE_SECONDS = 0.25

# Cutting flush against speech clips breath onsets and plosives, and sounds
# chopped. Leaving a sliver of each removed stretch at both ends fixes it. 0.10
# was chosen by ear, not measured.
DEFAULT_PADDING_SECONDS = 0.10

# A kept fragment shorter than a few frames carries no useful picture, and is the
# shape that turned into an audio-only part.
MIN_KEEP_SECONDS = 0.10

DEFAULT_RENDER_FPS = 30

# Above this, following the source's own frame rate stops being fidelity and
# starts being a way to spend hours of CPU on frames nobody asked for. Ordinary
# 24/25/30/50/60 material is well inside it.
MAX_FOLLOWED_FRAME_RATE = 120

# The ceiling being worked around is per-filter, not per-render: one select
# expression holding 167 `between()` terms made ffmpeg die with "Cannot allocate
# memory" while a 17-term one was fine. Batching removes the limit entirely —
# 1300 cuts on a 3h19m video render fine — so this is not a cap on cut count.
DEFAULT_RANGES_PER_BATCH = 25
DEFAULT_RENDER_WORKERS = 4

DEFAULT_ENCODE_ARGS = (
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
)

# The frame rate correction pass. Slightly cheaper than the delivery encode
# because its output is a scratch file that the cutting pass reads and deletes.
NORMALIZE_ENCODE_ARGS = (
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
)

# The ceiling caps the video encoder, but the promise is about the delivered
# file, which also carries audio and container overhead. Reading the source's
# rate and spending exactly that leaves no room for either, so the file can land
# a little above the one it was cut from while every stream is technically within
# budget. 5% is what covers it; on material that is already at its ceiling the
# difference is not visible, and material that never reaches the ceiling is
# untouched by it.
BITRATE_CEILING_MARGIN = 0.95

# Below this a measured video bitrate is read as a bad probe rather than a
# ceiling. Enforcing a few kbps would wreck a file, and no recording this product
# transcribes sits down there.
MIN_TRUSTED_VIDEO_BITRATE = 50_000

# How far apart to put keyframes on the hardware path. VideoToolbox's own default
# is around twelve frames — a fifth of a second on 60fps material — and under a
# bitrate ceiling that is not a size question but a legibility one: a keyframe is
# a whole picture paid for out of the same budget, so the budget buys hundreds of
# bad ones instead of a few good ones and the differences between them.
#
# Measured on 2026-09-15, 60 seconds of a 1920x1080 60fps screen recording
# carrying 517 kbps, held to its own rate: the default wrote 301 keyframes and the
# text in the result could not be read at any magnification; five seconds wrote
# 13, matched the source at 3x, and came out 37% smaller because it no longer
# needed the whole budget. On a 6 Mbps recording the same defect shows up only as
# size, because at that rate there is enough budget to waste.
#
# Five seconds rather than the source's own interval: the number lived with is a
# fixed one either way, and libx264's default keyint of 250 frames is already in
# this range, so the two encoders do not disagree about the same file. It also
# bounds what reads this output later — the visual-note pass detects picture
# changes with `-skip_frame nokey`, so the keyframe interval is the finest look
# it can take.
#
# How much this is worth depends on the material, and the measurement above is a
# screen recording, where the picture is mostly still and a keyframe is therefore
# nearly all redundant. Two live-action recordings measured the same day — a
# 640x360 25fps interview at 266 kbps and a 1024x576 30fps cut-heavy explainer at
# 1709 kbps — came out 8% and 3.5% smaller, with the face at 5x indistinguishable
# from the same encode without the setting. So it is the right direction on
# anything, and the large win belongs to screen recordings specifically.
HARDWARE_KEYFRAME_SECONDS = 5.0

# How much room the normalize pass gets above the source's own bitrate. Its
# output never reaches the user, so this ceiling is there to bound scratch disk,
# not the delivered file — and starting the cutting pass from an intermediate
# already squeezed to the final ceiling would spend the budget twice on the same
# picture. 1.5 is a judgement, not a measurement.
NORMALIZE_BITRATE_HEADROOM = 1.5

# Audio-only sources take a shorter path: no frame rate to normalize, no picture
# to renumber, and no audio/video skew to measure. Everything that makes video
# cutting delicate simply does not apply, so those steps are skipped rather than
# faked. Channel count and sample rate are left alone — a lecture recorded in
# mono should not come back as stereo.
DEFAULT_AUDIO_ENCODE_ARGS = ("-c:a", "aac", "-b:a", "128k")
MP3_ENCODE_ARGS = ("-c:a", "libmp3lame", "-b:a", "192k")

# Outside this band the detection threshold probably does not suit the material:
# near 0% means nothing was found and the file will come back unchanged, while a
# very high share means speech is being read as silence. Reported, never enforced
# — a slide-heavy recording really can be half quiet.
PLAUSIBLE_SILENCE_RATIO_PERCENT = (5.0, 60.0)

# How far below the kept speech the removed stretches should measure. The share
# of a file that is silent says nothing about whether the threshold separated
# speech from silence, and a phone recording of a classroom proved it: 48% of the
# runtime was marked silent, inside the plausible band, while in the faint
# stretches the removed pieces measured -39dB against speech at -37dB and a
# single sentence was being chopped into a dozen pieces. Comparing the two levels
# catches that; a ratio cannot.
#
# 10dB is a judgement, not a measured optimum. On that recording the safe
# stretches separated by about 15dB and the shredded ones by nearly nothing.
MIN_LEVEL_SEPARATION_DB = 10.0

# Stamped into every file this module renders, and looked for at intake. A
# filename can be changed and a directory can be moved; this travels with the
# bytes, which is the only way to recognise our own output for certain. It says
# nothing about files other tools produced — see `already_cut_reason`.
CUT_FILE_MARK = "fluentflow_debreath=1"

CommandRunner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


class SilenceCutError(RuntimeError):
    """A cut could not be planned or rendered."""


def run_command(command: Sequence[str], timeout: float = 14400) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [str(part) for part in command],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _ffmpeg_path() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise SilenceCutError("ffmpeg not found")
    return path


def _ffprobe_path() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise SilenceCutError("ffprobe not found")
    return path


@dataclass(frozen=True)
class TimeRange:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def as_dict(self) -> dict[str, float]:
        return {"start": round(self.start, 3), "end": round(self.end, 3)}


@dataclass
class CutPlan:
    """Which stretches to remove, which to keep, and how that was decided."""

    source_duration_seconds: float
    cuts: list[TimeRange]
    keeps: list[TimeRange]
    silences_found: int
    noise_db: float
    min_silence_seconds: float
    padding_seconds: float
    warnings: list[str] = field(default_factory=list)
    level_separation: dict[str, Any] = field(default_factory=dict)
    # Why this threshold, when the caller did not pick it. Empty when they did.
    threshold_choice: dict[str, Any] = field(default_factory=dict)

    @property
    def removed_seconds(self) -> float:
        return round(sum(cut.duration for cut in self.cuts), 3)

    @property
    def kept_seconds(self) -> float:
        return round(sum(keep.duration for keep in self.keeps), 3)

    @property
    def silence_ratio_percent(self) -> float:
        if self.source_duration_seconds <= 0:
            return 0.0
        return round(self.removed_seconds / self.source_duration_seconds * 100, 1)

    def as_dict(self) -> dict[str, Any]:
        """The persisted cut list. Read back by re-render, timeline export, or review."""
        return {
            "cut_list_version": "1",
            "source_duration_seconds": round(self.source_duration_seconds, 3),
            "kept_seconds": self.kept_seconds,
            "removed_seconds": self.removed_seconds,
            "removed_percent": self.silence_ratio_percent,
            "cut_count": len(self.cuts),
            "silences_found": self.silences_found,
            "detection": {
                "method": "acoustic_silence",
                "noise_db": self.noise_db,
                "min_silence_seconds": self.min_silence_seconds,
                "padding_seconds": self.padding_seconds,
            },
            "warnings": list(self.warnings),
            "level_separation": dict(self.level_separation),
            "threshold_choice": dict(self.threshold_choice) or None,
            "cuts": [cut.as_dict() for cut in self.cuts],
            "keeps": [keep.as_dict() for keep in self.keeps],
        }


@dataclass
class RenderReport:
    """What was rendered, and how it measures against the plan."""

    output_path: Path
    expected_seconds: float
    actual_seconds: float
    video_seconds: float
    audio_seconds: float
    frame_count: int
    batches: int
    checks: dict[str, bool]
    source_skew_seconds: float
    audio_only: bool = False

    @property
    def ok(self) -> bool:
        return all(self.checks.values())

    @property
    def failed_checks(self) -> list[str]:
        return [name for name, passed in self.checks.items() if not passed]

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "output_path": str(self.output_path),
            "audio_only": self.audio_only,
            "expected_seconds": round(self.expected_seconds, 3),
            "actual_seconds": round(self.actual_seconds, 3),
            "audio_seconds": round(self.audio_seconds, 3),
            "batches": self.batches,
            "checks": dict(self.checks),
            "verified": self.ok,
            "failed_checks": self.failed_checks,
        }
        if not self.audio_only:
            # Reporting a skew against a video stream that does not exist reads
            # as a huge sync problem; there is simply no picture.
            payload.update({
                "video_seconds": round(self.video_seconds, 3),
                "audio_video_skew_ms": round(abs(self.audio_seconds - self.video_seconds) * 1000),
                "source_audio_video_skew_ms": round(self.source_skew_seconds * 1000),
                "frame_count": self.frame_count,
            })
        return payload


def probe_value(media: Path | str, args: Sequence[str], runner: CommandRunner = run_command) -> str:
    result = runner([_ffprobe_path(), "-v", "error", *args, "-of", "csv=p=0", str(media)])
    return (result.stdout or "").strip()


def probe_float(media: Path | str, args: Sequence[str], runner: CommandRunner = run_command) -> float:
    raw = probe_value(media, args, runner=runner).splitlines()
    for line in raw:
        try:
            return float(line.strip())
        except ValueError:
            continue
    return 0.0


def has_video_stream(media: Path | str, runner: CommandRunner = run_command) -> bool:
    codec = probe_value(media, ["-select_streams", "v:0", "-show_entries", "stream=codec_type"], runner=runner)
    return codec.strip().splitlines()[:1] == ["video"]


def media_duration_seconds(media: Path | str, runner: CommandRunner = run_command) -> float:
    """Length of the video stream when there is one, else the container.

    The distinction matters for video: keep ranges clamped to the container can
    start after the last frame. See this module's docstring. For an audio-only
    file the container length is the only length there is.
    """
    stream = probe_float(media, ["-select_streams", "v:0", "-show_entries", "stream=duration"], runner=runner)
    if stream > 0:
        return stream
    return probe_float(media, ["-show_entries", "format=duration"], runner=runner)


def stream_skew_seconds(media: Path | str, runner: CommandRunner = run_command) -> float:
    """How far apart this file's audio and video stream lengths already are."""
    video = probe_float(media, ["-select_streams", "v:0", "-show_entries", "stream=duration"], runner=runner)
    audio = probe_float(media, ["-select_streams", "a:0", "-show_entries", "stream=duration"], runner=runner)
    if video <= 0 or audio <= 0:
        return 0.0
    return abs(audio - video)


def stream_bitrate_bps(media: Path | str, stream: str, runner: CommandRunner = run_command) -> float:
    """What one stream declares it spends per second, or 0 when it declares nothing.

    Matroska and some webm files record no per-stream bitrate at all, which is why
    every caller has to have an answer for 0 rather than treating it as "free".
    """
    return probe_float(media, ["-select_streams", stream, "-show_entries", "stream=bit_rate"], runner=runner)


def container_bitrate_bps(media: Path | str, runner: CommandRunner = run_command) -> float:
    """The whole file's bitrate, picture and sound together."""
    return probe_float(media, ["-show_entries", "format=bit_rate"], runner=runner)


def video_second_bytes(media: Path | str, runner: CommandRunner = run_command) -> tuple[int, ...]:
    """How many bytes of picture the source spends in each whole second of itself.

    One ffprobe pass over the video packet table. The point is that a recording's
    bitrate is not one number: a lecture that shows a black screen for the first
    eighty-five minutes and a dense document for the last twenty carries 14 kbps
    and 278 kbps in the same file. A ceiling taken from the average of those is
    set by the black, and starves the only part anybody needs to read.

    Empty when the container declares no packet sizes (Matroska is the common
    case), which every caller has to treat as "measure it the old way" rather
    than as "spends nothing".
    """
    raw = probe_value(
        media,
        ["-select_streams", "v:0", "-show_entries", "packet=pts_time,size"],
        runner=runner,
    )
    seconds: dict[int, int] = {}
    for line in raw.splitlines():
        stamp, _, size = line.strip().partition(",")
        try:
            index, count = int(float(stamp)), int(size)
        except ValueError:
            continue
        if index < 0:
            continue
        seconds[index] = seconds.get(index, 0) + count
    if not seconds:
        return ()
    return tuple(seconds.get(index, 0) for index in range(max(seconds) + 1))


def ranges_bitrate_bps(second_bytes: Sequence[int], ranges: Sequence[TimeRange]) -> float:
    """What the source spent per second across exactly the stretches being kept.

    Silence is cheap to encode, so measuring across the gaps as well would drag
    the answer back down towards the average this exists to get away from. 0 when
    there is nothing to measure, which reads the same as an unreadable probe.
    """
    if not second_bytes or not ranges:
        return 0.0
    total = 0
    span = 0.0
    last = len(second_bytes)
    for item in ranges:
        first_index = max(0, int(item.start))
        stop_index = min(last, int(item.end) + 1)
        if stop_index <= first_index:
            continue
        total += sum(second_bytes[first_index:stop_index])
        span += stop_index - first_index
    if span <= 0:
        return 0.0
    return total * 8.0 / span


def _with_ceiling_values(args: Sequence[str], ceiling_bps: float) -> list[str]:
    """Re-point already-built encoder settings at a different ceiling.

    Only the rate-control numbers move. Which encoder, which pixel format, which
    audio shape are decided once for the whole file and stay decided: the parts
    are concatenated with `-c copy`, and a stream that changes encoder halfway
    through is not one file.
    """
    if ceiling_bps <= 0:
        return list(args)
    target = str(int(ceiling_bps * BITRATE_CEILING_MARGIN))
    out = list(args)
    for index in range(len(out) - 1):
        if out[index] in {"-maxrate:v", "-bufsize:v", "-b:v"}:
            out[index + 1] = target
    return out


def video_width(media: Path | str, runner: CommandRunner = run_command) -> int:
    raw = probe_value(media, ["-select_streams", "v:0", "-show_entries", "stream=width"], runner=runner)
    try:
        return int(raw.splitlines()[0].strip())
    except (ValueError, IndexError):
        return 0


def _rounded_frame_rate(raw: str) -> int:
    """An `avg_frame_rate` reading as whole frames a second, or 0 if unusable."""
    numerator, _, denominator = (raw or "").strip().partition("/")
    try:
        top, bottom = float(numerator), float(denominator or 1)
    except ValueError:
        return 0
    if bottom <= 0 or top <= 0:
        return 0
    rate = round(top / bottom)
    return rate if 1 <= rate <= MAX_FOLLOWED_FRAME_RATE else 0


def source_frame_rate(media: Path | str, runner: CommandRunner = run_command) -> int:
    """How many frames a second the recording actually holds. 0 when unreadable.

    The average, deliberately, not the declared rate. A screen recorder writes a
    frame when the picture changes and declares a nominal ceiling: one 2560x1440
    capture announced 60fps while holding 30.3. Rendering its declared 60 recovers
    nothing — there is nothing to recover — it duplicates every frame, measured at
    2.1x the render time for 0.2MB more file and not one new picture.

    Rounded rather than required to be whole, because the average almost never is.
    Refusing anything fractional would leave 23.976 material resampled up to the
    fixed rate, which is the same defect in the other direction.
    """
    return _rounded_frame_rate(
        probe_value(media, ["-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate"], runner=runner)
    )


def parse_silencedetect(stderr: str, offset: float = 0.0) -> list[TimeRange]:
    """Pair up ffmpeg's silence_start/silence_end lines.

    An unpaired start is dropped rather than guessed at: ffmpeg omits the final
    silence_end when a file ends mid-silence, and inventing an end would place a
    cut past the last frame.
    """
    starts: list[float] = []
    ranges: list[TimeRange] = []
    for line in (stderr or "").splitlines():
        if "silence_start:" in line:
            try:
                starts.append(float(line.split("silence_start:", 1)[1].split()[0]) + offset)
            except (ValueError, IndexError):
                continue
        elif "silence_end:" in line and starts:
            try:
                end = float(line.split("silence_end:", 1)[1].split()[0]) + offset
            except (ValueError, IndexError):
                continue
            ranges.append(TimeRange(starts.pop(0), end))
    return ranges


def detect_silences(
    media: Path | str,
    *,
    noise_db: float = DEFAULT_NOISE_DB,
    min_silence_seconds: float = DEFAULT_MIN_SILENCE_SECONDS,
    runner: CommandRunner = run_command,
) -> list[TimeRange]:
    """Acoustically silent stretches, measured — not judged.

    Transcript gaps are not an alternative detector. Measured on one lecture,
    gaps between transcript segments found 0 candidates where acoustic detection
    found 197 covering 15.2% of the runtime: ASR segmentation swallows pauses,
    and how much it swallows depends on the engine.
    """
    result = runner([
        _ffmpeg_path(), "-hide_banner", "-nostats",
        "-i", str(media),
        "-af", f"silencedetect=noise={noise_db}dB:d={max(0.02, float(min_silence_seconds))}",
        "-vn", "-f", "null", "-",
    ])
    return parse_silencedetect(result.stderr or "")


def build_cut_plan(
    duration_seconds: float,
    silences: Sequence[TimeRange],
    *,
    noise_db: float = DEFAULT_NOISE_DB,
    min_silence_seconds: float = DEFAULT_MIN_SILENCE_SECONDS,
    padding_seconds: float = DEFAULT_PADDING_SECONDS,
    min_keep_seconds: float = MIN_KEEP_SECONDS,
) -> CutPlan:
    """Turn silent stretches into a cut list and its complement.

    For video, ``duration_seconds`` must be the video stream's length, not the
    container's; see this module's docstring for what happens otherwise.
    """
    duration = max(0.0, float(duration_seconds))
    cuts: list[TimeRange] = []
    for silence in sorted(silences, key=lambda item: item.start):
        start = max(0.0, silence.start + padding_seconds)
        end = min(duration, silence.end - padding_seconds)
        # A stretch that padding consumes entirely is not a cut. The 0.02 margin
        # keeps single-frame cuts, which cost an encode and save nothing, out.
        if end - start <= 0.02:
            continue
        if cuts and start <= cuts[-1].end:
            cuts[-1] = TimeRange(cuts[-1].start, max(cuts[-1].end, end))
        else:
            cuts.append(TimeRange(start, end))

    keeps: list[TimeRange] = []
    cursor = 0.0
    for cut in cuts:
        if cut.start > cursor:
            keeps.append(TimeRange(round(cursor, 3), round(min(cut.start, duration), 3)))
        cursor = max(cursor, cut.end)
    if cursor < duration:
        keeps.append(TimeRange(round(cursor, 3), round(duration, 3)))
    keeps = [keep for keep in keeps if keep.duration > min_keep_seconds]

    plan = CutPlan(
        source_duration_seconds=duration,
        cuts=[TimeRange(round(cut.start, 3), round(cut.end, 3)) for cut in cuts],
        keeps=keeps,
        silences_found=len(silences),
        noise_db=noise_db,
        min_silence_seconds=min_silence_seconds,
        padding_seconds=padding_seconds,
    )
    low, high = PLAUSIBLE_SILENCE_RATIO_PERCENT
    ratio = plan.silence_ratio_percent
    if plan.cuts and ratio < low:
        plan.warnings.append(
            f"只找到 {ratio:.1f}% 的静音，低于 {low:g}%：阈值 {noise_db:g}dB 可能不适合这份材料"
        )
    if ratio > high:
        plan.warnings.append(
            f"静音占 {ratio:.1f}%，高于 {high:g}%：可能把说话当成了静音，先看剪辑表再渲染"
        )
    if not plan.cuts:
        plan.warnings.append("没有找到可剪的气口，成品会和原片一样长")
    return plan


def mean_volume_db(
    media: Path | str,
    span: TimeRange,
    runner: CommandRunner = run_command,
) -> float | None:
    """Average level of one stretch, or None when it cannot be measured."""
    result = runner([
        _ffmpeg_path(), "-hide_banner", "-nostats",
        "-ss", f"{span.start:.3f}", "-t", f"{max(0.05, span.duration):.3f}",
        # -vn or this decodes the video stream to answer a question about audio.
        "-i", str(media), "-vn", "-af", "volumedetect", "-f", "null", "-",
    ])
    for line in (result.stderr or "").splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:", 1)[1].split()[0])
            except (ValueError, IndexError):
                return None
    return None


def _spread_sample(ranges: Sequence[TimeRange], count: int) -> list[TimeRange]:
    """Take `count` ranges spread across the file, not the first `count`."""
    if len(ranges) <= count:
        return list(ranges)
    step = len(ranges) / count
    return [ranges[min(len(ranges) - 1, int(index * step))] for index in range(count)]


def _neighbouring_keeps(plan: CutPlan, cut: TimeRange) -> list[TimeRange]:
    before = [keep for keep in plan.keeps if keep.end <= cut.start + 0.001]
    after = [keep for keep in plan.keeps if keep.start >= cut.end - 0.001]
    return [span for span in (before[-1] if before else None, after[0] if after else None) if span]


def measure_level_separation(
    media: Path | str,
    plan: CutPlan,
    *,
    samples: int = 10,
    runner: CommandRunner = run_command,
) -> dict[str, Any]:
    """How far each removed stretch sits below the audio on either side of it.

    Deliberately local. A first version of this compared the median of all cuts
    against the median of all keeps, and on a faint classroom recording it
    reported a comfortable 20dB while, in the quiet stretches of that same file,
    removed pieces measured within 2-5dB of their own neighbours. A whole-file
    median hides exactly the case worth knowing about, because most of the file
    really is clean.

    It measures and reports. Whether a narrow margin matters is the caller's
    call: it can mean quiet speech is being cut, or simply that nobody was
    speaking loudly in that part of the room.
    """
    margins: list[float] = []
    cut_levels: list[float] = []
    speech_levels: list[float] = []
    for cut in _spread_sample(plan.cuts, samples):
        cut_level = mean_volume_db(media, cut, runner)
        if cut_level is None:
            continue
        neighbours = [
            level
            for level in (mean_volume_db(media, span, runner) for span in _neighbouring_keeps(plan, cut))
            if level is not None
        ]
        if not neighbours:
            continue
        speech = max(neighbours)
        cut_levels.append(cut_level)
        speech_levels.append(speech)
        margins.append(speech - cut_level)
    if not margins:
        return {"measured": False}
    narrow = [margin for margin in margins if margin < MIN_LEVEL_SEPARATION_DB]
    return {
        "measured": True,
        "sampled_cuts": len(margins),
        "cut_median_db": round(median(cut_levels), 1),
        "neighbour_median_db": round(median(speech_levels), 1),
        "separation_db": round(median(margins), 1),
        "narrowest_db": round(min(margins), 1),
        "narrow_cut_count": len(narrow),
        "separated": not narrow,
    }


# Below this, the fixed threshold is inside the speech rather than under it.
# Measured: a WeChat meeting recording averages -31.1dB overall, so the default
# -30dB judged a great deal of quiet speech to be silence — 1552 cuts, and the spot
# check found three of ten sampled cuts sitting within 7.5dB of their neighbouring
# speech. Normal close-mic speech on this machine measures around -20dB and is not
# affected. Raising the recording's volume would work as well and for the same
# reason, but it means re-encoding and handing the user a file whose loudness they
# did not ask to change; moving the threshold costs one measurement pass.
FAINT_MEAN_VOLUME_DB = -28.0

# How far under the material's own average to put the threshold. 15dB is a
# judgement, not an optimum: on the recording above it produced 26.8dB of
# separation where the default produced 7.5dB.
FAINT_THRESHOLD_MARGIN_DB = 15.0

# A floor, because a near-silent file (one measured -91dB) would otherwise push the
# threshold somewhere meaningless. The existing ratio warnings handle material that
# absurd; this only keeps the number sane.
MIN_ADAPTED_NOISE_DB = -55.0

# The check above asks whether the recording is faint. That question misses the
# opposite failure: material whose overall level is normal while its gaps have
# been lifted, which is exactly what audio "enhancement" does to a recording.
# Measured on one talk, the enhanced file against the original it was made from:
# the quiet windows sat at -35.3dB enhanced and -45.1dB original, while both
# averaged about -20dB overall. At the default -30dB the enhanced file yielded
# 6 cuts and 0.0% removed; the original yielded 771 cuts and 11.6%. The overall
# average cannot see that difference. The low end of the short-window loudness
# distribution can, because that low end IS the gaps.
QUIET_WINDOW_SECONDS = 0.25
QUIET_FLOOR_PERCENTILE = 0.05

# How far above the material's own quiet floor the threshold has to sit to cut
# through it. Read off the same file: floor -35.3 + 15 = -20.3, and a scan of that
# file found -20 to be the best threshold available (10.9% removed at 15.2dB of
# separation). One step further, -17dB, put the narrowest cut 0.2dB ABOVE its
# neighbouring speech — that is no longer cutting gaps.
QUIET_FLOOR_MARGIN_DB = 15.0

# The speech end of the same distribution, and how far above the threshold it has
# to sit for the threshold to be safe.
#
# The faint check below reads the whole-file average as a stand-in for "how loud is
# the speech", and that stand-in fails in a way that was measured here: a lecture
# with 11.6% of gaps averaged -29.1dB while its speech sat around -16dB. The average
# crossed the -28dB line, the threshold was dropped to -44.1dB, and the cut that had
# been finding 771 gaps found one. The average is pulled down by however much of the
# file is silence; the speech level is not.
#
# The recording the faint check was built for is the opposite shape: its speech
# measured -37dB, below the default threshold itself. Speech under the threshold is
# the condition worth acting on, and it is what this measures.
SPEECH_LEVEL_PERCENTILE = 0.90

# The threshold has to land between the gaps and the speech. Three shapes were
# measured, and each puts the default -30dB somewhere different:
#   original lecture: gaps -45.1, speech -25.2 -> between them, 11.6% removed
#   the same, enhanced: gaps -35.3, speech -16.8 -> below the gaps, 0.0% removed
#   phone recording:    gaps -39.0, speech -37.0 -> above the speech, cuts words
# So 3dB is the minimum room the speech needs above the threshold — a halving of
# power, not a number picked for comfort. An earlier attempt at 10dB failed the
# first row: 4.8dB of real headroom was read as "faint" and the threshold dropped
# to -44.1dB, taking 771 gaps down to one.
MIN_SPEECH_HEADROOM_DB = 3.0


def overall_mean_volume_db(media: Path | str, runner: CommandRunner = run_command) -> float | None:
    """The average level of the whole file, or None when it cannot be measured."""
    result = runner([
        _ffmpeg_path(), "-hide_banner", "-nostats",
        # -vn is not a detail: measured on a 15 minute recording, this took 16.4s
        # without it and 0.50s with it, for the same -22.5dB. The video stream was
        # being fully decoded to answer a question about the audio, on every upload.
        "-i", str(media), "-vn", "-af", "volumedetect", "-f", "null", "-",
    ])
    for line in (result.stderr or "").splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:", 1)[1].split()[0])
            except (ValueError, IndexError):
                return None
    return None


def quiet_floor_db(
    media: Path | str,
    *,
    window_seconds: float = QUIET_WINDOW_SECONDS,
    percentile: float = QUIET_FLOOR_PERCENTILE,
    runner: CommandRunner = run_command,
) -> float | None:
    """How quiet the quiet parts of this recording actually are.

    Splits the audio into short windows, measures each one, and returns the low
    end of that distribution. The low end is the gaps — the thing a silence
    threshold has to cut through — which is why this answers a question the
    whole-file average cannot (see QUIET_WINDOW_SECONDS).

    Resampled to a fixed rate first so the window stays a duration rather than
    becoming a sample count that means different things at 16kHz and 48kHz.
    Returns None when nothing could be measured, and the caller keeps its default.
    """
    levels = _window_levels_db(media, window_seconds=window_seconds, runner=runner)
    if not levels:
        return None
    return _percentile_db(levels, percentile)


def loudness_range_db(
    media: Path | str,
    *,
    window_seconds: float = QUIET_WINDOW_SECONDS,
    runner: CommandRunner = run_command,
) -> tuple[float, float] | None:
    """(quiet floor, speech level) from a single pass over the audio.

    Both ends of the distribution come from the same measurement because both
    questions have to be asked about every file — whether the gaps are reachable,
    and whether the speech is above the threshold — and asking them separately
    would decode the file twice.
    """
    levels = _window_levels_db(media, window_seconds=window_seconds, runner=runner)
    if not levels:
        return None
    return (
        _percentile_db(levels, QUIET_FLOOR_PERCENTILE),
        _percentile_db(levels, SPEECH_LEVEL_PERCENTILE),
    )


def _percentile_db(sorted_or_not: list[float], percentile: float) -> float:
    values = sorted(sorted_or_not)
    return round(values[min(len(values) - 1, int(len(values) * percentile))], 1)


def _window_levels_db(
    media: Path | str,
    *,
    window_seconds: float = QUIET_WINDOW_SECONDS,
    runner: CommandRunner = run_command,
) -> list[float]:
    """Loudness of each short window, unsorted. Empty when nothing could be read."""
    samples = max(1, int(48000 * window_seconds))
    result = runner([
        _ffmpeg_path(), "-hide_banner", "-nostats",
        # -vn for the same reason as the whole-file measurement above: this is a
        # question about audio and decoding the picture to answer it is pure cost.
        "-i", str(media), "-vn",
        "-af", (
            f"aresample=48000,asetnsamples=n={samples},astats=metadata=1:reset=1,"
            "ametadata=print:key=lavfi.astats.Overall.RMS_level"
        ),
        "-f", "null", "-",
    ])
    levels: list[float] = []
    for line in ((result.stdout or "") + "\n" + (result.stderr or "")).splitlines():
        if "astats.Overall.RMS_level=" not in line:
            continue
        raw = line.split("astats.Overall.RMS_level=", 1)[1].strip()
        # A window of digital silence reads as -inf. It is a real measurement of a
        # real gap, so it is kept as a very low number rather than discarded.
        if raw.startswith("-inf"):
            levels.append(-120.0)
            continue
        try:
            levels.append(float(raw))
        except ValueError:
            continue
    return levels


def carries_cut_mark(media: Path | str, runner: CommandRunner = run_command) -> bool:
    """Whether this file is one this product cut, by the mark it left in it."""
    tags = probe_value(media, ["-show_entries", "format_tags=comment"], runner=runner)
    return CUT_FILE_MARK in (tags or "")


def suggest_noise_db(
    media: Path | str,
    *,
    default: float = DEFAULT_NOISE_DB,
    runner: CommandRunner = run_command,
) -> tuple[float, dict[str, Any]]:
    """A silence threshold that suits this material, and how it was chosen.

    The default is an absolute number, which is only meaningful for material
    recorded at a normal level. A faint room sits below it entirely, so the
    detector reads speech as silence — and every count still looks reasonable,
    which is why this is measured rather than left to the caller's judgement.

    Returns the threshold and a record of the decision, so a result can say why it
    used a number the user did not choose.
    """
    mean = overall_mean_volume_db(media, runner=runner)
    profile = loudness_range_db(media, runner=runner)
    floor, speech = profile if profile else (None, None)
    if floor is not None and speech is not None:
        # The threshold has to sit above this file's gaps and below its speech. Both
        # bounds are measured from the file, and the default is kept whenever it
        # already falls between them — it was validated on 34.3 hours of material and
        # should not move for the sake of moving.
        low = round(floor + QUIET_FLOOR_MARGIN_DB, 1)
        high = round(speech - MIN_SPEECH_HEADROOM_DB, 1)
        if low > high:
            # The two ends are closer together than a threshold needs. Measured on
            # five conference recordings whose speech sat at -29 to -32dB with gaps
            # only 7.5-16.4dB below it: no threshold removed more than 0.2%. The
            # midpoint was tried and removed 0.0-0.2% — not a better answer, just a
            # busier one. So the bound that protects speech wins, the cut comes back
            # nearly empty, and build_cut_plan's existing warning says so rather than
            # a number pretending the material was cuttable.
            return high, {
                "measured": True,
                "mean_volume_db": None if mean is None else round(mean, 1),
                "speech_level_db": speech,
                "quiet_floor_db": floor,
                "noise_db": high,
                "default_noise_db": default,
                "adapted": True,
                "separable": False,
                "reason": (
                    f"这份材料的气口（{floor:.1f}dB）和说话声（{speech:.1f}dB）只差 "
                    f"{speech - floor:.1f}dB，中间放不下一条干净的分界线。已把阈值压到 "
                    f"{high:g}dB 以免切进说话声——大概率剪不出什么，这是材料本身的限制。"
                ),
            }
        chosen = min(max(default, low), high)
        if chosen == default:
            return default, {
                "measured": True,
                "mean_volume_db": None if mean is None else round(mean, 1),
                "speech_level_db": speech,
                "quiet_floor_db": floor,
                "noise_db": default,
                "adapted": False,
            }
        lifted = chosen > default
        return chosen, {
            "measured": True,
            "mean_volume_db": None if mean is None else round(mean, 1),
            "speech_level_db": speech,
            "quiet_floor_db": floor,
            "noise_db": chosen,
            "default_noise_db": default,
            "adapted": True,
            "reason": (
                (f"这份材料整体音量正常，但安静的地方只到 {floor:.1f}dB，"
                 f"默认阈值 {default:g}dB 切不进去（多半是做过降噪或音量增强，气口被抬起来了）。"
                 f"已按它自己的安静段把阈值上调到 {chosen:g}dB。")
                if lifted else
                (f"这份材料的说话声只有 {speech:.1f}dB，默认阈值 {default:g}dB 会切进说话声。"
                 f"已按它自己的音量把阈值下调到 {chosen:g}dB。")
            ),
        }
    if speech is not None and speech - default < MIN_SPEECH_HEADROOM_DB:
        # The speech itself is at or under the threshold, so the default would cut
        # words. Checked first because it is the more damaging of the two failures:
        # this one removes speech, the other merely removes nothing.
        basis = mean if mean is not None else speech
        adapted = max(MIN_ADAPTED_NOISE_DB, round(basis - FAINT_THRESHOLD_MARGIN_DB, 1))
        return adapted, {
            "measured": True,
            "mean_volume_db": None if mean is None else round(mean, 1),
            "speech_level_db": speech,
            "quiet_floor_db": floor,
            "noise_db": adapted,
            "default_noise_db": default,
            "adapted": True,
            "reason": (
                f"这份材料的说话声只有 {speech:.1f}dB，离默认阈值 {default:g}dB 不到 "
                f"{MIN_SPEECH_HEADROOM_DB:g}dB，用默认值会把说话声当成静音。"
                f"已按材料自身音量把阈值下调到 {adapted:g}dB。"
            ),
        }
    # The speech is safely above the threshold. The other way the fixed number fails
    # is a normal level with lifted gaps, and only the quiet end shows that.
    if floor is not None:
        lifted = round(floor + QUIET_FLOOR_MARGIN_DB, 1)
        if lifted > default:
            return lifted, {
                "measured": True,
                "mean_volume_db": None if mean is None else round(mean, 1),
                "speech_level_db": speech,
                "quiet_floor_db": floor,
                "noise_db": lifted,
                "default_noise_db": default,
                "adapted": True,
                "reason": (
                    f"这份材料整体音量正常，但安静的地方只到 {floor:.1f}dB，"
                    f"默认阈值 {default:g}dB 切不进去（多半是做过降噪或音量增强，气口被抬起来了）。"
                    f"已按它自己的安静段把阈值上调到 {lifted:g}dB。"
                ),
            }
    if profile is None and mean is not None and mean < FAINT_MEAN_VOLUME_DB:
        # The distribution could not be read, so the average is all there is. Kept as
        # a fallback rather than deleted: a file this measurement cannot open is
        # exactly the file most likely to need the adjustment.
        adapted = max(MIN_ADAPTED_NOISE_DB, round(mean - FAINT_THRESHOLD_MARGIN_DB, 1))
        return adapted, {
            "measured": True,
            "mean_volume_db": round(mean, 1),
            "noise_db": adapted,
            "default_noise_db": default,
            "adapted": True,
            "reason": (
                f"这份材料整体只有 {mean:.1f}dB，比默认阈值 {default:g}dB 还低，"
                f"用默认值会把说话声当成静音。已按材料自身音量把阈值下调到 {adapted:g}dB。"
            ),
        }
    if mean is None and profile is None:
        return default, {"measured": False, "noise_db": default, "adapted": False}
    return default, {
        "measured": True,
        "mean_volume_db": None if mean is None else round(mean, 1),
        "speech_level_db": speech,
        "quiet_floor_db": floor,
        "noise_db": default,
        "adapted": False,
    }


def plan_silence_cuts(
    media: Path | str,
    *,
    noise_db: float | None = None,
    min_silence_seconds: float = DEFAULT_MIN_SILENCE_SECONDS,
    padding_seconds: float = DEFAULT_PADDING_SECONDS,
    check_separation: bool = True,
    runner: CommandRunner = run_command,
) -> CutPlan:
    """Probe, detect, and plan in one call. No file is written.

    `noise_db=None` means "measure it": which threshold suits this material is a
    question about the material, so it is answered here rather than by whichever
    caller happens to be in front. An explicit number is the caller's judgement
    and is used exactly as given.

    This default used to be the constant, with the measurement living one layer up
    in the job pipeline. Every caller that did not go through that pipeline — a
    script, a test, a batch entry, the Agent API — silently got the constant, and
    on material with lifted gaps that means cutting 0.0% and reporting success.
    """
    source = Path(media)
    if not source.is_file():
        raise SilenceCutError(f"source media not found: {source}")
    threshold_choice: dict[str, Any] = {}
    if noise_db is None:
        noise_db, threshold_choice = suggest_noise_db(source, runner=runner)
    duration = media_duration_seconds(source, runner=runner)
    if duration <= 0:
        raise SilenceCutError(f"could not read a duration from {source.name}")
    silences = detect_silences(
        source,
        noise_db=noise_db,
        min_silence_seconds=min_silence_seconds,
        runner=runner,
    )
    plan = build_cut_plan(
        duration,
        silences,
        noise_db=noise_db,
        min_silence_seconds=min_silence_seconds,
        padding_seconds=padding_seconds,
    )
    plan.threshold_choice = threshold_choice
    if check_separation and plan.cuts:
        plan.level_separation = measure_level_separation(source, plan, runner=runner)
        measurement = plan.level_separation
        if measurement.get("measured") and not measurement["separated"]:
            plan.warnings.append(
                f"抽查的 {measurement['sampled_cuts']} 处里有 {measurement['narrow_cut_count']} 处，"
                f"剪掉的部分只比紧邻的声音低不到 {MIN_LEVEL_SEPARATION_DB:g}dB"
                f"（最窄 {measurement['narrowest_db']:.1f}dB）："
                f"那几段录得很轻，{noise_db:g}dB 在那里分不开说话和空白。"
                "先听这几处，或把「最短静音时长」调大只剪长空白"
            )
    return plan


def _parse_bitrate_bps(value: str | None) -> float:
    """Read an ffmpeg bitrate argument (`128k`, `1.5M`, `750000`) as bits per second."""
    text = str(value or "").strip().lower()
    multiplier = 1.0
    if text.endswith("k"):
        multiplier, text = 1_000.0, text[:-1]
    elif text.endswith("m"):
        multiplier, text = 1_000_000.0, text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return 0.0


def _option_value(args: Sequence[str], name: str) -> str | None:
    for index in range(len(args) - 1):
        if args[index] == name:
            return args[index + 1]
    return None


def _with_option(args: Sequence[str], name: str, value: str) -> list[str]:
    replaced = list(args)
    for index in range(len(replaced) - 1):
        if replaced[index] == name:
            replaced[index + 1] = value
            return replaced
    return replaced + [name, value]


def bitrate_ceiling_bps(
    media: Path | str,
    *,
    audio_bitrate_bps: float = 0.0,
    runner: CommandRunner = run_command,
) -> float:
    """What the source spends on picture per second. 0 when nothing can be measured.

    Prefers the video stream's own figure and falls back to the container total
    minus whatever the audio encoder is about to be told to write, because a
    container that reports only a total is the common case in Matroska.
    """
    video = stream_bitrate_bps(media, "v:0", runner=runner)
    if video > 0:
        return video if video >= MIN_TRUSTED_VIDEO_BITRATE else 0.0
    total = container_bitrate_bps(media, runner=runner)
    if total <= 0:
        return 0.0
    ceiling = max(0.0, total - max(0.0, audio_bitrate_bps))
    return ceiling if ceiling >= MIN_TRUSTED_VIDEO_BITRATE else 0.0


def _bitrate_ceiling_args(ceiling_bps: float) -> list[str]:
    """Constrained-quality settings: keep `-crf` as the target, cap the result.

    x264 reads `-crf` with `-maxrate/-bufsize` as quality-first-within-a-budget,
    so a source that never needed the budget is encoded exactly as it was before
    this existed.

    The buffer is one second of the ceiling, and the size is not cosmetic: the
    average may exceed the ceiling by roughly `bufsize / duration`, so a buffer of
    two seconds is nothing across a lecture and a third of the budget across a
    six-second clip. Measured on a nine-second test clip capped at 208 kbps: a
    two-second buffer came back at 277 kbps, over its source.
    """
    if ceiling_bps <= 0:
        return []
    ceiling = ceiling_bps * BITRATE_CEILING_MARGIN
    return [
        "-maxrate:v", f"{int(ceiling)}",
        "-bufsize:v", f"{int(ceiling)}",
    ]


# Sample rates AAC encodes without resampling. Anything else keeps the fixed
# rate rather than handing ffmpeg one it will refuse.
SUPPORTED_SAMPLE_RATES = frozenset({8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000, 64000, 88200, 96000})


def _with_source_audio_shape(
    args: Sequence[str],
    media: Path | str,
    *,
    runner: CommandRunner = run_command,
) -> list[str]:
    """Follow the recording's channels and sample rate instead of overriding them.

    A lecture recorded in one channel at 32kHz was coming back as two channels at
    48kHz: a second channel invented and the rest resampled upward, neither of
    which adds anything that was recorded. The audio-only path already left these
    alone and said why; this is the same rule reaching the video path.

    Only the shapes AAC can write are followed, and only downward in channel
    count — an unreadable or unusual reading keeps the fixed settings.
    """
    followed = list(args)
    channels = int(probe_float(media, ["-select_streams", "a:0", "-show_entries", "stream=channels"], runner=runner))
    if 1 <= channels <= 2:
        followed = _with_option(followed, "-ac", str(channels))
    sample_rate = int(probe_float(media, ["-select_streams", "a:0", "-show_entries", "stream=sample_rate"], runner=runner))
    if sample_rate in SUPPORTED_SAMPLE_RATES:
        followed = _with_option(followed, "-ar", str(sample_rate))
    return followed


def hardware_encode_enabled() -> bool:
    """Whether to hand the re-encode to this Mac's media engine.

    Off by default, and deliberately an opt-in rather than a platform check: this
    module is shared with the hosted edition, and an encoder swap is not something
    a shared module should decide for a caller who never asked.

    Measured on 2026-08-23, 60 seconds of a 2560x1440 screen recording under
    identical load: libx264 at `-preset veryfast -crf 20` took 94 seconds at
    700-870% CPU; `h264_videotoolbox` took 28 seconds at almost none.
    """
    return (os.environ.get("FLUENTFLOW_DEBREATH_HARDWARE_ENCODE") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _with_hardware_video_encoder(
    args: Sequence[str],
    ceiling_bps: float,
    *,
    fps: float = DEFAULT_RENDER_FPS,
) -> list[str]:
    """Swap the software video encoder for the media engine, when asked and safe.

    The media engine has no `-crf`: quality-targeted encoding is a software idea,
    and asking videotoolbox for it silently gets a default bitrate instead. A
    first attempt at 1200k on a source carrying 400 produced a file five times
    larger than the software one — the encoder spends every bit it is given, on
    detail the recording never had. So the target is the same measurement the
    software path caps with: whatever the recording itself carried.

    No trusted measurement means no hardware encode. Guessing a bitrate for a file
    whose own rate could not be read is how a lecture comes back unreadable, and
    on a screen recording those pictures are what the note is written from.

    The keyframe interval is set rather than left to the encoder for the same
    reason the bitrate is: videotoolbox's default spends the whole ceiling on
    keyframes and delivers a file whose text cannot be read. See
    ``HARDWARE_KEYFRAME_SECONDS``.
    """
    if not hardware_encode_enabled() or ceiling_bps <= 0:
        return list(args)
    software = {"-c:v", "-preset", "-crf"}
    kept: list[str] = []
    index = 0
    while index < len(args):
        if args[index] in software:
            index += 2
            continue
        kept.append(args[index])
        index += 1
    target = int(ceiling_bps * BITRATE_CEILING_MARGIN)
    rate = float(fps) if fps and float(fps) > 0 else float(DEFAULT_RENDER_FPS)
    keyframe_interval = max(1, round(rate * HARDWARE_KEYFRAME_SECONDS))
    return [
        "-c:v", "h264_videotoolbox",
        "-b:v", str(target),
        "-g", str(keyframe_interval),
        *kept,
    ]


def resolve_scale_width(
    media: Path | str,
    scale_width: int | None,
    *,
    runner: CommandRunner = run_command,
) -> int | None:
    """The width to scale to, or None to leave the picture the size it is.

    Downscaling is the other half of not writing a file bigger than the one it
    came from, and it is the larger half: a 2940x1718 60fps screen recording went
    from 726MB to 66MB at 1470 wide, with the chat text in it still readable —
    checked by pulling frames out of the result, not assumed. Bitrate alone cannot
    do that, because the pixels are really there.

    Refuses to upscale. Spending bits inventing detail the source never had is the
    same defect as encoding above its bitrate, wearing a different hat.
    """
    if not scale_width or int(scale_width) <= 0:
        return None
    target = int(scale_width) - int(scale_width) % 2  # h264 needs even dimensions
    if target < 2:
        return None
    source_width = video_width(media, runner=runner)
    if source_width and target >= source_width:
        return None
    return target


def normalize_to_constant_frame_rate(
    source: Path | str,
    output: Path | str,
    *,
    fps: int = DEFAULT_RENDER_FPS,
    scale_width: int | None = None,
    runner: CommandRunner = run_command,
) -> Path:
    """Re-encode to strictly constant frame rate, then prove it took.

    Downscaling belongs here rather than in the cutting pass: every later batch
    then reads and writes the smaller picture, so the resize is paid once instead
    of once per part.
    """
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    filters = [f"fps={fps}"]
    target_width = resolve_scale_width(source, scale_width, runner=runner)
    if target_width:
        filters.append(f"scale={target_width}:-2")
    encode = _with_source_audio_shape(NORMALIZE_ENCODE_ARGS, source, runner=runner)
    ceiling = bitrate_ceiling_bps(
        source,
        audio_bitrate_bps=_parse_bitrate_bps(_option_value(encode, "-b:a")),
        runner=runner,
    )
    headroom = ceiling * NORMALIZE_BITRATE_HEADROOM
    encode = _with_hardware_video_encoder(encode, headroom, fps=fps) + _bitrate_ceiling_args(headroom)
    result = runner([
        _ffmpeg_path(), "-hide_banner", "-loglevel", "error",
        "-i", str(source), "-vf", ",".join(filters),
        *encode,
        "-y", str(out),
    ])
    if result.returncode != 0 or not out.is_file():
        raise SilenceCutError(f"normalize failed: {(result.stderr or '')[-300:]}")
    rate = probe_value(out, ["-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate"], runner=runner)
    if rate != f"{fps}/1":
        raise SilenceCutError(f"normalize did not produce constant {fps}fps: got {rate or 'nothing'}")
    return out


def _select_expression(batch: Sequence[TimeRange], offset: float) -> str:
    return "+".join(
        f"between(t,{keep.start - offset:.3f},{keep.end - offset:.3f})" for keep in batch
    )


def default_encode_args(output: Path | str, *, audio_only: bool) -> tuple[str, ...]:
    """Encoder settings that suit the container being written.

    Frame-accurate cuts cannot be made by stream copy, so the output is always
    re-encoded; the only question is into what. Writing AAC into an .mp3 file
    fails outright, which is why this is picked from the extension rather than
    fixed.
    """
    if not audio_only:
        return DEFAULT_ENCODE_ARGS
    if Path(output).suffix.lower() == ".mp3":
        return MP3_ENCODE_ARGS
    return DEFAULT_AUDIO_ENCODE_ARGS


def encode_args_for_source(
    source: Path | str,
    output: Path | str,
    *,
    audio_only: bool,
    fps: float | None = None,
    runner: CommandRunner = run_command,
) -> tuple[str, ...]:
    """Encoder settings for this container, held under what the source itself spent.

    Re-encoding is not restoration. A source that was already compressed hard has
    no detail left for a higher bitrate to recover, so a fixed quality target just
    spends more bits describing the same artefacts. Measured on a 1920x1080 meeting
    recording carrying 752 kbps: the fixed `-crf 20` came back at over 957 kbps, so
    removing 13.6% of the runtime still produced a file *bigger per second* than the
    one it was cut from — the de-breathed copy was larger than the original. Four of
    five recordings measured (121, 637, 915, 1115 kbps) sit in that range; only the
    fifth (6000 kbps) is what the fixed default was chosen for, and it is unaffected
    because it never reaches its own ceiling.

    Nothing measurable means nothing is capped: an unreadable bitrate leaves the
    previous behaviour exactly as it was.

    ``fps`` is the rate the render is about to write at, which the hardware path
    needs to space its keyframes. A caller that has already settled on one passes
    it rather than paying for the same probe twice.
    """
    args = list(default_encode_args(output, audio_only=audio_only))
    audio_target = _parse_bitrate_bps(_option_value(args, "-b:a"))
    source_audio = stream_bitrate_bps(source, "a:0", runner=runner)
    if 0 < source_audio < audio_target:
        # Same argument, applied to sound: 128k AAC cannot put back what a 64k
        # recording never carried, it only doubles the bytes.
        args = _with_option(args, "-b:a", f"{int(source_audio)}")
        audio_target = source_audio
    if audio_only:
        return tuple(args)
    # Channels and sample rate are the same argument as the bitrate: a mono 32kHz
    # recording should not come back as stereo 48kHz.
    args = _with_source_audio_shape(args, source, runner=runner)
    ceiling = bitrate_ceiling_bps(source, audio_bitrate_bps=audio_target, runner=runner)
    rate = fps if fps and float(fps) > 0 else source_frame_rate(source, runner=runner)
    return tuple(
        _with_hardware_video_encoder(args, ceiling, fps=rate or DEFAULT_RENDER_FPS)
        + _bitrate_ceiling_args(ceiling)
    )


def _render_batch(
    media: Path,
    batch: Sequence[TimeRange],
    part: Path,
    *,
    fps: int,
    encode_args: Sequence[str],
    audio_only: bool,
    scale_width: int | None = None,
    runner: CommandRunner,
) -> Path:
    # Seeking to just before the batch keeps the filter reading a short window
    # instead of decoding from the top of the file for every batch.
    offset = max(0.0, batch[0].start - 0.5)
    span = batch[-1].end - offset + 0.5
    expression = _select_expression(batch, offset)
    command = [
        _ffmpeg_path(), "-hide_banner", "-loglevel", "error",
        "-ss", f"{offset:.3f}", "-t", f"{span:.3f}", "-i", str(media),
    ]
    if audio_only:
        command += ["-vn"]
    else:
        # The fps -> select -> setpts chain is the one that survived the
        # timestamp-inflation bug; a resize is appended after it rather than
        # woven into it, and scaling only the frames that survived the select is
        # also the cheaper order.
        chain = f"fps={fps},select='{expression}',setpts=N/{fps}/TB"
        if scale_width:
            chain += f",scale={scale_width}:-2"
        command += ["-vf", chain]
    command += [
        "-af", f"aselect='{expression}',asetpts=N/SR/TB",
        *encode_args, "-y", str(part),
    ]
    result = runner(command)
    if result.returncode != 0 or not part.is_file():
        raise SilenceCutError(f"render batch failed: {(result.stderr or '')[-300:]}")
    # The guard that would have caught the inflated-duration bug on the spot: a
    # part missing a stream silently destroys the concatenated timeline.
    expected_streams = "1" if audio_only else "2"
    streams = probe_value(part, ["-show_entries", "format=nb_streams"], runner=runner)
    if streams != expected_streams:
        raise SilenceCutError(
            f"render batch produced {streams or '0'} stream(s), expected {expected_streams} — range "
            f"{batch[0].start:.3f}-{batch[-1].end:.3f} probably falls outside the media"
        )
    return part


def render_keeps(
    media: Path | str,
    keeps: Sequence[TimeRange],
    output: Path | str,
    *,
    fps: int = DEFAULT_RENDER_FPS,
    ranges_per_batch: int = DEFAULT_RANGES_PER_BATCH,
    workers: int = DEFAULT_RENDER_WORKERS,
    encode_args: Sequence[str] | None = None,
    audio_only: bool = False,
    scale_width: int | None = None,
    ceiling_source: Path | str | None = None,
    work_dir: Path | str | None = None,
    runner: CommandRunner = run_command,
) -> int:
    """Render the kept ranges in batches and concatenate them. Returns batch count.

    ``ceiling_source`` is the file the per-part bitrate ceilings are measured on.
    It exists because the frames being read are usually not the frames that carry
    the answer: the cutting pass reads a normalized intermediate, and that
    intermediate has already been re-encoded once. Left unset, the media being
    rendered is measured — which is right when it is also the original.
    """
    if not keeps:
        raise SilenceCutError("nothing to render: the plan keeps no ranges")
    source = Path(media)
    out = Path(output)
    derived_encode_args = encode_args is None
    if encode_args is None:
        encode_args = encode_args_for_source(
            source, out, audio_only=audio_only, fps=fps, runner=runner,
        )
    # The whole-file ceiling is the right average and the wrong budget for any one
    # stretch of a recording whose content changes. Measured per second, meeting 15
    # of one training-camp archive carries 14 kbps of black screen for eighty-five
    # minutes and 200-278 kbps of document for the last twenty; its average is
    # 67 kbps, so every readable minute was encoded at a quarter of what the source
    # itself had spent, and the text came back unreadable. The parts are already
    # rendered one ffmpeg call at a time, so each one can be held to what the source
    # spent on the frames that part is keeping.
    #
    # A caller that both chose the settings and named nothing to measure is taken
    # at its word and gets them unchanged; naming a ceiling_source is how the
    # pipeline says "these settings are mine, the measurement is that file's".
    second_bytes: tuple[int, ...] = ()
    measured = Path(ceiling_source) if ceiling_source else (source if derived_encode_args else None)
    if measured is not None and not audio_only and "-maxrate:v" in encode_args:
        second_bytes = video_second_bytes(measured, runner=runner)
    target_width = None if audio_only else resolve_scale_width(source, scale_width, runner=runner)
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(work_dir) if work_dir else out.parent / f".render-{uuid.uuid4().hex}"
    stage.mkdir(parents=True, exist_ok=True)
    batches = [
        list(keeps[index:index + ranges_per_batch])
        for index in range(0, len(keeps), max(1, ranges_per_batch))
    ]
    try:
        def one(job: tuple[int, list[TimeRange]]) -> Path:
            index, batch = job
            args = encode_args
            if second_bytes:
                spent = ranges_bitrate_bps(second_bytes, batch)
                # Below the trust floor the reading is treated as a bad probe, the
                # same judgement the whole-file ceiling makes; the batch keeps the
                # file-wide number rather than being held to a few kbps.
                if spent >= MIN_TRUSTED_VIDEO_BITRATE:
                    args = _with_ceiling_values(encode_args, spent)
            return _render_batch(
                source, batch, stage / f"part{index:04d}{out.suffix or '.mp4'}",
                fps=fps, encode_args=args, audio_only=audio_only,
                scale_width=target_width, runner=runner,
            )

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            parts = list(pool.map(one, enumerate(batches)))
        listing = stage / "parts.txt"
        listing.write_text("".join(f"file '{part}'\n" for part in parts), encoding="utf-8")
        concat = [
            _ffmpeg_path(), "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy",
            # A mark on our own output, so a file that comes back through any entry
            # is recognisable as already cut even after it has been renamed or moved.
            # Verified to survive `-c copy` and to read back through ffprobe.
            "-metadata", f"comment={CUT_FILE_MARK}",
        ]
        # faststart moves the mp4 index to the front so playback can begin before
        # the file is fully read. It is an mp4-family flag; other containers
        # reject it.
        if out.suffix.lower() in {".mp4", ".m4a", ".mov", ".m4v"}:
            concat += ["-movflags", "+faststart"]
        result = runner([*concat, "-y", str(out)])
        if result.returncode != 0 or not out.is_file():
            raise SilenceCutError(f"concat failed: {(result.stderr or '')[-300:]}")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return len(batches)


def verify_render(
    output: Path | str,
    *,
    expected_seconds: float,
    source_skew_seconds: float = 0.0,
    fps: int = DEFAULT_RENDER_FPS,
    batches: int = 0,
    audio_only: bool = False,
    runner: CommandRunner = run_command,
) -> RenderReport:
    """Measure the render against the plan. Reports; never deletes.

    Every threshold is relative to the input, not an absolute number picked in
    advance. Two of thirteen recordings shipped with 1.09s of audio/video skew of
    their own, so demanding 0.5s of the output demanded better than the source and
    rejected faithful renders. Concatenation loses about a frame of metadata per
    part, so 198 parts drift several seconds — harmless, and an absolute limit
    rejected a 107ms-skew render for it. The failure actually worth catching is
    the slow-motion case, where the frames cover a fraction of the container.
    """
    out = Path(output)
    duration = probe_float(out, ["-show_entries", "format=duration"], runner=runner)
    video = probe_float(out, ["-select_streams", "v:0", "-show_entries", "stream=duration"], runner=runner)
    audio = probe_float(out, ["-select_streams", "a:0", "-show_entries", "stream=duration"], runner=runner)
    raw_frames = probe_value(out, ["-select_streams", "v:0", "-show_entries", "stream=nb_frames"], runner=runner)
    try:
        frames = int(raw_frames.splitlines()[0]) if raw_frames.strip() else 0
    except (ValueError, IndexError):
        frames = 0
    checks = {
        "duration_matches_plan": abs(duration - expected_seconds) < max(3.0, expected_seconds * 0.01),
    }
    # An audio-only render has no picture to fall out of step with and no frames
    # to count. Reporting those two as failures would be reporting the absence of
    # a problem that cannot occur.
    if not audio_only:
        checks["audio_video_in_sync"] = abs(audio - video) <= max(0.5, source_skew_seconds + 0.3)
        checks["frame_count_matches_duration"] = (
            abs(frames / fps / video - 1.0) < 0.05 if frames and video > 0 else False
        )
    return RenderReport(
        output_path=out,
        expected_seconds=expected_seconds,
        actual_seconds=duration,
        video_seconds=video,
        audio_seconds=audio,
        frame_count=frames,
        batches=batches,
        checks=checks,
        source_skew_seconds=source_skew_seconds,
        audio_only=audio_only,
    )


def render_cut_plan(
    media: Path | str,
    plan: CutPlan,
    output: Path | str,
    *,
    fps: int | None = None,
    ranges_per_batch: int = DEFAULT_RANGES_PER_BATCH,
    workers: int = DEFAULT_RENDER_WORKERS,
    encode_args: Sequence[str] | None = None,
    scale_width: int | None = None,
    work_dir: Path | str | None = None,
    runner: CommandRunner = run_command,
) -> RenderReport:
    """Normalize, cut, concatenate, and verify.

    Video takes two passes on purpose. The extra full-file encode costs roughly
    one pass at 8.8x realtime, and buys correctness that cutting in a single pass
    did not deliver at scale; see this module's docstring.

    Audio-only sources skip the normalize pass entirely. There is no frame rate
    to correct and no picture to renumber, so the pass would cost an encode and
    protect against nothing.

    `scale_width` delivers a smaller picture, aspect ratio kept and height rounded
    to an even number. It is the only lever here that reduces a screen recording by
    an order of magnitude rather than a percentage, and it is refused when it would
    upscale. Passing `encode_args` explicitly opts out of the source-derived
    bitrate ceiling: the caller has then said what to spend.

    `fps` defaults to the rate the recording itself holds, for the same reason the
    bitrate does: a fixed rate resamples everything that is not already at it, in
    both directions. A caller that names a rate still wins, and an unreadable one
    falls back to the fixed rate.
    """
    source = Path(media)
    if not source.is_file():
        raise SilenceCutError(f"source media not found: {source}")
    if not plan.keeps:
        raise SilenceCutError("nothing to render: the plan keeps no ranges")
    out = Path(output)
    audio_only = not has_video_stream(source, runner=runner)
    if fps is None:
        fps = (0 if audio_only else source_frame_rate(source, runner=runner)) or DEFAULT_RENDER_FPS
    if encode_args is None:
        # Measured on the file the user handed over, never on the normalized
        # intermediate: that intermediate has already been re-encoded once, and
        # its bitrate is the inflation this ceiling exists to prevent rather than
        # a reading of the source.
        encode_args = encode_args_for_source(
            source, out, audio_only=audio_only, fps=fps, runner=runner,
        )
    stage = Path(work_dir) if work_dir else out.parent / f".debreath-{uuid.uuid4().hex}"
    stage.mkdir(parents=True, exist_ok=True)
    source_skew = 0.0 if audio_only else stream_skew_seconds(source, runner=runner)
    normalized = stage / "normalized.mp4"
    try:
        if audio_only:
            cut_source = source
        else:
            # The resize happens in this pass, so the batches below read frames
            # that are already the delivered size.
            normalize_to_constant_frame_rate(
                source, normalized, fps=fps, scale_width=scale_width, runner=runner,
            )
            cut_source = normalized
        batches = render_keeps(
            cut_source,
            plan.keeps,
            out,
            fps=fps,
            ranges_per_batch=ranges_per_batch,
            workers=workers,
            encode_args=encode_args,
            audio_only=audio_only,
            # Same reason the encode args above are read off the original: the
            # normalized intermediate the batches are cut from has already been
            # re-encoded, so its per-second profile is that pass's output, not a
            # reading of what the recording carried.
            ceiling_source=source,
            work_dir=stage / "parts",
            runner=runner,
        )
    finally:
        normalized.unlink(missing_ok=True)
        shutil.rmtree(stage, ignore_errors=True)
    report = verify_render(
        out,
        expected_seconds=plan.kept_seconds,
        source_skew_seconds=source_skew,
        fps=fps,
        batches=batches,
        audio_only=audio_only,
        runner=runner,
    )
    if not report.ok:
        logger.warning(
            "debreath render failed verification for %s: %s",
            out.name, ", ".join(report.failed_checks),
        )
    return report
