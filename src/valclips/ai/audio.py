"""Audio analysis for Valorant gameplay clips.

Uses FFmpeg silencedetect and volumedetect filters to extract audio
characteristics that correlate with gameplay action: gunfire produces
sharp loudness spikes, abilities create distinct audio patterns, and
tense moments often follow quiet-to-loud transitions.

Signals extracted:
- Audio peaks (loud moments above a threshold)
- Peak density (peaks per second -- higher = more action)
- Loudness range / dynamic range (quiet-to-loud contrast = tension + action)
- Gunfire segments (clusters of rapid loudness spikes)
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field

from ..config import FFPROBE_TIMEOUT

logger = logging.getLogger(__name__)

# FFmpeg command timeout for audio analysis (seconds).
AUDIO_ANALYSIS_TIMEOUT = 30

# silencedetect defaults -- segments quieter than this for longer than
# min_silence_duration are classified as silence.
DEFAULT_SILENCE_THRESHOLD_DB = -30.0
DEFAULT_MIN_SILENCE_DURATION = 0.5

# Peak detection: audio louder than this is considered a "peak" moment.
DEFAULT_PEAK_THRESHOLD_DB = -20.0

# Gunfire detection: minimum number of peaks within GUNFIRE_WINDOW seconds
# to classify a region as gunfire.
GUNFIRE_WINDOW = 2.0
GUNFIRE_MIN_PEAKS = 3

# Scoring constants
MAX_PEAK_DENSITY = 1.5       # peaks/sec at which density component maxes out
MAX_LOUDNESS_RANGE = 40.0    # dB range at which range component maxes out
DENSITY_WEIGHT = 0.5
RANGE_WEIGHT = 0.3
PEAK_COUNT_WEIGHT = 0.2
MAX_PEAK_COUNT_FOR_SCORE = 20  # peak count at which the peak-count component maxes


@dataclass
class SilenceInterval:
    """A detected silence interval within the clip."""
    start: float   # seconds
    end: float     # seconds
    duration: float


@dataclass
class LoudSegment:
    """A non-silent (loud) segment -- the inverse of a silence interval."""
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class VolumeStats:
    """Overall volume statistics from volumedetect."""
    mean_volume: float = 0.0       # dB
    max_volume: float = 0.0        # dB
    histogram: dict[int, int] = field(default_factory=dict)


def _run_ffmpeg(args: list[str], timeout: int = AUDIO_ANALYSIS_TIMEOUT) -> str | None:
    """Run an FFmpeg/FFprobe command and return its stderr output.

    FFmpeg writes filter diagnostic output (silencedetect, volumedetect,
    astats, etc.) to stderr regardless of output format. Returns None on
    failure.
    """
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stderr
    except subprocess.TimeoutExpired:
        logger.warning("FFmpeg command timed out after %ds: %s", timeout, " ".join(args[:6]))
        return None
    except FileNotFoundError:
        logger.error("FFmpeg not found on PATH")
        return None
    except OSError as exc:
        logger.error("FFmpeg execution error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# silencedetect -- find quiet intervals
# ---------------------------------------------------------------------------

_SILENCE_START_RE = re.compile(
    r"silence_start:\s*(-?[\d.]+)"
)
_SILENCE_END_RE = re.compile(
    r"silence_end:\s*(-?[\d.]+)\s*\|\s*silence_duration:\s*(-?[\d.]+)"
)


def detect_silence(
    clip_path: str,
    noise_db: float = DEFAULT_SILENCE_THRESHOLD_DB,
    min_duration: float = DEFAULT_MIN_SILENCE_DURATION,
) -> list[SilenceInterval]:
    """Detect silence intervals using FFmpeg silencedetect filter.

    Args:
        clip_path: Path to the video/audio file.
        noise_db: Silence threshold in dB (segments quieter than this
            are considered silent).
        min_duration: Minimum silence duration in seconds.

    Returns:
        List of SilenceInterval objects sorted by start time.
    """
    cmd = [
        "ffmpeg", "-i", clip_path,
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
        "-vn",
        "-f", "null", "-",
    ]

    stderr = _run_ffmpeg(cmd)
    if stderr is None:
        return []

    intervals: list[SilenceInterval] = []
    pending_start: float | None = None

    for line in stderr.split("\n"):
        start_match = _SILENCE_START_RE.search(line)
        if start_match:
            pending_start = float(start_match.group(1))
            continue

        end_match = _SILENCE_END_RE.search(line)
        if end_match:
            end = float(end_match.group(1))
            dur = float(end_match.group(2))
            start = pending_start if pending_start is not None else end - dur
            intervals.append(SilenceInterval(start=start, end=end, duration=dur))
            pending_start = None

    return sorted(intervals, key=lambda s: s.start)


def _invert_silence(
    silence: list[SilenceInterval],
    clip_duration: float,
) -> list[LoudSegment]:
    """Convert silence intervals into loud (non-silent) segments.

    The gaps between silent intervals -- plus the start/end of the clip
    if they are not silent -- are the loud segments.
    """
    if not silence:
        # No silence detected -> the entire clip is loud.
        if clip_duration > 0:
            return [LoudSegment(start=0.0, end=clip_duration)]
        return []

    segments: list[LoudSegment] = []

    # Before first silence
    first_silence_start = silence[0].start
    if first_silence_start > 0.05:  # skip trivially short
        segments.append(LoudSegment(start=0.0, end=first_silence_start))

    # Between consecutive silence intervals
    for i in range(len(silence) - 1):
        gap_start = silence[i].end
        gap_end = silence[i + 1].start
        if gap_end - gap_start > 0.05:
            segments.append(LoudSegment(start=gap_start, end=gap_end))

    # After last silence
    last_silence_end = silence[-1].end
    if clip_duration - last_silence_end > 0.05:
        segments.append(LoudSegment(start=last_silence_end, end=clip_duration))

    return segments


# ---------------------------------------------------------------------------
# volumedetect -- overall loudness stats
# ---------------------------------------------------------------------------

_MEAN_VOLUME_RE = re.compile(r"mean_volume:\s*(-?[\d.]+)\s*dB")
_MAX_VOLUME_RE = re.compile(r"max_volume:\s*(-?[\d.]+)\s*dB")
_HISTOGRAM_RE = re.compile(r"histogram_(\d+)db:\s*(\d+)")


def get_volume_stats(clip_path: str) -> VolumeStats:
    """Get overall volume statistics using FFmpeg volumedetect filter.

    Returns a VolumeStats with mean/max volume and a dB histogram.
    """
    cmd = [
        "ffmpeg", "-i", clip_path,
        "-af", "volumedetect",
        "-vn",
        "-f", "null", "-",
    ]

    stderr = _run_ffmpeg(cmd)
    if stderr is None:
        return VolumeStats()

    stats = VolumeStats()
    for line in stderr.split("\n"):
        mean_m = _MEAN_VOLUME_RE.search(line)
        if mean_m:
            stats.mean_volume = float(mean_m.group(1))
            continue

        max_m = _MAX_VOLUME_RE.search(line)
        if max_m:
            stats.max_volume = float(max_m.group(1))
            continue

        hist_m = _HISTOGRAM_RE.search(line)
        if hist_m:
            db_val = int(hist_m.group(1))
            count = int(hist_m.group(2))
            stats.histogram[db_val] = count

    return stats


# ---------------------------------------------------------------------------
# Public API: detect_audio_peaks
# ---------------------------------------------------------------------------

def detect_audio_peaks(
    clip_path: str,
    threshold_db: float = DEFAULT_PEAK_THRESHOLD_DB,
) -> list[dict]:
    """Detect loud audio moments (peaks) in a clip.

    Uses silencedetect to find quiet intervals, then inverts to find
    loud segments. Each loud segment midpoint is reported as a peak.
    The loudness estimate is derived from volumedetect overall stats
    combined with segment duration heuristics.

    Args:
        clip_path: Path to the video/audio file.
        threshold_db: Loudness threshold in dB. Silence below this
            value is used to delineate peaks.

    Returns:
        List of dicts with keys:
            timestamp (float): peak time in seconds
            loudness_db (float): estimated loudness in dB
            duration (float): duration of the loud segment in seconds
    """
    silence = detect_silence(clip_path, noise_db=threshold_db, min_duration=0.3)
    volume = get_volume_stats(clip_path)

    clip_duration = _get_duration(clip_path)
    if clip_duration is None or clip_duration <= 0:
        return []

    loud_segments = _invert_silence(silence, clip_duration)

    peaks: list[dict] = []
    longest = max((seg.duration for seg in loud_segments), default=1.0)

    for seg in loud_segments:
        midpoint = (seg.start + seg.end) / 2.0
        ratio = seg.duration / longest if longest > 0 else 0.5
        estimated_db = volume.mean_volume + (volume.max_volume - volume.mean_volume) * ratio

        peaks.append({
            "timestamp": round(midpoint, 3),
            "loudness_db": round(estimated_db, 1),
            "duration": round(seg.duration, 3),
        })

    return sorted(peaks, key=lambda p: p["timestamp"])


# ---------------------------------------------------------------------------
# Public API: get_audio_intensity_score
# ---------------------------------------------------------------------------

def get_audio_intensity_score(
    clip_path: str,
    duration: float | None = None,
) -> dict:
    """Compute an audio intensity score for a clip.

    Combines peak count, peak density, and loudness range into a
    single 0-1 score that correlates with gameplay action intensity.

    Args:
        clip_path: Path to the video/audio file.
        duration: Clip duration in seconds. If not provided, it will be
            detected via FFprobe.

    Returns:
        Dict with keys:
            peak_count (int): number of loud moments detected
            peak_density (float): peaks per second
            loudness_range (float): dynamic range in dB
            max_volume (float): peak volume in dB
            mean_volume (float): mean volume in dB
            score (float): normalized 0-1 intensity score
    """
    if duration is None:
        duration = _get_duration(clip_path)

    if duration is None or duration <= 0:
        return {
            "peak_count": 0,
            "peak_density": 0.0,
            "loudness_range": 0.0,
            "max_volume": 0.0,
            "mean_volume": 0.0,
            "score": 0.0,
        }

    peaks = detect_audio_peaks(clip_path)
    volume = get_volume_stats(clip_path)

    peak_count = len(peaks)
    peak_density = peak_count / duration if duration > 0 else 0.0
    loudness_range = abs(volume.max_volume - volume.mean_volume)

    # Normalize each component to 0-1
    density_norm = min(1.0, peak_density / MAX_PEAK_DENSITY)
    range_norm = min(1.0, loudness_range / MAX_LOUDNESS_RANGE)
    count_norm = min(1.0, peak_count / MAX_PEAK_COUNT_FOR_SCORE)

    # Weighted combination
    score = (
        DENSITY_WEIGHT * density_norm
        + RANGE_WEIGHT * range_norm
        + PEAK_COUNT_WEIGHT * count_norm
    )
    score = round(min(1.0, max(0.0, score)), 4)

    return {
        "peak_count": peak_count,
        "peak_density": round(peak_density, 4),
        "loudness_range": round(loudness_range, 1),
        "max_volume": round(volume.max_volume, 1),
        "mean_volume": round(volume.mean_volume, 1),
        "score": score,
    }


# ---------------------------------------------------------------------------
# Public API: detect_gunfire_segments
# ---------------------------------------------------------------------------

def detect_gunfire_segments(clip_path: str) -> list[tuple[float, float]]:
    """Detect segments with rapid loudness spikes (gunfire patterns).

    Gunfire in Valorant produces clusters of short, sharp audio peaks
    in quick succession. This function finds regions where many peaks
    are concentrated within a short time window.

    Args:
        clip_path: Path to the video/audio file.

    Returns:
        List of (start_sec, end_sec) tuples for detected gunfire
        segments, sorted by start time.
    """
    # Use a tighter silence threshold to get more granular peaks
    silence = detect_silence(
        clip_path,
        noise_db=-25.0,
        min_duration=0.15,
    )
    clip_duration = _get_duration(clip_path)
    if clip_duration is None or clip_duration <= 0:
        return []

    loud_segments = _invert_silence(silence, clip_duration)

    if len(loud_segments) < GUNFIRE_MIN_PEAKS:
        return []

    # Extract midpoints of short loud bursts (< 1.5s = likely individual
    # shots or short bursts, not sustained audio like music or voice).
    burst_times = [
        (seg.start + seg.end) / 2.0
        for seg in loud_segments
        if seg.duration < 1.5
    ]

    if len(burst_times) < GUNFIRE_MIN_PEAKS:
        return []

    # Sliding window: find regions with enough bursts within GUNFIRE_WINDOW
    gunfire_regions: list[tuple[float, float]] = []
    i = 0
    while i < len(burst_times):
        window_end = burst_times[i] + GUNFIRE_WINDOW
        # Count bursts within window
        j = i
        while j < len(burst_times) and burst_times[j] <= window_end:
            j += 1
        count = j - i

        if count >= GUNFIRE_MIN_PEAKS:
            region_start = burst_times[i]
            region_end = burst_times[j - 1]
            # Add small padding
            region_start = max(0.0, region_start - 0.3)
            region_end = min(clip_duration, region_end + 0.3)
            gunfire_regions.append((round(region_start, 3), round(region_end, 3)))
            i = j  # skip past this cluster
        else:
            i += 1

    # Merge overlapping regions
    return _merge_gunfire_regions(gunfire_regions)


def _merge_gunfire_regions(
    regions: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Merge overlapping or adjacent gunfire regions."""
    if not regions:
        return []

    sorted_regions = sorted(regions, key=lambda r: r[0])
    merged: list[tuple[float, float]] = [sorted_regions[0]]

    for start, end in sorted_regions[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 0.5:  # allow 0.5s gap for merge
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    return merged


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_duration(clip_path: str) -> float | None:
    """Get clip duration in seconds via FFprobe.

    Returns None if duration cannot be determined.
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        clip_path,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=FFPROBE_TIMEOUT,
        )
        output = result.stdout.strip()
        if output and output != "N/A":
            return float(output)
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        return None
