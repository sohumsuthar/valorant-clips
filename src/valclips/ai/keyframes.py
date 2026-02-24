"""Extract evenly-spaced keyframes from a video for AI analysis."""

import subprocess
import tempfile
from pathlib import Path

from ..config import FFPROBE_TIMEOUT


def extract_keyframes(clip_path: str, n: int = 4) -> list[Path]:
    """Extract N evenly-spaced frames from a video file.

    Returns list of paths to temporary JPEG files.
    """
    # First get duration
    probe_cmd = [
        "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
        "-of", "csv=p=0", clip_path,
    ]
    try:
        result = subprocess.run(
            probe_cmd, capture_output=True, text=True, timeout=FFPROBE_TIMEOUT,
        )
        duration = float(result.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
        return []

    if duration <= 0:
        return []

    # Calculate timestamps (skip first and last 5%)
    start = duration * 0.05
    end = duration * 0.95
    step = (end - start) / max(n - 1, 1)
    timestamps = [start + step * i for i in range(n)]

    frames: list[Path] = []
    tmpdir = Path(tempfile.mkdtemp(prefix="valclips_frames_"))

    for i, ts in enumerate(timestamps):
        out = tmpdir / f"frame_{i:03d}.jpg"
        cmd = [
            "ffmpeg", "-y", "-ss", f"{ts:.2f}",
            "-i", clip_path,
            "-frames:v", "1",
            "-q:v", "2",
            str(out),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=FFPROBE_TIMEOUT)
            if out.exists():
                frames.append(out)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

    return frames
