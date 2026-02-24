"""Parse Valorant clip filenames into metadata."""

import re
from datetime import datetime
from pathlib import Path


def parse_filename(filename: str) -> dict:
    """Extract date and metadata from a ShadowPlay/replay filename.

    Returns dict with keys: recorded_at, clip_sequence, source_format.
    Values are None if the filename doesn't match known patterns.
    """
    # Valorant YYYY.MM.DD - HH.MM.SS.NN.DVR.mp4
    m = re.match(
        r"^Valorant\s+(\d{4})\.(\d{2})\.(\d{2})\s*-\s*(\d{2})\.(\d{2})\.(\d{2})\.(\d{2})\.DVR\.mp4$",
        filename,
        re.IGNORECASE,
    )
    if m:
        y, mo, d, h, mi, s, seq = m.groups()
        return {
            "recorded_at": datetime(int(y), int(mo), int(d), int(h), int(mi), int(s)),
            "clip_sequence": int(seq),
            "source_format": "dvr",
        }

    # VALORANT_replay_YYYY.MM.DD-HH.MM.mp4
    m = re.match(
        r"^VALORANT_replay_(\d{4})\.(\d{2})\.(\d{2})-(\d{2})\.(\d{2})\.mp4$",
        filename,
        re.IGNORECASE,
    )
    if m:
        y, mo, d, h, mi = m.groups()
        return {
            "recorded_at": datetime(int(y), int(mo), int(d), int(h), int(mi)),
            "clip_sequence": None,
            "source_format": "replay",
        }

    # Unknown format -- still index it
    return {
        "recorded_at": None,
        "clip_sequence": None,
        "source_format": "unknown",
    }


def extract_share_name(file_path: str, share_roots: dict[str, object]) -> str | None:
    """Determine which share a file belongs to by checking path prefixes."""
    for name, root in share_roots.items():
        root_str = str(root)
        if file_path.startswith(root_str):
            return name
    return None


def extract_directory(file_path: str) -> str:
    """Get the parent directory. Works with both / and \\ separators."""
    return str(Path(file_path).parent)


def is_valorant_clip(filename: str) -> bool:
    """Check if a filename looks like a Valorant clip (any format)."""
    lower = filename.lower()
    return lower.endswith(".mp4") and ("valorant" in lower)
