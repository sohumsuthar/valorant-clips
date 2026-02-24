"""Extract highlight segments from long DVR recordings.

Uses FFmpeg scene change detection to find the most action-packed
segment within a 90-120s DVR recording and exports just that portion
as a new trimmed clip.
"""

import subprocess
from pathlib import Path

from .ai.highlights import detect_activity_regions, Segment
from .config import FFPROBE_TIMEOUT


def find_best_segment(
    clip_path: str,
    duration: float | None = None,
    min_length: float = 8.0,
    max_length: float = 35.0,
    padding: float = 2.0,
) -> Segment | None:
    """Find the best highlight segment within a clip.

    Returns a Segment with start/end times, or None if no good segment found.
    """
    segments = detect_activity_regions(clip_path, duration=duration)
    if not segments:
        return None

    # Get the highest-scoring segment
    best = max(segments, key=lambda s: s.score)

    # Add padding
    start = max(0.0, best.start - padding)
    end = best.end + padding
    if duration:
        end = min(end, duration)

    seg_len = end - start

    # Enforce min/max length
    if seg_len < min_length:
        # Expand around the center
        center = (start + end) / 2
        start = max(0.0, center - min_length / 2)
        end = start + min_length
        if duration and end > duration:
            end = duration
            start = max(0.0, end - min_length)

    if seg_len > max_length:
        # Find the densest sub-window
        # For now just take the first max_length seconds
        end = start + max_length

    return Segment(start=start, end=end, score=best.score)


def extract_segment(
    clip_path: str,
    output_path: str,
    start: float,
    end: float,
    reencode: bool = False,
) -> bool:
    """Extract a segment from a clip using FFmpeg.

    Args:
        clip_path: Source video path.
        output_path: Destination path for the trimmed clip.
        start: Start time in seconds.
        end: End time in seconds.
        reencode: If True, re-encode for precise cuts. If False, use
                  stream copy (fast but may have keyframe imprecision).

    Returns True if successful.
    """
    duration = end - start

    if reencode:
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start:.2f}",
            "-i", clip_path,
            "-t", f"{duration:.2f}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            output_path,
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start:.2f}",
            "-i", clip_path,
            "-t", f"{duration:.2f}",
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            output_path,
        ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=FFPROBE_TIMEOUT * 4,
        )
        return result.returncode == 0 and Path(output_path).exists()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def extract_highlight(
    clip_path: str,
    output_dir: str | None = None,
    suffix: str = "_highlight",
    reencode: bool = False,
    duration: float | None = None,
) -> tuple[str | None, Segment | None]:
    """Full pipeline: detect best segment and extract it.

    Returns (output_path, segment) or (None, None) on failure.
    """
    seg = find_best_segment(clip_path, duration=duration)
    if not seg:
        return None, None

    src = Path(clip_path)
    if output_dir:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = src.parent

    out_name = f"{src.stem}{suffix}{src.suffix}"
    out_path = str(out_dir / out_name)

    ok = extract_segment(clip_path, out_path, seg.start, seg.end, reencode=reencode)
    if ok:
        return out_path, seg
    return None, None
