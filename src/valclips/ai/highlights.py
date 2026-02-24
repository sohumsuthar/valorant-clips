"""Detect highlight moments within longer clip recordings.

ShadowPlay DVR recordings are typically 90 seconds long, but the actual
highlight play (ace, clutch, multi-kill) is usually only 10-30 seconds.
This module uses FFmpeg scene change detection and frame analysis to
identify the most interesting segments.
"""

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..config import FFPROBE_TIMEOUT


@dataclass
class Segment:
    """A detected segment within a clip."""
    start: float      # seconds
    end: float         # seconds
    score: float       # 0-1 activity score
    frame_path: Path | None = None  # representative frame


def detect_scene_changes(clip_path: str, threshold: float = 0.3) -> list[float]:
    """Detect scene change timestamps using FFmpeg.

    Returns list of timestamps (seconds) where significant visual changes occur.
    Lower threshold = more sensitive (more scene changes detected).
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "frame=pts_time,pict_type",
        "-select_streams", "v:0",
        "-of", "json",
        "-f", "lavfi",
        f"movie='{clip_path}',select='gt(scene,{threshold})'",
    ]

    # Alternative approach using ffmpeg filter that's more reliable
    cmd = [
        "ffmpeg", "-i", clip_path,
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-vsync", "vfr",
        "-f", "null", "-",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=FFPROBE_TIMEOUT * 2,
        )
        # Parse timestamps from showinfo output
        timestamps = []
        for line in result.stderr.split("\n"):
            if "showinfo" in line and "pts_time:" in line:
                parts = line.split("pts_time:")
                if len(parts) > 1:
                    try:
                        ts = float(parts[1].split()[0])
                        timestamps.append(ts)
                    except (ValueError, IndexError):
                        continue
        return timestamps
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def detect_activity_regions(
    clip_path: str,
    duration: float | None = None,
    window: float = 5.0,
) -> list[Segment]:
    """Identify high-activity regions by clustering scene changes.

    Groups scene changes into segments and scores them by density
    (more scene changes = more action).
    """
    scene_times = detect_scene_changes(clip_path, threshold=0.25)

    if not scene_times:
        # Fallback: return the whole clip as one segment
        if duration:
            return [Segment(start=0, end=duration, score=0.5)]
        return []

    if duration is None:
        duration = max(scene_times) + 5.0

    # Slide a window across the clip and count scene changes per window
    best_segments: list[Segment] = []
    step = window / 2

    t = 0.0
    while t < duration:
        window_end = min(t + window, duration)
        count = sum(1 for ts in scene_times if t <= ts < window_end)
        if count > 0:
            score = min(1.0, count / 5.0)  # Normalize: 5+ changes = max score
            best_segments.append(Segment(start=t, end=window_end, score=score))
        t += step

    # Merge overlapping high-score segments
    if not best_segments:
        return [Segment(start=0, end=duration, score=0.3)]

    merged = _merge_segments(best_segments, min_score=0.3)
    return sorted(merged, key=lambda s: s.score, reverse=True)


def _merge_segments(segments: list[Segment], min_score: float = 0.3) -> list[Segment]:
    """Merge overlapping or adjacent segments, keeping the best scores."""
    filtered = [s for s in segments if s.score >= min_score]
    if not filtered:
        return segments[:1] if segments else []

    filtered.sort(key=lambda s: s.start)
    merged = [filtered[0]]

    for seg in filtered[1:]:
        prev = merged[-1]
        if seg.start <= prev.end + 1.0:  # Allow 1s gap for merging
            merged[-1] = Segment(
                start=prev.start,
                end=max(prev.end, seg.end),
                score=max(prev.score, seg.score),
            )
        else:
            merged.append(seg)

    return merged


def extract_highlight_frames(
    clip_path: str,
    segments: list[Segment],
    frames_per_segment: int = 3,
    max_frames: int = 12,
) -> list[Path]:
    """Extract representative frames from the highest-scoring segments.

    Takes frames from the most active segments to give the AI analyzer
    the best material to work with.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="valclips_highlights_"))
    frames: list[Path] = []

    # Sort by score descending, take top segments
    top_segments = sorted(segments, key=lambda s: s.score, reverse=True)

    for seg in top_segments:
        if len(frames) >= max_frames:
            break

        seg_duration = seg.end - seg.start
        for i in range(frames_per_segment):
            if len(frames) >= max_frames:
                break

            # Spread frames across the segment
            offset = seg_duration * (i + 1) / (frames_per_segment + 1)
            timestamp = seg.start + offset

            out = tmpdir / f"highlight_{len(frames):03d}_{timestamp:.1f}s.jpg"
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{timestamp:.2f}",
                "-i", clip_path,
                "-frames:v", "1",
                "-q:v", "2",
                str(out),
            ]

            try:
                subprocess.run(cmd, capture_output=True, timeout=FFPROBE_TIMEOUT)
                if out.exists() and out.stat().st_size > 0:
                    frames.append(out)
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue

    return frames


def get_clip_highlights(
    clip_path: str,
    duration: float | None = None,
    max_frames: int = 8,
) -> tuple[list[Segment], list[Path]]:
    """Full pipeline: detect activity regions, extract best frames.

    Returns (segments, frame_paths).
    """
    segments = detect_activity_regions(clip_path, duration=duration)
    frames = extract_highlight_frames(clip_path, segments, max_frames=max_frames)
    return segments, frames
