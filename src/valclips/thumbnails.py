"""FFmpeg thumbnail extraction with hash-based naming."""

import hashlib
import subprocess
from pathlib import Path

from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

from .config import THUMBNAIL_DIR, FFPROBE_TIMEOUT
from .db import get_connection, update_clip_thumbnail
from .models import Clip


def _thumb_path(file_path: str) -> Path:
    """Generate a deterministic thumbnail filename from the clip path."""
    h = hashlib.sha256(file_path.encode()).hexdigest()[:16]
    return THUMBNAIL_DIR / f"{h}.jpg"


def _pick_timestamp(clip: Clip) -> str:
    """Choose a seek timestamp that's within the clip's duration.

    For longer clips, pick ~40% through (more likely to catch action
    than the default 5s which is often still in buy phase).
    """
    dur = clip.duration_seconds or 0
    if dur <= 0:
        return "1"
    if dur < 5:
        return f"{dur * 0.1:.2f}"
    if dur > 30:
        # For DVR clips, seek to ~40% (usually mid-action)
        return f"{dur * 0.4:.2f}"
    return "5"


def _generate_one(clip: Clip) -> str | None:
    """Extract a single frame as a JPEG thumbnail. Returns path or None."""
    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
    out = _thumb_path(clip.file_path)

    if out.exists():
        return str(out)

    ts = _pick_timestamp(clip)
    cmd = [
        "ffmpeg", "-y", "-ss", ts,
        "-i", clip.file_path,
        "-frames:v", "1",
        "-vf", "scale=320:-1",
        "-q:v", "5",
        str(out),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=FFPROBE_TIMEOUT,
        )
        if result.returncode == 0 and out.exists():
            return str(out)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return None


def _preview_path(file_path: str) -> Path:
    """Generate a deterministic preview filename from the clip path."""
    h = hashlib.sha256(file_path.encode()).hexdigest()[:16]
    return THUMBNAIL_DIR / f"{h}_preview.webp"


def _generate_preview(clip: Clip, duration: float = 3.0, fps: int = 8) -> str | None:
    """Generate a short animated WebP preview from the clip.

    Extracts a ~3s segment at 8fps from the most interesting part
    (40% through for DVR clips) as a 320px wide animated WebP.
    """
    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
    out = _preview_path(clip.file_path)

    if out.exists() and out.stat().st_size > 1000:
        return str(out)

    # Pick start timestamp (same logic as thumbnails, but we need a range)
    clip_dur = clip.duration_seconds or 0
    if clip_dur <= 0:
        return None
    if clip_dur < duration + 1:
        start = 0
    elif clip_dur > 30:
        start = clip_dur * 0.4
    else:
        start = max(0, clip_dur * 0.3)

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.2f}",
        "-i", clip.file_path,
        "-t", f"{duration:.1f}",
        "-vf", f"fps={fps},scale=320:-1:flags=lanczos",
        "-vcodec", "libwebp",
        "-lossless", "0",
        "-compression_level", "4",
        "-q:v", "50",
        "-loop", "0",
        "-an",
        str(out),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=FFPROBE_TIMEOUT * 3,
        )
        if result.returncode == 0 and out.exists() and out.stat().st_size > 1000:
            return str(out)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Clean up failed output
    if out.exists():
        out.unlink()
    return None


def generate_previews(clips: list[Clip]):
    """Generate animated WebP previews for a list of clips."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
    ) as progress:
        task = progress.add_task("Generating previews...", total=len(clips))
        ok = 0
        for clip in clips:
            progress.update(task, advance=1)
            if _generate_preview(clip):
                ok += 1
    return ok


def generate_thumbnails(clips: list[Clip]):
    """Generate thumbnails for a list of clips, updating the DB."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
    ) as progress:
        task = progress.add_task("Generating thumbnails...", total=len(clips))

        for clip in clips:
            progress.update(task, advance=1)
            thumb = _generate_one(clip)
            if thumb:
                with get_connection() as conn:
                    update_clip_thumbnail(conn, clip.id, thumb)
