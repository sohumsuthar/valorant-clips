"""Credit-free clip analyzer using FFmpeg heuristics.

Scores clips based on:
- Scene change density (more action = more cuts/flashes)
- Bitrate variation (action scenes spike bitrate in VBR)
- Audio intensity (gunfire peaks, loudness dynamics)
- Motion intensity (frame-size variance, flick detection)
- Curated folder signals (user already sorted best clips)
- Duration signals (shorter = likely already trimmed highlights)
- Tag signals (weapon/agent tags suggest noteworthy clips)
"""

import logging
import subprocess
import re
from pathlib import Path

from ..config import FFPROBE_TIMEOUT
from ..models import AnalysisResult
from .base import ClipAnalyzer

logger = logging.getLogger(__name__)


# Folder names that signal curated/highlight content
CURATED_FOLDERS = {
    "best": 2.0,
    "clips": 1.0,
    "old clips that are good": 2.0,
    "sherf": 1.5,
    "phx": 1.0,
    "knives": 1.5,
    "guardian": 1.0,
    "op": 1.0,
    "finished vids": 1.5,
}

# Tags that signal interesting content
SIGNAL_TAGS = {
    "highlight": 2.0,
    "knife-kill": 1.5,
    "operator": 1.0,
    "sheriff": 1.5,
    "edited": 1.5,
    "clip": 1.0,
}


def _get_scene_score(clip_path: str, duration: float | None) -> tuple[float, int]:
    """Count scene changes and compute density score.

    Returns (density_score_0_to_1, scene_change_count).
    """
    if not duration or duration <= 0:
        return 0.0, 0

    cmd = [
        "ffmpeg", "-i", clip_path,
        "-vf", "select='gt(scene,0.25)',showinfo",
        "-vsync", "vfr",
        "-f", "null", "-",
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=FFPROBE_TIMEOUT * 3,
        )
        count = 0
        for line in result.stderr.split("\n"):
            if "showinfo" in line and "pts_time:" in line:
                count += 1

        # Normalize: scene changes per second
        # Typical gameplay: 0.1-0.3/s, action: 0.5-1.0/s, intense: 1.0+/s
        density = count / duration
        score = min(1.0, density / 0.8)  # 0.8 changes/s = max score
        return score, count
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 0.0, 0


def _get_frame_sizes_raw(clip_path: str) -> list[int]:
    """Extract frame packet sizes via ffprobe. Used by both bitrate and motion."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-select_streams", "v:0",
        "-show_entries", "frame=pkt_size",
        "-of", "csv=p=0",
        clip_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=FFPROBE_TIMEOUT * 3,
        )
        sizes = []
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if line and line.isdigit():
                sizes.append(int(line))
        return sizes
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def _bitrate_from_sizes(sizes: list[int]) -> float:
    """Compute bitrate variance score from pre-extracted frame sizes."""
    if len(sizes) < 10:
        return 0.0
    mean = sum(sizes) / len(sizes)
    if mean == 0:
        return 0.0
    variance = sum((s - mean) ** 2 for s in sizes) / len(sizes)
    cv = (variance ** 0.5) / mean
    return min(1.0, cv / 1.5)


def _get_bitrate_variance(clip_path: str) -> float:
    """Measure bitrate variance across the clip using frame sizes.

    High variance = mix of calm/action. Returns normalized 0-1 score.
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-select_streams", "v:0",
        "-show_entries", "frame=pkt_size",
        "-of", "csv=p=0",
        clip_path,
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=FFPROBE_TIMEOUT * 3,
        )
        sizes = []
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if line and line.isdigit():
                sizes.append(int(line))

        if len(sizes) < 10:
            return 0.0

        mean = sum(sizes) / len(sizes)
        if mean == 0:
            return 0.0

        variance = sum((s - mean) ** 2 for s in sizes) / len(sizes)
        # Coefficient of variation (std/mean)
        cv = (variance ** 0.5) / mean
        # CV > 1.0 is very high variance, typical of action clips
        return min(1.0, cv / 1.5)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 0.0


def _folder_bonus(directory: str | None) -> float:
    """Bonus score based on folder name (user curation signal)."""
    if not directory:
        return 0.0

    dir_lower = directory.lower()
    bonus = 0.0
    for folder, weight in CURATED_FOLDERS.items():
        if folder in dir_lower:
            bonus = max(bonus, weight)
    return bonus


def _tag_bonus(tags: list[str]) -> float:
    """Bonus score based on existing tags."""
    bonus = 0.0
    for tag in tags:
        tag_lower = tag.lower()
        for signal, weight in SIGNAL_TAGS.items():
            if signal == tag_lower:
                bonus += weight
    return min(3.0, bonus)  # Cap at 3


def _duration_signal(duration: float | None) -> float:
    """Score based on duration. Short clips in curated folders = likely trimmed highlights."""
    if not duration:
        return 0.0
    # Sweet spot: 10-30s (trimmed highlight), penalty for very long/short
    if 10 <= duration <= 30:
        return 1.0
    elif 5 <= duration < 10:
        return 0.6
    elif 30 < duration <= 60:
        return 0.5
    elif 60 < duration <= 120:
        return 0.3
    else:
        return 0.1


def _combined_audio_analysis(clip_path: str, duration: float | None) -> dict:
    """Single-pass combined audio analysis: silencedetect + volumedetect.

    Runs ONE FFmpeg command with chained audio filters to get both
    silence intervals and volume statistics, avoiding duplicate file reads.

    Returns dict with: score, peak_count, peak_density, loudness_range,
    gunfire_segments, mean_volume, max_volume.
    """
    try:
        from .audio import (
            detect_silence, get_volume_stats, _invert_silence, _get_duration,
            _SILENCE_START_RE, _SILENCE_END_RE, _MEAN_VOLUME_RE, _MAX_VOLUME_RE,
            SilenceInterval, VolumeStats, LoudSegment,
            GUNFIRE_WINDOW, GUNFIRE_MIN_PEAKS,
            MAX_PEAK_DENSITY, MAX_LOUDNESS_RANGE, MAX_PEAK_COUNT_FOR_SCORE,
            DENSITY_WEIGHT, RANGE_WEIGHT, PEAK_COUNT_WEIGHT,
        )
    except ImportError:
        return {"score": 0.0, "peak_count": 0, "peak_density": 0.0,
                "loudness_range": 0.0, "gunfire_segments": 0,
                "mean_volume": 0.0, "max_volume": 0.0}

    if not duration:
        duration = _get_duration(clip_path)
    if not duration or duration <= 0:
        return {"score": 0.0, "peak_count": 0, "peak_density": 0.0,
                "loudness_range": 0.0, "gunfire_segments": 0,
                "mean_volume": 0.0, "max_volume": 0.0}

    # Single FFmpeg pass: silencedetect + volumedetect combined
    cmd = [
        "ffmpeg", "-i", clip_path,
        "-af", "silencedetect=noise=-20dB:d=0.3,volumedetect",
        "-vn", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=FFPROBE_TIMEOUT * 3,
        )
        stderr = result.stderr or ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {"score": 0.0, "peak_count": 0, "peak_density": 0.0,
                "loudness_range": 0.0, "gunfire_segments": 0,
                "mean_volume": 0.0, "max_volume": 0.0}

    # Parse silence intervals
    silence: list[SilenceInterval] = []
    pending_start = None
    vol = VolumeStats()

    for line in stderr.split("\n"):
        sm = _SILENCE_START_RE.search(line)
        if sm:
            pending_start = float(sm.group(1))
            continue
        em = _SILENCE_END_RE.search(line)
        if em:
            end = float(em.group(1))
            dur = float(em.group(2))
            start = pending_start if pending_start is not None else end - dur
            silence.append(SilenceInterval(start=start, end=end, duration=dur))
            pending_start = None
            continue
        mm = _MEAN_VOLUME_RE.search(line)
        if mm:
            vol.mean_volume = float(mm.group(1))
            continue
        xm = _MAX_VOLUME_RE.search(line)
        if xm:
            vol.max_volume = float(xm.group(1))

    # Compute peaks from silence inversion
    loud_segments = _invert_silence(silence, duration)
    peak_count = len(loud_segments)
    peak_density = peak_count / duration if duration > 0 else 0.0
    loudness_range = abs(vol.max_volume - vol.mean_volume)

    # Gunfire detection: short loud bursts clustered together
    burst_times = [
        (seg.start + seg.end) / 2.0
        for seg in loud_segments
        if seg.duration < 1.5
    ]
    gunfire_count = 0
    if len(burst_times) >= GUNFIRE_MIN_PEAKS:
        i = 0
        while i < len(burst_times):
            window_end = burst_times[i] + GUNFIRE_WINDOW
            j = i
            while j < len(burst_times) and burst_times[j] <= window_end:
                j += 1
            if j - i >= GUNFIRE_MIN_PEAKS:
                gunfire_count += 1
                i = j
            else:
                i += 1

    # Score
    density_norm = min(1.0, peak_density / MAX_PEAK_DENSITY)
    range_norm = min(1.0, loudness_range / MAX_LOUDNESS_RANGE)
    count_norm = min(1.0, peak_count / MAX_PEAK_COUNT_FOR_SCORE)
    score = round(min(1.0, max(0.0,
        DENSITY_WEIGHT * density_norm +
        RANGE_WEIGHT * range_norm +
        PEAK_COUNT_WEIGHT * count_norm
    )), 4)

    return {
        "score": score,
        "peak_count": peak_count,
        "peak_density": round(peak_density, 4),
        "loudness_range": round(loudness_range, 1),
        "gunfire_segments": gunfire_count,
        "mean_volume": round(vol.mean_volume, 1),
        "max_volume": round(vol.max_volume, 1),
    }


def _motion_from_frame_sizes(frame_sizes: list[int]) -> float:
    """Compute motion score from frame sizes (reuses bitrate variance data).

    Same data as _get_bitrate_variance but returns a 0-1 motion score.
    """
    if len(frame_sizes) < 10:
        return 0.0
    mean = sum(frame_sizes) / len(frame_sizes)
    if mean == 0:
        return 0.0
    variance = sum((s - mean) ** 2 for s in frame_sizes) / len(frame_sizes)
    cv = (variance ** 0.5) / mean
    return min(1.0, cv / 1.5)


class FFmpegHeuristicAnalyzer(ClipAnalyzer):
    """Score clips using FFmpeg analysis + metadata heuristics. No API needed.

    Modes:
        quick: Metadata-only (instant, least accurate)
        normal (default): Scene changes + bitrate variance (fast)
        deep: + audio intensity + gunfire detection + motion analysis (slow but thorough)
    """

    def __init__(self, quick: bool = False, deep: bool = False):
        """
        Args:
            quick: If True, skip FFmpeg analysis entirely and use metadata only.
            deep: If True, add audio + motion analysis on top of scene/bitrate.
        """
        self.quick = quick
        self.deep = deep

    def analyze(self, clip_path: str, keyframes: list[Path]) -> AnalysisResult:
        return self.analyze_with_metadata(clip_path, keyframes)

    def analyze_with_metadata(
        self,
        clip_path: str,
        keyframes: list[Path],
        duration: float | None = None,
        directory: str | None = None,
        tags: list[str] | None = None,
    ) -> AnalysisResult:
        tags = tags or []
        result_tags = []

        # --- FFmpeg-based signals (skipped in quick mode) ---
        scene_score = 0.0
        scene_count = 0
        bitrate_score = 0.0
        audio_score = 0.0
        audio_data: dict = {}
        motion_score = 0.0
        gunfire_segments = 0

        if not self.quick:
            scene_score, scene_count = _get_scene_score(clip_path, duration)

            if self.deep:
                # Optimized deep mode: 3 passes total
                # Pass 2: ffprobe frame sizes → bitrate variance + motion score
                frame_sizes = _get_frame_sizes_raw(clip_path)
                bitrate_score = _bitrate_from_sizes(frame_sizes)
                motion_score = _motion_from_frame_sizes(frame_sizes)

                # Pass 3: single combined audio pass → peaks + volume + gunfire
                audio_data = _combined_audio_analysis(clip_path, duration)
                audio_score = audio_data.get("score", 0.0)
                gunfire_segments = audio_data.get("gunfire_segments", 0)
            else:
                bitrate_score = _get_bitrate_variance(clip_path)

        # --- Metadata-based signals ---
        folder_bonus = _folder_bonus(directory)
        tag_bonus = _tag_bonus(tags)
        dur_signal = _duration_signal(duration)

        # --- Weighted combination ---
        if self.quick:
            raw = (
                folder_bonus * 3.0 +
                tag_bonus * 2.0 +
                dur_signal * 1.5
            )
            max_raw = 3.0 * 3.0 + 3.0 * 2.0 + 1.0 * 1.5  # 16.5
        elif self.deep:
            # Deep mode: full multi-signal scoring
            raw = (
                scene_score * 3.5 +       # Visual action density
                audio_score * 3.0 +        # Audio intensity (gunfire, abilities)
                motion_score * 2.0 +       # Motion/flick activity
                bitrate_score * 1.5 +      # Bitrate spikes during action
                folder_bonus * 2.0 +       # User curation signal
                tag_bonus * 1.0 +          # Existing tags
                dur_signal * 0.5           # Duration sweet spot
            )
            # Gunfire bonus: direct evidence of combat
            if gunfire_segments > 0:
                raw += min(gunfire_segments, 3) * 1.0

            max_raw = (
                3.5 + 3.0 + 2.0 + 1.5 +  # ffmpeg signals
                2.0 * 2.0 +               # folder_bonus max 2.0
                1.0 * 3.0 +               # tag_bonus max 3.0
                0.5 + 3.0                  # dur_signal + gunfire bonus
            )  # = 20.0
        else:
            # Normal mode: scene + bitrate + metadata (fast)
            raw = (
                scene_score * 4.0 +       # Most important: action density
                bitrate_score * 2.0 +      # Bitrate spikes during action
                folder_bonus * 2.5 +       # User curation is a strong signal
                tag_bonus * 1.5 +          # Tags indicate notable clips
                dur_signal * 1.0           # Duration sweet spot
            )
            max_raw = 4.0 + 2.0 + 2.5 * 2.0 + 1.5 * 3.0 + 1.0  # 16.5

        # Normalize to 1-10 scale
        normalized = raw / max_raw if max_raw > 0 else 0.0
        score = max(1, min(10, round(normalized * 9 + 1)))

        # --- Highlight type classification ---
        highlight_type = "regular"

        # Strong audio + visual = almost certainly an action highlight
        if audio_score > 0.6 and scene_score > 0.5:
            highlight_type = "likely-highlight"
            result_tags.append("high-action")
        elif gunfire_segments >= 2 and scene_score > 0.4:
            highlight_type = "multi-kill"
            result_tags.append("multi-engagement")
        elif scene_score > 0.7 and folder_bonus > 0:
            highlight_type = "likely-highlight"
            result_tags.append("high-action")
        elif scene_score > 0.5:
            highlight_type = "action"
            result_tags.append("action")
        elif audio_score > 0.5:
            highlight_type = "action"
            result_tags.append("audio-action")
        elif folder_bonus >= 1.5:
            highlight_type = "curated"
            result_tags.append("curated")

        # Extra tags from audio/motion signals
        if gunfire_segments > 0:
            result_tags.append("gunfire-detected")
        if motion_score > 0.7:
            result_tags.append("high-motion")

        # --- Build summary ---
        parts = []
        if scene_count > 0:
            parts.append(f"{scene_count} scene changes")
        if audio_score > 0:
            parts.append(f"audio {audio_score:.0%}")
        if gunfire_segments > 0:
            parts.append(f"{gunfire_segments} gunfire segment{'s' if gunfire_segments != 1 else ''}")
        if motion_score > 0.3:
            parts.append(f"motion {motion_score:.0%}")
        if folder_bonus > 0:
            parts.append("curated folder")
        if tag_bonus > 0:
            parts.append(f"tagged: {', '.join(tags[:3])}")
        if duration:
            parts.append(f"{duration:.0f}s")

        summary = "; ".join(parts) if parts else "No notable signals"

        return AnalysisResult(
            agent="ffmpeg-heuristic",
            map_name=None,
            tags=result_tags,
            summary=summary,
            confidence=normalized,
            score=score,
            kills=None,
            highlight_type=highlight_type if highlight_type != "regular" else None,
        )
