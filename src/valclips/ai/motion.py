"""Motion and visual activity analysis for Valorant gameplay clips.

Uses FFmpeg/FFprobe to detect motion intensity from frame-level data:
- Frame size variance: I-frames during action are much larger than calm moments
- Fast aim detection: Large frame-to-frame pixel differences indicate flicks
"""

from __future__ import annotations

import logging
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from ..config import FFPROBE_TIMEOUT

logger = logging.getLogger(__name__)

MOTION_TIMEOUT = FFPROBE_TIMEOUT * 3  # 90s for full-clip analysis


def _get_frame_sizes(clip_path: str) -> list[tuple[float, int]]:
    """Extract per-frame packet sizes and timestamps via ffprobe.

    Returns list of (pts_time, pkt_size) tuples.
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-select_streams", "v:0",
        "-show_entries", "frame=pkt_size,pts_time",
        "-of", "csv=p=0",
        clip_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=MOTION_TIMEOUT,
        )
        frames = []
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) >= 2:
                try:
                    pts = float(parts[0]) if parts[0] != "N/A" else 0.0
                    size = int(parts[1])
                    frames.append((pts, size))
                except (ValueError, IndexError):
                    continue
        return frames
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def _get_duration(clip_path: str) -> float | None:
    """Get clip duration via ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        clip_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=FFPROBE_TIMEOUT,
        )
        val = result.stdout.strip()
        if val and val != "N/A":
            return float(val)
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        return None


def estimate_motion_intensity(
    clip_path: str,
    duration: float | None = None,
) -> dict:
    """Estimate motion intensity from frame-level packet sizes.

    I-frames during high action are significantly larger due to more
    visual complexity. High variance in frame sizes = mix of action/calm.

    Returns dict with:
        motion_score (float): 0-1 normalized score
        static_ratio (float): fraction of frames below 40% of mean size
        peak_motion_times (list[float]): timestamps with highest motion
    """
    frames = _get_frame_sizes(clip_path)
    if len(frames) < 10:
        return {"motion_score": 0.0, "static_ratio": 1.0, "peak_motion_times": []}

    sizes = [s for _, s in frames]
    mean = sum(sizes) / len(sizes)
    if mean == 0:
        return {"motion_score": 0.0, "static_ratio": 1.0, "peak_motion_times": []}

    # Coefficient of variation of frame sizes
    variance = sum((s - mean) ** 2 for s in sizes) / len(sizes)
    std_dev = variance ** 0.5
    cv = std_dev / mean

    # Static ratio: frames below 40% of mean are "calm"
    static_threshold = mean * 0.4
    static_count = sum(1 for s in sizes if s < static_threshold)
    static_ratio = static_count / len(sizes)

    # Peak motion: frames exceeding mean + 1.5 * std_dev
    peak_threshold = mean + 1.5 * std_dev
    peak_times = [t for t, s in frames if s > peak_threshold]

    # Cluster nearby peak times (within 1s)
    clustered = _cluster_timestamps(peak_times, gap=1.0)

    # Score: CV-based, higher CV = more motion variation
    motion_score = min(1.0, cv / 1.5)

    return {
        "motion_score": round(motion_score, 4),
        "static_ratio": round(static_ratio, 4),
        "peak_motion_times": clustered[:10],  # Top 10 peak clusters
    }


def detect_fast_aim_movements(
    clip_path: str,
    sample_count: int = 20,
) -> dict:
    """Detect fast aim movements (flicks) by comparing frame pixel diffs.

    Extracts small thumbnail frames at regular intervals and computes
    mean absolute pixel difference between consecutive frames. Large
    sudden differences indicate flicks or fast crosshair movements.

    Returns dict with:
        flick_score (float): 0-1 (higher = more fast movements)
        flick_count (int): number of detected flick-like movements
        flick_timestamps (list[float]): approximate timestamps of flicks
    """
    duration = _get_duration(clip_path)
    if not duration or duration <= 0:
        return {"flick_score": 0.0, "flick_count": 0, "flick_timestamps": []}

    # Skip first/last 5% of clip
    start = duration * 0.05
    end = duration * 0.95
    usable = end - start
    if usable <= 0 or sample_count < 3:
        return {"flick_score": 0.0, "flick_count": 0, "flick_timestamps": []}

    interval = usable / sample_count
    timestamps = [start + i * interval for i in range(sample_count)]

    # Extract small BMP frames to temp dir
    tmp_dir = tempfile.mkdtemp(prefix="valclips_motion_")
    try:
        frame_paths = _extract_sample_frames(clip_path, timestamps, tmp_dir)
        if len(frame_paths) < 3:
            return {"flick_score": 0.0, "flick_count": 0, "flick_timestamps": []}

        # Compute frame-to-frame differences
        diffs = _compute_frame_diffs(frame_paths)
        if not diffs:
            return {"flick_score": 0.0, "flick_count": 0, "flick_timestamps": []}

        # Detect spikes
        mean_diff = sum(diffs) / len(diffs)
        sorted_diffs = sorted(diffs)
        median_diff = sorted_diffs[len(sorted_diffs) // 2]
        threshold = max(median_diff * 3, mean_diff * 2, 15.0)

        flick_indices = [i for i, d in enumerate(diffs) if d > threshold]
        flick_count = len(flick_indices)

        # Map indices back to timestamps
        flick_timestamps = []
        for idx in flick_indices:
            if idx + 1 < len(timestamps):
                flick_timestamps.append(round(timestamps[idx + 1], 2))

        # Score: ratio of flicks to total transitions, capped at 1.0
        max_expected_flicks = max(1, sample_count // 4)
        flick_score = min(1.0, flick_count / max_expected_flicks)

        return {
            "flick_score": round(flick_score, 4),
            "flick_count": flick_count,
            "flick_timestamps": flick_timestamps,
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _extract_sample_frames(
    clip_path: str,
    timestamps: list[float],
    output_dir: str,
) -> list[Path]:
    """Extract small BMP frames at specified timestamps."""
    paths = []
    for i, ts in enumerate(timestamps):
        out_path = Path(output_dir) / f"frame_{i:04d}.bmp"
        cmd = [
            "ffmpeg", "-v", "quiet",
            "-ss", str(ts),
            "-i", clip_path,
            "-frames:v", "1",
            "-vf", "scale=160:90",
            "-f", "image2",
            str(out_path),
        ]
        try:
            subprocess.run(cmd, timeout=10, capture_output=True)
            if out_path.exists() and out_path.stat().st_size > 0:
                paths.append(out_path)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue
    return paths


def _read_bmp_pixels(path: Path) -> bytes | None:
    """Read raw pixel data from a BMP file."""
    try:
        data = path.read_bytes()
        if len(data) < 54:
            return None
        # BMP header: offset to pixel data at bytes 10-13
        offset = struct.unpack_from("<I", data, 10)[0]
        if offset >= len(data):
            return None
        return data[offset:]
    except (OSError, struct.error):
        return None


def _compute_frame_diffs(frame_paths: list[Path]) -> list[float]:
    """Compute mean absolute pixel difference between consecutive frames."""
    diffs = []
    prev_pixels = None
    for path in frame_paths:
        pixels = _read_bmp_pixels(path)
        if pixels is None:
            prev_pixels = None
            continue
        if prev_pixels is not None:
            # Compare same-length regions
            length = min(len(prev_pixels), len(pixels))
            if length > 0:
                total_diff = sum(
                    abs(pixels[j] - prev_pixels[j])
                    for j in range(0, length, 3)  # Sample every 3rd byte for speed
                )
                sample_count = length // 3
                mad = total_diff / sample_count if sample_count > 0 else 0.0
                diffs.append(mad)
        prev_pixels = pixels
    return diffs


def _cluster_timestamps(timestamps: list[float], gap: float = 1.0) -> list[float]:
    """Cluster nearby timestamps and return cluster midpoints."""
    if not timestamps:
        return []

    sorted_ts = sorted(timestamps)
    clusters: list[list[float]] = [[sorted_ts[0]]]

    for ts in sorted_ts[1:]:
        if ts - clusters[-1][-1] <= gap:
            clusters[-1].append(ts)
        else:
            clusters.append([ts])

    return [round(sum(c) / len(c), 2) for c in clusters]
