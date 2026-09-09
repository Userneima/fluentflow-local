"""
Measure and correct the muffled far-field phone-recording profile.

Recordings made with a phone left on a table sound muffled not because they are
noisy but because their energy piles up around 100-400 Hz while the 2-4 kHz band
that carries consonants nearly vanishes. Measured on the 2026-08 training-camp
corpus: 100-400 Hz held 82-94% of the energy and 2-4 kHz held 0.25-0.36%, at a
21-31 dB signal-to-noise ratio. The problem is frequency balance, not noise, so
the fix is EQ rather than denoising — an AI denoiser would read the already
faint high end as noise and suppress it further.

On transcription accuracy, measured rather than assumed. Published work showing
enhancement front-ends hurt ASR concerns *neural* denoisers, which introduce
artifacts and shift the spectrum away from what the recognizer trained on; it
does not transfer to static EQ, and it was wrong to assume it did. Four 90s
windows from four speakers, transcribed by whisper-medium and large-v3-turbo on
both takes: the two takes agree 85-94% of the time, and no consistent direction
emerges — the corrected take scored closer to a neutral reference on two clips,
further on one, level on one. Spot-checking the differences favours the
corrected take on consonant-dense words: "IG ID / EVN ID" became "Agent ID /
Event ID", and "每个样的处" became "美妙之处". So enhancement is not a hazard to
transcription, and may help on technical vocabulary; the effect is smaller than
the models' own run-to-run spread, which is why the transcription path was left
alone rather than switched.

**This module deliberately offers no `should_enhance()` predicate.** Two
candidate thresholds were measured against both corpora (phone recordings vs
professionally mixed video) and both overlapped heavily: low-vs-high band gap
9.9-21.5 dB for phone against 6.4-15.8 dB for produced video, and normalized
presence deficit -10.6 to -19.0 dB against -7.7 to -15.3 dB. Whether a recording
"sounds muffled" is a listening judgement, and no single spectral threshold
separates the two populations. So this module reports numbers and applies the
filter on request; deciding which take to keep belongs to whoever is listening.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Voice-clarity correction, validated by ear on the 2026-08 training-camp corpus
# and applied to all ten recordings: drop the table rumble, pull down the boxy
# 180/320 Hz region, lift the 2.6/4.5 kHz consonant range, then level the result.
#
# One caveat per stage:
#   - `loudnorm` must be followed by an explicit output sample rate. It reports
#     192 kHz as its preferred rate, and ffmpeg will happily resample the whole
#     file to it, quadrupling the output for no benefit.
#   - The lift is intentionally gentle. Measured across the corpus it landed
#     every file between -6.6 and -13.3 dB of presence deficit, self-scaling
#     because loudnorm flattens the difference: the muffled takes gained 8 dB
#     and the already-clear ones only 3 dB.
PRESENCE_EQ_FILTERS = (
    "highpass=f=80",
    "equalizer=f=180:t=q:w=1.2:g=-7",
    "equalizer=f=320:t=q:w=1.4:g=-5",
    "equalizer=f=2600:t=q:w=1.0:g=7",
    "equalizer=f=4500:t=q:w=1.2:g=5",
    "loudnorm=I=-16:TP=-1.5:LRA=11",
)

# The consonant band. Speech intelligibility lives here; "muffled" means this
# band is weak relative to the rest of the signal.
PRESENCE_CENTER_HZ = 3000
PRESENCE_WIDTH_HZ = 2000

DEFAULT_SAMPLE_SECONDS = 90.0
ENHANCED_SAMPLE_RATE = 48_000

_RMS_LINE = re.compile(r"RMS level dB:\s*(-?(?:\d+(?:\.\d+)?|inf|nan))", re.IGNORECASE)


@dataclass(frozen=True)
class PresenceReading:
    """How much quieter the consonant band is than the signal as a whole."""

    full_band_dbfs: float
    presence_dbfs: float
    sampled_seconds: float
    offset_seconds: float

    @property
    def deficit_db(self) -> float:
        """Presence relative to the full band. More negative reads as muffled.

        Normalizing against the full band removes overall loudness, so this is
        comparable across recordings. It is *not* comparable across speakers:
        a naturally deep voice reads lower without being badly recorded, which
        is why this number informs a judgement rather than making one.
        """
        return self.presence_dbfs - self.full_band_dbfs

    def as_dict(self) -> dict[str, float]:
        return {
            "full_band_dbfs": round(self.full_band_dbfs, 2),
            "presence_dbfs": round(self.presence_dbfs, 2),
            "presence_deficit_db": round(self.deficit_db, 2),
            "sampled_seconds": round(self.sampled_seconds, 2),
            "offset_seconds": round(self.offset_seconds, 2),
        }


def _require(binary: str) -> str:
    found = shutil.which(binary)
    if not found:
        raise RuntimeError(f"{binary} not found on PATH; it is required for audio processing")
    return found


def _media_duration_seconds(path: Path) -> float | None:
    try:
        completed = subprocess.run(
            [
                _require("ffprobe"),
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1",
                str(path),
            ],
            check=True, capture_output=True, text=True,
        )
    except (subprocess.CalledProcessError, RuntimeError):
        return None
    try:
        duration = float(completed.stdout.strip())
    except ValueError:
        return None
    return duration if duration > 0 else None


def _band_rms_dbfs(
    ffmpeg: str,
    path: Path,
    *,
    offset_seconds: float,
    sample_seconds: float,
    bandpass: str | None,
) -> float:
    chain = ["aformat=channel_layouts=mono"]
    if bandpass:
        chain.append(bandpass)
    chain.append("astats=metadata=1:reset=0")

    completed = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-nostdin",
            "-ss", f"{offset_seconds:.3f}",
            "-t", f"{sample_seconds:.3f}",
            "-i", str(path),
            "-af", ",".join(chain),
            "-f", "null", "-",
        ],
        check=True, capture_output=True, text=True,
    )

    # astats prints a per-channel block and then an "Overall" block; the mono
    # aformat above means they agree, so the last RMS line is the one to take.
    matches = _RMS_LINE.findall(completed.stderr)
    if not matches:
        raise RuntimeError("无法测量音频频段能量，请重新提交媒体文件。")
    value = matches[-1].lower()
    if value in {"-inf", "inf", "nan"}:
        raise RuntimeError("音频中没有检测到可测量的声音，请确认录制包含人声后重新提交。")
    return float(value)


def measure_presence(
    audio_path: str | Path,
    *,
    offset_seconds: float | None = None,
    sample_seconds: float = DEFAULT_SAMPLE_SECONDS,
) -> PresenceReading:
    """Measure the consonant band against the full band on a sampled window.

    Sampling a window rather than the whole file keeps this cheap on hour-long
    lectures. When no offset is given it reads from the middle, because the
    opening of a recording is usually room noise and setup chatter.
    """
    path = Path(audio_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Audio file not found: {path}")

    ffmpeg = _require("ffmpeg")
    duration = _media_duration_seconds(path)
    if duration is not None:
        sample_seconds = min(sample_seconds, duration)
        if offset_seconds is None:
            offset_seconds = max(0.0, (duration - sample_seconds) / 2)
    if offset_seconds is None:
        offset_seconds = 0.0

    full_band = _band_rms_dbfs(
        ffmpeg, path,
        offset_seconds=offset_seconds, sample_seconds=sample_seconds, bandpass=None,
    )
    presence = _band_rms_dbfs(
        ffmpeg, path,
        offset_seconds=offset_seconds, sample_seconds=sample_seconds,
        bandpass=(
            f"bandpass=f={PRESENCE_CENTER_HZ}"
            f":width_type=h:w={PRESENCE_WIDTH_HZ}"
        ),
    )
    return PresenceReading(
        full_band_dbfs=full_band,
        presence_dbfs=presence,
        sampled_seconds=sample_seconds,
        offset_seconds=offset_seconds,
    )


def stt_audio_source(
    enhanced_path: Path | None,
    original_path: Path,
) -> tuple[Path, str]:
    """Pick the take the recognizer reads, and the label to record on the result.

    The corrected take when there is one. Measured on four speakers with two
    whisper sizes it transcribes at least as well as the plain take and better on
    consonant-dense words — "IG ID / EVN ID" came back as "Agent ID / Event ID" —
    which is what lifting the consonant band predicts. The effect is smaller than
    the models' own run-to-run spread, so this is a mild preference, not a
    guarantee; the returned label is what makes it auditable per task.

    Correcting is best-effort, so falling back to the original must stay
    possible: clarity is optional, the transcript is not.
    """
    if enhanced_path is not None:
        return enhanced_path, "enhanced"
    return original_path, "plain"


def enhance_voice(
    input_path: str | Path,
    *,
    output_path: str | Path | None = None,
    bitrate: str = "192k",
) -> Path:
    """Write a presence-corrected copy of the audio. The source is never touched.

    One output format on purpose. Producing a recognizer input directly from the
    original — skipping the intermediate's lossy generation — looks strictly
    better and measured worse: on a lecture clip it lost a company name that the
    m4a-derived take recovered. So everything downstream derives from this file,
    and this file is the one that was measured.

    Both takes are meant to be kept: the correction helps a far-field phone
    recording and hurts an already-mixed soundtrack, and since no threshold
    reliably tells those apart (see the module docstring), the listener needs
    the original to fall back to.
    """
    src = Path(input_path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"File not found: {src}")

    if output_path is None:
        out = src.with_name(f"{src.stem}_voice_enhanced.m4a")
    else:
        out = Path(output_path).expanduser().resolve()
    if out == src:
        raise ValueError("enhance_voice refuses to overwrite its own source file")
    out.parent.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            _require("ffmpeg"), "-y", "-nostdin",
            "-i", str(src),
            "-vn",
            "-af", ",".join(PRESENCE_EQ_FILTERS),
            "-ar", str(ENHANCED_SAMPLE_RATE),  # required: see PRESENCE_EQ_FILTERS on loudnorm
            "-ac", "1",
            "-c:a", "aac", "-b:a", bitrate,
            str(out),
        ],
        check=True, capture_output=True,
    )
    return out
