"""Extract candidate frames from video for multimodal note generation."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Finding where the picture changes and reading what it says are two different
# jobs, and they were being done by one ffmpeg pass at full resolution. Detection
# only needs shape, so it runs on a stream that is cheap to decode; the frames
# actually sent are grabbed afterwards at full size, because the whole point is
# that a model can read the text on them.
#
# Measured on a 69-minute lecture: the old single pass was killed by its own 120s
# timeout having reached 10:49 of the file (15.7%), and the frames it had already
# written were discarded with the exception. Detecting on keyframes only, scaled
# to 180 lines, covers the same file in 8 seconds.
ANALYSIS_HEIGHT = 180
SCENE_ANALYSIS_TIMEOUT_SECONDS = 600
FRAME_GRAB_TIMEOUT_SECONDS = 30

# Detection used to be all-or-nothing: any change it reported won, and if it
# reported none the file was sampled evenly instead. That made a bad detection
# pass worse than no detection at all — the 69-minute demo class reported four
# changes, so four looks were all its note ever got, and the sampling that would
# have covered the other 65 minutes never ran because the branch was an either/or.
#
# So the floor is always on: after the reported changes are in, the longest
# unlooked stretches are split until the candidate budget is full. Dense material
# adds nothing here and costs nothing; silent material ends up evenly sampled,
# which is what the old fallback did; and the common middle — a few real changes
# separated by an hour — now gets both. The idea is claude-real-video's
# ``--fps-floor`` (MIT); the shape is different because a fixed frame-per-second
# floor would make us grab hundreds of frames to throw nearly all of them away,
# and grabbing is the expensive half here.
#
# A gap is only worth splitting if both halves are still a real interval apart.
CANDIDATE_MIN_GAP_SECONDS = 2.0

# Where the floor lands is arbitrary by construction, so it is snapped onto the
# nearest moment somebody started speaking. That costs nothing — the same number
# of grabs, moved by a few seconds — and buys a frame the model can tie to a line
# of the transcript it was sent, instead of one taken between two words.
#
# Only the times are used, never what was said. A text model reading the
# transcript to decide where a picture would help is the *other* pipeline
# (`docs/video_keyframe_notes_plan.md`), and it would put this one's frames back
# under the transcript's authority — which is the exact claim this path exists to
# make good on. It would also aim at the wrong places: the frames that earned
# their keep in the Stage 5 notes were a port number, a table of contents, and a
# model name the transcript had *misheard*, none of which the transcript
# announces, and screen demonstrations are frequently silent.
#
# So the snap is bounded: a cue within a few seconds is the same moment of the
# lecture, and beyond that the sample was sitting in silence, where its arbitrary
# time is the honest one. Pulling a frame across a minute of quiet to reach the
# nearest word would empty out exactly the stretches worth looking at.
ANCHOR_SNAP_SECONDS = 5.0

# Both are gap-fillers; only their placement differs. Wherever the difference
# between "detection found this" and "we looked here anyway" matters, it is this
# set that is being asked about.
SAMPLED_SOURCES = frozenset({"floor", "cue"})

# One sensitivity does not fit both a camera pointed at a person and a shared
# document being scrolled. Measured on a 103-minute screen-share lecture: at 0.3
# the detector found ten changes and went 74 minutes without noticing anything,
# because a page of text scrolling is a small-region change; at a sixth of that
# it found 105, with no gap longer than 7.3 minutes. So the configured threshold
# is the preference, not the rule — weaker changes are accepted only when the
# strong ones do not fill the budget.
SCENE_THRESHOLD_DIVISORS = (3.0, 6.0)

# Filling the budget is only worth it with pictures that differ. Escalating on a
# talking-head recording would otherwise buy twenty copies of one webcam shot,
# and every copy is paid for in tokens.
#
# Sameness is measured as a real pixel difference on a 32x32 RGB grid — a pixel
# counts as changed when any channel moves more than DUPLICATE_TOLERANCE, and a
# frame is new when more than DUPLICATE_CHANGED_PERCENT of the grid changed. This
# replaces a 64-bit perceptual hash, for claude-real-video's reason (MIT): a hash
# goes blind on flat colour, and a deck of white slides differing only in their
# text is exactly that — the hash would have called them one picture and deleted
# the ones the note needed. RGB rather than grey, so a red-to-green cut at equal
# brightness is not read as the same frame. The hash is still computed and
# reported, because the note pipeline downstream dedupes on it.
#
# The threshold is NOT that project's default of 8%, which was measured here to be
# far too coarse for a lecture: on 1280x720 slides, one more bullet appearing is
# 3.5% of the grid, so 8% deletes the slide a note was going to cite. Measured on
# generated slides at this grid — jpeg noise 0.00%, a mouse cursor moving 0.10%,
# one more bullet 3.52%, a whole new slide 19%. 1.5% sits fifteen times above the
# noise and twice below the smallest real content change.
#
# What that costs, also measured: a talking head shifting 15px reads as 2.7%, so
# this no longer drops webcam frames that differ without carrying new information.
# That case is bounded by SCREENLESS_FRAME_BUDGET below instead, which is the right
# place for it — the picture genuinely did change, it just was not worth sending.
#
# Both numbers come from generated images, not from the three course recordings,
# and every kept frame reports the distance it survived at (duplicate_distance) so
# they can be reset from real notes.
#
# A new frame is compared against every frame kept so far, not a sliding window:
# a shot that returns after twenty minutes is a picture the model has already been
# sent, and sending it twice is paid for twice.
DUPLICATE_GRID = 32
DUPLICATE_TOLERANCE = 25
DUPLICATE_CHANGED_PERCENT = 1.5
SCENE_OVERSAMPLE = 2

# A camera pointed at a person carries no text, and twenty pictures of it cost
# real money to be told so. Measured across three course recordings: every frame
# of the talking-head meeting scored 3.8–10.4 on edge contrast, while the two
# screen-share recordings scored 34–53, with no overlap — and the meeting's note
# cited none of its frames on either of two runs.
#
# This is an absolute number, which is normally the wrong way to set a threshold,
# and it sits in the middle of a fourfold gap for exactly one corpus recorded on
# one setup. So it never removes the pictures, only most of them: a recording
# that turns out to have one crucial screen moment still gets looked at. Every
# frame reports the value it was judged on, so this can be recalibrated from real
# results instead of guessed at again.
SCREEN_CONTENT_EDGE_CONTRAST = 20.0
SCREENLESS_FRAME_BUDGET = 4


def _ffmpeg_path() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("ffmpeg not found")
    return path


def _ffprobe_path() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise RuntimeError("ffprobe not found")
    return path


def _video_duration_seconds(video_path: str) -> float:
    result = subprocess.run(
        [
            _ffprobe_path(),
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        check=True, capture_output=True, text=True, timeout=15,
    )
    return max(0.0, float((result.stdout or "").strip()))


def _parse_showinfo_timestamps(stderr: str) -> list[float]:
    timestamps: list[float] = []
    for match in re.finditer(r"pts_time:([0-9]+(?:\.[0-9]+)?)", stderr or ""):
        try:
            timestamps.append(float(match.group(1)))
        except ValueError:
            continue
    return timestamps


def _selected_even_indices(total: int, keep: int) -> list[int]:
    if total <= 0 or keep <= 0:
        return []
    if total <= keep:
        return list(range(total))
    if keep == 1:
        return [0]
    return [round(i * (total - 1) / (keep - 1)) for i in range(keep)]


def _frame_quality_metadata(path: Path) -> dict[str, Any]:
    try:
        from PIL import Image, ImageFilter, ImageStat

        with Image.open(path) as image:
            gray = image.convert("L")
            stat = ImageStat.Stat(gray)
            edge_stat = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES))
            small = gray.resize((9, 8))
            pixels = list(small.getdata())
            bits = []
            for row in range(8):
                offset = row * 9
                for col in range(8):
                    bits.append("1" if pixels[offset + col] > pixels[offset + col + 1] else "0")
            visual_hash = f"{int(''.join(bits), 2):016x}"
            contrast = float(stat.stddev[0] or 0)
            edge_contrast = float(edge_stat.stddev[0] or 0)
            return {
                "visual_hash": visual_hash,
                "brightness": round(float(stat.mean[0] or 0), 2),
                "contrast": round(contrast, 2),
                "edge_contrast": round(edge_contrast, 2),
                "low_information": contrast < 2.0 and edge_contrast < 1.0,
            }
    except Exception:
        return {}


def _frame_record(path: Path, timestamp: float, source: str) -> dict[str, Any]:
    return {
        "path": str(path),
        "timestamp_seconds": round(timestamp, 1),
        "source": source,
        **_frame_quality_metadata(path),
    }


def _detect_scene_timestamps(video_path: str, *, threshold: float) -> list[float]:
    """When the picture changes, over the whole file.

    Decodes keyframes only and scales them down before comparing: detection needs
    shape, not detail, and the cost of decoding every frame at full resolution is
    what used to make this pass unfinishable. Writes no images — the timestamps
    are the product, and they come from ffmpeg's own ``showinfo`` rather than
    being spread evenly afterwards, so a frame's reported time is the time it
    actually occurred.
    """
    cmd = [
        _ffmpeg_path(),
        "-skip_frame", "nokey",
        "-i", str(video_path),
        "-an",
        "-vf", f"scale=-1:{ANALYSIS_HEIGHT},select='gt(scene,{threshold})',showinfo",
        "-f", "null",
        "-",
    ]
    completed = subprocess.run(
        cmd, check=False, capture_output=True, text=True,
        timeout=SCENE_ANALYSIS_TIMEOUT_SECONDS,
    )
    timestamps = _parse_showinfo_timestamps(completed.stderr or "")
    if not timestamps and int(getattr(completed, "returncode", 0) or 0) != 0:
        logger.warning(
            "Scene detection failed for %s: %s", video_path,
            ((completed.stderr or "").strip().splitlines() or [""])[-1][:200],
        )
    return timestamps


def _scene_threshold_ladder(threshold: float) -> list[float]:
    steps = [threshold]
    for divisor in SCENE_THRESHOLD_DIVISORS:
        step = round(threshold / divisor, 4)
        if 0 < step < steps[-1]:
            steps.append(step)
    return steps


def _detect_scene_candidates(
    video_path: str, *, threshold: float, wanted: int
) -> tuple[list[float], float]:
    """Change timestamps, relaxing sensitivity only if there are too few.

    Returns the timestamps and the threshold they were found at, so the job can
    record how hard it had to look. Material with plenty of strong changes costs
    exactly one pass, which is the common case.
    """
    best: tuple[list[float], float] = ([], threshold)
    for step in _scene_threshold_ladder(threshold):
        found = _detect_scene_timestamps(video_path, threshold=step)
        if len(found) > len(best[0]):
            best = (found, step)
        if len(found) >= wanted:
            return found, step
    return best


def _pixel_signature(path: Path) -> list[tuple[int, int, int]] | None:
    """A frame reduced to a 16x16 RGB grid, or ``None`` if it cannot be read."""
    try:
        from PIL import Image

        with Image.open(path) as image:
            grid = image.convert("RGB").resize((DUPLICATE_GRID, DUPLICATE_GRID))
            return list(grid.getdata())
    except Exception:
        return None


def _changed_percent(
    left: list[tuple[int, int, int]], right: list[tuple[int, int, int]]
) -> float:
    """How much of the picture actually moved, as a percentage of the grid."""
    if not left or len(left) != len(right):
        return 100.0
    changed = sum(
        max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2])) > DUPLICATE_TOLERANCE
        for a, b in zip(left, right)
    )
    return 100.0 * changed / len(left)


def _drop_near_duplicates(
    records: list[dict[str, Any]], *, changed_percent: float = DUPLICATE_CHANGED_PERCENT
) -> list[dict[str, Any]]:
    """Keep one of each picture. A frame that cannot be read is always kept.

    Each survivor records the distance it was kept at (``duplicate_distance``, the
    percentage of the grid that differed from the nearest picture already kept), so
    a threshold that is dropping real slides can be recalibrated from real notes
    instead of argued about.
    """
    kept: list[dict[str, Any]] = []
    signatures: list[tuple[dict[str, Any], list[tuple[int, int, int]]]] = []
    for record in records:
        signature = _pixel_signature(Path(str(record.get("path"))))
        if signature is None:
            kept.append(record)
            continue
        nearest_distance = 100.0
        nearest_index = -1
        for index, (_incumbent, existing) in enumerate(signatures):
            distance = _changed_percent(signature, existing)
            if distance < nearest_distance:
                nearest_distance, nearest_index = distance, index
        if nearest_index >= 0 and nearest_distance <= changed_percent:
            # The same picture twice. Which copy stays is not arbitrary: a frame
            # detection reported carries a measured time and a threshold, and a
            # gap-filler that happens to sit two seconds earlier does not. Keeping
            # whichever arrived first would quietly relabel found changes as
            # samples, which is the one thing the source field exists to prevent.
            incumbent, _existing = signatures[nearest_index]
            if record.get("source") == "scene" and incumbent.get("source") in SAMPLED_SOURCES:
                Path(str(incumbent.get("path"))).unlink(missing_ok=True)
                record["duplicate_distance"] = incumbent.get("duplicate_distance")
                kept[kept.index(incumbent)] = record
                signatures[nearest_index] = (record, signature)
            else:
                Path(str(record.get("path"))).unlink(missing_ok=True)
            continue
        record["duplicate_distance"] = round(nearest_distance, 2) if nearest_index >= 0 else None
        kept.append(record)
        signatures.append((record, signature))
    # A survivor that replaced an earlier copy of itself lands out of order.
    kept.sort(key=lambda item: float(item.get("timestamp_seconds") or 0.0))
    return kept


def _grab_frame(video_path: str, output: Path, timestamp: float) -> bool:
    """One frame at full resolution, by seeking. Returns whether it landed."""
    cmd = [
        _ffmpeg_path(),
        "-y",
        "-ss", str(timestamp),
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "2",
        str(output),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True,
                       timeout=FRAME_GRAB_TIMEOUT_SECONDS)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return output.is_file()


def _gap_fill_timestamps(known: list[float], duration: float, needed: int) -> list[float]:
    """Split the longest unlooked stretch, ``needed`` times over.

    Called with nothing known this is even sampling; called with a rich detection
    pass it returns nothing; called with the real middle case it puts looks exactly
    where the recording was going unwatched. A stretch too short to hold two frames
    an interval apart is left alone, which is what stops this from filling a
    30-second clip with 40 near-identical grabs. The one exception is a recording
    shorter than that interval with nothing known about it at all: it still gets
    its one look, because a five-second clip nobody looked at is worse than a
    five-second clip looked at once.
    """
    if needed <= 0 or duration <= 0:
        return []
    bounds = [0.0, *sorted(ts for ts in known if 0 <= ts <= duration), duration]
    added: list[float] = []
    for _ in range(needed):
        widest = max(range(len(bounds) - 1), key=lambda i: bounds[i + 1] - bounds[i])
        gap = bounds[widest + 1] - bounds[widest]
        if gap < CANDIDATE_MIN_GAP_SECONDS * 2 and (known or added):
            break
        middle = round(bounds[widest] + gap / 2, 1)
        added.append(middle)
        bounds.insert(widest + 1, middle)
    return added


def _time_thinned(values: list[float], min_spacing: float) -> list[float]:
    """Sorted times with no two closer than ``min_spacing``, earliest of each run.

    Thinning by index instead would keep a cluster a cluster: forty changes inside
    one busy ten minutes are still forty candidates in ten minutes.
    """
    if min_spacing <= 0:
        return list(values)
    kept: list[float] = []
    for value in values:
        if not kept or value - kept[-1] >= min_spacing:
            kept.append(value)
    return kept


def _snapped_to_anchor(
    timestamp: float, anchors: list[float], taken: set[float]
) -> float | None:
    """The nearest unused cue within reach, or ``None`` to stay where it was."""
    nearest = None
    for anchor in anchors:
        if abs(anchor - timestamp) > ANCHOR_SNAP_SECONDS or anchor in taken:
            continue
        if nearest is None or abs(anchor - timestamp) < abs(nearest - timestamp):
            nearest = anchor
    return nearest


def _with_density_floor(
    video_path: str,
    detected: list[float],
    *,
    wanted: int,
    anchors: list[float] | None = None,
) -> list[tuple[float, str]]:
    """Detected changes, plus enough gap-filling looks to reach the budget.

    Each candidate carries where it came from, because a note written from evenly
    spaced snapshots reads exactly like one written from real scene changes and the
    result has to be able to say which it was. A gap-filler that landed on a
    subtitle cue says ``cue`` rather than ``floor`` — same look, better placed, and
    the difference is worth being able to count.

    Detected changes are thinned by *time*, not by count, before the floor runs,
    so that a cluster cannot eat the budget: forty changes bunched into one busy
    ten minutes are still forty candidates in ten minutes if you thin by index. No
    two candidates may sit closer than the ideal spacing, and what that frees goes
    to the holes. It only intervenes when detection over-subscribes the budget; with
    room to spare every reported change is kept, because discarding a measured
    change to make space for a sample is backwards.

    A hole in the *output* is not the same as a hole here. The 69-minute demo class
    ends up with 17.9 minutes carrying no frame, and that is the right answer, not
    a gap in coverage: candidates were spread across it 2.5 minutes apart, grabbed,
    compared, and found identical to a picture already kept. The screen did not
    change for 17.9 minutes, and that was established by looking.
    """
    try:
        duration = _video_duration_seconds(video_path)
    except (subprocess.SubprocessError, ValueError, RuntimeError, OSError) as exc:
        logger.warning("Duration lookup failed for %s: %s", video_path, exc)
        duration = 0.0
    picked = sorted(detected)
    # Only when detection over-subscribes the budget. With room to spare, every
    # reported change is kept and the floor fills around them — thinning there
    # would discard a measured change to make space for a sample.
    if len(picked) > wanted > 0 and duration > 0:
        picked = _time_thinned(picked, duration / wanted)
    if len(picked) > wanted:
        picked = [picked[index] for index in _selected_even_indices(len(picked), wanted)]
    candidates: list[tuple[float, str]] = [(ts, "scene") for ts in picked]
    if len(candidates) < wanted:
        cues = sorted(anchors or [])
        taken = {ts for ts, _ in candidates}
        for timestamp in _gap_fill_timestamps(picked, duration, wanted - len(candidates)):
            snapped = _snapped_to_anchor(timestamp, cues, taken) if cues else None
            chosen = timestamp if snapped is None else snapped
            if chosen in taken:
                continue
            taken.add(chosen)
            candidates.append((chosen, "floor" if snapped is None else "cue"))
    candidates.sort(key=lambda item: item[0])
    return candidates


def _extract_scene_frames(
    video_path: str,
    output_dir: Path,
    *,
    threshold: float,
    max_frames: int,
    anchors: list[float] | None = None,
) -> list[dict[str, Any]]:
    """The frames worth looking at, at full resolution, within budget.

    Reported changes first, then the longest unlooked stretches split until the
    candidate budget is full, so a detection pass that found four changes in 69
    minutes no longer decides that four looks is the whole recording.
    """
    for prefix in ("scene_", "floor_", "cue_"):
        for stale in output_dir.glob(f"{prefix}*.jpg"):
            stale.unlink(missing_ok=True)
    # Grab more than the budget so that dropping duplicates does not leave the
    # budget unfilled, then spread whatever survives across the file. More changes
    # than budget is the normal case for a lecture, and spreading beats taking the
    # first N of them.
    wanted = max(1, max_frames * SCENE_OVERSAMPLE)
    detected, used_threshold = _detect_scene_candidates(
        video_path, threshold=threshold, wanted=wanted
    )
    candidates = _with_density_floor(video_path, detected, wanted=wanted, anchors=anchors)
    if not candidates:
        return []
    grabbed: list[dict[str, Any]] = []
    for index, (timestamp, source) in enumerate(candidates, start=1):
        output = output_dir / f"{source}_{index:04d}.jpg"
        if _grab_frame(video_path, output, timestamp):
            record = _frame_record(output, timestamp, source)
            if source == "scene":
                record["scene_threshold"] = used_threshold
            grabbed.append(record)
    distinct = _drop_near_duplicates(grabbed)
    budget = max_frames
    if distinct and not any(
        float(record.get("edge_contrast") or 0) >= SCREEN_CONTENT_EDGE_CONTRAST
        for record in distinct
    ):
        # Nothing in this recording looks like a screen. Look a little, not a lot.
        budget = min(max_frames, SCREENLESS_FRAME_BUDGET)
    return _trim_to_budget(distinct, budget)


def _trim_to_budget(records: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    """Fit the budget by giving up samples before giving up detected changes.

    Spreading evenly over the merged list would let a gap-filling look survive at
    the cost of a real change three seconds away, which is backwards: the floor
    exists to cover what detection missed, not to compete with what it found.
    """
    if len(records) <= budget:
        return records
    floors = [record for record in records if record.get("source") in SAMPLED_SOURCES]
    scenes = len(records) - len(floors)
    if floors and scenes < budget:
        thinned = _trim_to_budget_evenly(floors, budget - scenes)
        surviving = {id(record) for record in thinned}
        return [
            record for record in records
            if record.get("source") not in SAMPLED_SOURCES or id(record) in surviving
        ]
    if floors:
        for record in floors:
            Path(str(record.get("path"))).unlink(missing_ok=True)
        records = [record for record in records if record.get("source") not in SAMPLED_SOURCES]
    return _trim_to_budget_evenly(records, budget)


def _trim_to_budget_evenly(records: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    if len(records) <= budget:
        return records
    selected = set(_selected_even_indices(len(records), budget))
    kept: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if index in selected:
            kept.append(record)
        else:
            Path(str(record.get("path"))).unlink(missing_ok=True)
    return kept


def _segment_capture_timestamps(segment: dict[str, Any]) -> list[float]:
    try:
        start = max(0.0, float(segment.get("start") or 0))
    except (TypeError, ValueError):
        return []
    try:
        end = float(segment.get("end")) if segment.get("end") is not None else start
    except (TypeError, ValueError):
        end = start
    end = max(start, end)
    if end - start < 1.5:
        return [round(start, 1)]
    mid = start + (end - start) / 2
    tail = max(start, end - 0.4)
    timestamps = [start, mid, tail]
    deduped: list[float] = []
    for ts in timestamps:
        rounded = round(ts, 1)
        if not deduped or abs(rounded - deduped[-1]) >= 1.0:
            deduped.append(rounded)
    return deduped


def _extract_timepoint_frames(
    video_path: str,
    output_dir: Path,
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract local candidate frames around requested transcript time windows."""
    results: list[dict[str, Any]] = []
    seen_timestamps: set[float] = set()
    for segment_index, segment in enumerate(segments, start=1):
        for ts in _segment_capture_timestamps(segment):
            if ts in seen_timestamps:
                continue
            seen_timestamps.add(ts)
            mm = int(ts // 60)
            ss = int(ts % 60)
            tenth = int(round((ts - int(ts)) * 10))
            filename = f"ts_{mm:02d}{ss:02d}_{tenth:d}.jpg"
            output = output_dir / filename
            cmd = [
                _ffmpeg_path(),
                "-y",
                "-ss", str(ts),
                "-i", str(video_path),
                "-frames:v", "1",
                "-q:v", "2",
                str(output),
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
            except subprocess.CalledProcessError:
                continue
            if output.is_file():
                record = _frame_record(output, ts, "timepoint")
                for key in ("visual_request_id", "note_section", "query", "reason"):
                    if segment.get(key):
                        record[key] = segment.get(key)
                record["segment_index"] = segment_index
                results.append(record)
    return results


def _deduplicate_frames(
    scene_frames: list[dict[str, Any]],
    timepoint_frames: list[dict[str, Any]],
    *,
    min_gap_seconds: float = 2.0,
) -> list[dict[str, Any]]:
    """Merge scene and timepoint frames, dropping near-duplicates within min_gap_seconds."""
    merged = sorted(scene_frames + timepoint_frames, key=lambda f: f["timestamp_seconds"])
    if not merged:
        return []
    deduped: list[dict[str, Any]] = [merged[0]]
    for frame in merged[1:]:
        if frame["timestamp_seconds"] - deduped[-1]["timestamp_seconds"] >= min_gap_seconds:
            deduped.append(frame)
        else:
            # Transcript-derived timepoints map better to note sections than either
            # a raw scene change or a gap-filling look.
            if frame["source"] == "timepoint" and deduped[-1]["source"] in {"scene", "floor"}:
                deduped[-1] = frame
    return deduped


def extract_candidate_frames(
    video_path: str,
    output_dir: Path,
    segments: list[dict[str, Any]] | None = None,
    *,
    scene_threshold: float = 0.3,
    max_scene_frames: int = 30,
    min_gap_seconds: float = 2.0,
    anchor_seconds: list[float] | None = None,
) -> list[dict[str, Any]]:
    """Extract candidate still frames from a video file.

    Uses ffmpeg scene-detection to find significant visual changes, with an
    always-on density floor so that a stretch nothing was reported in is still
    looked at, optionally supplemented by per-segment timepoint captures. Returns
    a deduplicated list of frame metadata sorted by timestamp.

    Args:
        video_path: Path to the source video file.
        output_dir: Directory to write JPEG frames into.
        segments: Optional transcript segments with ``start`` timestamps.
        scene_threshold: ffmpeg scene change sensitivity (0.0-1.0).
        max_scene_frames: Upper bound on scene frames kept.
        min_gap_seconds: Minimum gap between consecutive output frames.
        anchor_seconds: Optional subtitle cue start times. Only the times are
            used, never the words: a sampled frame that lands within
            ``ANCHOR_SNAP_SECONDS`` of a cue is moved onto it, so it shows the
            screen at a moment somebody was speaking about.

    Returns:
        List of dicts with keys ``path``, ``timestamp_seconds``, and ``source``.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    scene_frames: list[dict[str, Any]] = []
    if max_scene_frames > 0:
        try:
            scene_frames = _extract_scene_frames(
                video_path, output_dir, threshold=scene_threshold,
                max_frames=max_scene_frames, anchors=anchor_seconds,
            )
        except Exception as exc:
            logger.warning("Scene frame extraction failed: %s", exc)

    timepoint_frames: list[dict[str, Any]] = []
    if segments:
        try:
            timepoint_frames = _extract_timepoint_frames(video_path, output_dir, segments)
        except Exception as exc:
            logger.warning("Timepoint frame extraction failed: %s", exc)

    # No separate fallback pass: even sampling is what the density floor already
    # does when detection reports nothing, and having it as an either/or branch is
    # what made a weak detection pass discard the sampling that would have covered
    # the rest of the recording.
    return _deduplicate_frames(scene_frames, timepoint_frames, min_gap_seconds=min_gap_seconds)


__all__ = ["extract_candidate_frames"]
