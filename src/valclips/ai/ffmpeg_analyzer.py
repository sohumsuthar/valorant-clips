"""Credit-free clip analyzer using FFmpeg heuristics.

Scores clips based on:
- Scene change density (more action = more cuts/flashes)
- Bitrate variation (action scenes spike bitrate in VBR)
- Curated folder signals (user already sorted best clips)
- Duration signals (shorter = likely already trimmed highlights)
- Tag signals (weapon/agent tags suggest noteworthy clips)
"""

import subprocess
import re
from pathlib import Path

from ..config import FFPROBE_TIMEOUT
from ..models import AnalysisResult
from .base import ClipAnalyzer


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


class FFmpegHeuristicAnalyzer(ClipAnalyzer):
    """Score clips using FFmpeg analysis + metadata heuristics. No API needed."""

    def __init__(self, quick: bool = False):
        """
        Args:
            quick: If True, skip slow FFmpeg analysis (scene/bitrate) and use metadata only.
        """
        self.quick = quick

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

        # Scene change analysis (the most informative signal)
        scene_score = 0.0
        scene_count = 0
        bitrate_score = 0.0

        if not self.quick:
            scene_score, scene_count = _get_scene_score(clip_path, duration)
            bitrate_score = _get_bitrate_variance(clip_path)

        # Metadata-based signals
        folder_bonus = _folder_bonus(directory)
        tag_bonus = _tag_bonus(tags)
        dur_signal = _duration_signal(duration)

        # Weighted combination
        if self.quick:
            # Metadata-only scoring
            raw = (
                folder_bonus * 3.0 +
                tag_bonus * 2.0 +
                dur_signal * 1.5
            )
            max_raw = 3.0 * 3.0 + 3.0 * 2.0 + 1.0 * 1.5  # 16.5
        else:
            # Full scoring with FFmpeg analysis
            raw = (
                scene_score * 4.0 +       # Most important: action density
                bitrate_score * 2.0 +      # Bitrate spikes during action
                folder_bonus * 2.5 +       # User curation is a strong signal
                tag_bonus * 1.5 +          # Tags indicate notable clips
                dur_signal * 1.0           # Duration sweet spot
            )
            max_raw = 4.0 + 2.0 + 2.5 * 2.0 + 1.5 * 3.0 + 1.0  # 16.5

        # Normalize to 1-10 scale
        normalized = raw / max_raw
        score = max(1, min(10, round(normalized * 9 + 1)))

        # Generate highlight type estimate
        highlight_type = "regular"
        if scene_score > 0.7 and folder_bonus > 0:
            highlight_type = "likely-highlight"
            result_tags.append("high-action")
        elif scene_score > 0.5:
            highlight_type = "action"
            result_tags.append("action")
        elif folder_bonus >= 1.5:
            highlight_type = "curated"
            result_tags.append("curated")

        # Build summary
        parts = []
        if scene_count > 0:
            parts.append(f"{scene_count} scene changes")
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
