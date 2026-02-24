"""Local Valorant clip analyzer using computer vision.

No API needed. Uses OpenCV + pixel analysis of Valorant's HUD to detect:
- Kill count (from kill feed red bands AND yellow kill banner markers)
- Clutch situations (from alive player indicators at top)
- Round outcome (win/loss from green/red text at center)
- Whether it's actual gameplay vs menus/loading
- Map identification (color palette matching)
- Scene intensity (action vs idle)

Valorant HUD layout (1920x1080):
- Kill feed: top-right (~1275-1900, 96-496) -- red text, stacked entries
- Round score: top-center (left: 809-837, right: 1085-1113, y: 31-63)
- Alive indicators: left team x=442-706, right team x=1169-1433, y=28-72
- Minimap: top-left (~30-300, 30-300)
- Abilities: bottom-center (~760-1160, 980-1070)
- Health/shield: bottom-left (~100-500, 1020-1060)
- Crosshair: center (~920-1000, 500-580)
- Kill banner: bottom-right area with yellow marker (HSV H=26-32, S=106-135)
- Round outcome text: center screen (~660-1260, 440-640) -- green=win, red=loss
"""

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .base import ClipAnalyzer
from ..models import AnalysisResult


@dataclass
class FrameAnalysis:
    """Analysis results from a single frame."""
    is_gameplay: bool = False
    is_landscape: bool = True
    kill_banner_visible: bool = False
    kill_feed_entries: int = 0
    has_crosshair: bool = False
    has_minimap: bool = False
    has_abilities_hud: bool = False
    has_health_bar: bool = False
    scene_intensity: float = 0.0
    dominant_colors: list = field(default_factory=list)
    red_intensity: float = 0.0
    # New detections
    allies_alive: int = 0       # 0-5, from top bar indicators
    enemies_alive: int = 0      # 0-5, from top bar indicators
    round_outcome: str | None = None  # "win", "loss", or None
    yellow_banner_hits: int = 0  # Yellow kill confirmation markers
    has_round_end_text: bool = False


def _count_alive_players(img: np.ndarray, sx: float, sy: float, side: str) -> int:
    """Count alive players from top-bar agent portrait icons.

    Alive agents have visible portrait icons (bright pixels > 100).
    Dead agents have dimmed slots (brightness near 0).
    Uses brightness ratio with threshold calibrated from real clips:
      - Dead slots: bright ratio < 0.06
      - Alive slots: bright ratio > 0.27
      - Threshold: 0.20

    Args:
        side: "left" for your team, "right" for enemy team
    """
    if side == "left":
        x_start = int(442 * sx)
        x_end = int(706 * sx)
    else:
        x_start = int(1169 * sx)
        x_end = int(1433 * sx)

    y_top = int(28 * sy)
    y_bot = int(72 * sy)

    region = img[y_top:y_bot, x_start:x_end]
    if region.size == 0:
        return 0

    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    slot_width = (x_end - x_start) / 5

    alive = 0
    for i in range(5):
        x1 = int(i * slot_width)
        x2 = int((i + 1) * slot_width)
        slot = gray[:, x1:x2]
        if slot.size == 0:
            continue
        bright_pixels = np.sum(slot > 100) / slot.size
        if bright_pixels > 0.20:
            alive += 1

    return alive


def _detect_round_outcome(img: np.ndarray, sx: float, sy: float) -> str | None:
    """Detect round outcome text at center screen.

    "Attackers win!" / "Defenders win!" -- large colored text center screen.
    Green tint = your team won, red tint = your team lost.
    Also detect "ROUND WON" (green) or "ROUND LOST" (red).
    """
    # Center screen region where round outcome text appears
    y1, y2 = int(400 * sy), int(650 * sy)
    x1, x2 = int(560 * sx), int(1360 * sx)
    region = img[y1:y2, x1:x2]
    if region.size == 0:
        return None

    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)

    # Check for bright green text (round won)
    green_mask = cv2.inRange(hsv, (40, 60, 150), (80, 255, 255))
    green_ratio = np.sum(green_mask > 0) / green_mask.size

    # Check for bright red text (round lost)
    red_mask1 = cv2.inRange(hsv, (0, 60, 150), (10, 255, 255))
    red_mask2 = cv2.inRange(hsv, (170, 60, 150), (180, 255, 255))
    red_mask = red_mask1 | red_mask2
    red_ratio = np.sum(red_mask > 0) / red_mask.size

    # Also check for large bright white text (generic round end)
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    white_ratio = np.sum(gray > 220) / gray.size

    if green_ratio > 0.01 and white_ratio > 0.02:
        return "win"
    if red_ratio > 0.01 and white_ratio > 0.02:
        return "loss"

    return None


def _detect_yellow_kill_banner(img: np.ndarray, sx: float, sy: float) -> int:
    """Detect yellow kill confirmation markers.

    When you get a kill, a yellow marker appears in the kill banner area.
    HSV range: H=26-32, S=106-135, V=any (from valorant-timestamp-finder).
    """
    # Kill banner area: roughly bottom portion of screen, wider area
    y1, y2 = int(800 * sy), int(1000 * sy)
    x1, x2 = int(600 * sx), int(1400 * sx)
    region = img[y1:y2, x1:x2]
    if region.size == 0:
        return 0

    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    # Yellow kill marker
    yellow_mask = cv2.inRange(hsv, (22, 90, 100), (35, 200, 255))
    yellow_sum = np.sum(yellow_mask > 0)

    # Threshold: significant yellow presence = kill confirmation
    if yellow_sum > 500:
        return 1
    return 0


def _analyze_frame(img: np.ndarray) -> FrameAnalysis:
    """Analyze a single frame for Valorant HUD elements."""
    h, w = img.shape[:2]
    result = FrameAnalysis()

    # Reject non-landscape frames (replays are often portrait)
    aspect = w / h
    if aspect < 1.3:
        result.is_landscape = False
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        result.scene_intensity = min(1.0, np.sum(edges > 0) / edges.size * 10)
        return result

    sx, sy = w / 1920, h / 1080

    # --- Gameplay indicators ---

    # 1. Minimap region (top-left)
    minimap = img[int(30*sy):int(280*sy), int(30*sx):int(280*sx)]
    minimap_hsv = cv2.cvtColor(minimap, cv2.COLOR_BGR2HSV)
    dark_pixels = np.sum(minimap_hsv[:, :, 2] < 60) / minimap.size * 3
    if dark_pixels > 0.3:
        green_mask = cv2.inRange(minimap_hsv, (35, 50, 50), (85, 255, 255))
        green_ratio = np.sum(green_mask > 0) / green_mask.size
        if green_ratio > 0.01:
            result.has_minimap = True

    # 2. Crosshair region (center)
    cx, cy = int(w / 2), int(h / 2)
    cross_region = img[cy-20:cy+20, cx-20:cx+20]
    if cross_region.size > 0:
        cross_gray = cv2.cvtColor(cross_region, cv2.COLOR_BGR2GRAY)
        bright = np.sum(cross_gray > 200) / cross_gray.size
        if 0.01 < bright < 0.25:
            result.has_crosshair = True

    # 3. Health bar (bottom-left)
    health_region = img[int(1010*sy):int(1060*sy), int(100*sx):int(500*sx)]
    health_hsv = cv2.cvtColor(health_region, cv2.COLOR_BGR2HSV)
    green_mask = cv2.inRange(health_hsv, (35, 80, 80), (85, 255, 255))
    blue_mask = cv2.inRange(health_hsv, (90, 80, 80), (130, 255, 255))
    if np.sum(green_mask > 0) / green_mask.size > 0.02 or \
       np.sum(blue_mask > 0) / blue_mask.size > 0.02:
        result.has_health_bar = True

    # 4. Ability HUD (bottom-center)
    ability_region = img[int(980*sy):int(1070*sy), int(760*sx):int(1160*sx)]
    ability_gray = cv2.cvtColor(ability_region, cv2.COLOR_BGR2GRAY)
    if np.std(ability_gray) > 35:
        result.has_abilities_hud = True

    # Gameplay = 2+ HUD elements
    gameplay_signals = sum([
        result.has_minimap,
        result.has_crosshair,
        result.has_health_bar,
        result.has_abilities_hud,
    ])
    result.is_gameplay = gameplay_signals >= 2

    # --- Kill detection ---

    # 5. Kill feed (top-right) -- red bands
    kill_feed = img[int(50*sy):int(350*sy), int(1300*sx):int(w)]
    kf_hsv = cv2.cvtColor(kill_feed, cv2.COLOR_BGR2HSV)
    red_mask = cv2.inRange(kf_hsv, (0, 120, 120), (8, 255, 255))
    red_mask2 = cv2.inRange(kf_hsv, (172, 120, 120), (180, 255, 255))
    red_combined = red_mask | red_mask2
    result.red_intensity = np.sum(red_combined > 0) / red_combined.size

    if result.red_intensity > 0.001 and result.is_gameplay:
        red_rows = np.any(red_combined > 0, axis=1)
        entries = 0
        in_entry = False
        gap = 0
        for row_has_red in red_rows:
            if row_has_red:
                if not in_entry:
                    entries += 1
                    in_entry = True
                gap = 0
            else:
                gap += 1
                if gap > 10:
                    in_entry = False
        result.kill_feed_entries = min(entries, 6)

    # 6. Kill banner -- bright white text center screen
    if result.is_gameplay:
        banner_region = img[int(350*sy):int(500*sy), int(600*sx):int(1300*sx)]
        banner_gray = cv2.cvtColor(banner_region, cv2.COLOR_BGR2GRAY)
        bright_pixels = np.sum(banner_gray > 230) / banner_gray.size
        if bright_pixels > 0.05:
            result.kill_banner_visible = True

    # 7. Yellow kill confirmation marker
    if result.is_gameplay:
        result.yellow_banner_hits = _detect_yellow_kill_banner(img, sx, sy)

    # --- Clutch detection (alive player count) ---
    if result.is_gameplay:
        result.allies_alive = _count_alive_players(img, sx, sy, "left")
        result.enemies_alive = _count_alive_players(img, sx, sy, "right")

    # --- Round outcome ---
    result.round_outcome = _detect_round_outcome(img, sx, sy)
    if result.round_outcome:
        result.has_round_end_text = True

    # --- Scene intensity ---
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    result.scene_intensity = min(1.0, np.sum(edges > 0) / edges.size * 10)

    # --- Dominant colors for map ---
    if result.is_gameplay:
        gameplay_area = img[int(100*sy):int(900*sy), int(300*sx):int(1600*sx)]
        ga_hsv = cv2.cvtColor(gameplay_area, cv2.COLOR_BGR2HSV)
        result.dominant_colors = [
            float(np.mean(ga_hsv[:, :, 0])),
            float(np.mean(ga_hsv[:, :, 1])),
            float(np.mean(ga_hsv[:, :, 2])),
        ]

    return result


def _guess_map(analyses: list[FrameAnalysis]) -> str | None:
    """Guess the map from color analysis of gameplay frames."""
    gameplay = [a for a in analyses if a.is_gameplay and a.dominant_colors]
    if not gameplay:
        return None

    avg_hue = np.mean([a.dominant_colors[0] for a in gameplay])
    avg_sat = np.mean([a.dominant_colors[1] for a in gameplay])
    avg_val = np.mean([a.dominant_colors[2] for a in gameplay])

    if avg_val < 50:
        return "abyss"
    if avg_hue > 15 and avg_hue < 30 and avg_sat > 60:
        return "sunset"
    if avg_hue > 90 and avg_hue < 120 and avg_sat > 40:
        return "icebox"
    if avg_hue > 35 and avg_hue < 75 and avg_sat > 50:
        return "haven"
    if avg_hue > 10 and avg_hue < 25 and avg_sat > 30:
        return "bind"

    return None


def _estimate_kills(analyses: list[FrameAnalysis]) -> int:
    """Estimate kill count from gameplay frame analyses."""
    gameplay = [a for a in analyses if a.is_gameplay]
    if not gameplay:
        return 0

    # Kill feed red band count (most reliable)
    max_feed = max((a.kill_feed_entries for a in gameplay), default=0)

    # Yellow kill banner hits across frames
    yellow_hits = sum(a.yellow_banner_hits for a in gameplay)

    # Kill banners (white text center) -- less reliable
    banner_count = sum(1 for a in gameplay if a.kill_banner_visible)

    # Primary: kill feed. Secondary: yellow markers. Tertiary: banners.
    return max(max_feed, yellow_hits, min(banner_count, 3))


def _detect_clutch(analyses: list[FrameAnalysis], total_kills: int) -> str | None:
    """Detect clutch situations from alive player counts.

    A clutch is when you're the last alive on your team (1 ally)
    against 2+ enemies, and you get enough kills to plausibly win.

    Validation:
    - Need at least 2 frames showing the clutch situation (not just noise)
    - Kill count must be >= enemies - 1 (you need to actually win the clutch)
    """
    gameplay = [a for a in analyses if a.is_gameplay]
    if not gameplay:
        return None

    # Look for frames where allies=1 and enemies >= 2
    clutch_frames = [
        a for a in gameplay
        if a.allies_alive == 1 and a.enemies_alive >= 2
    ]

    # Require at least 2 frames confirming the clutch (not just a single-frame blip)
    if len(clutch_frames) < 2:
        return None

    # Get the maximum enemies alive during the clutch
    max_enemies = max(a.enemies_alive for a in clutch_frames)

    # Kill count must be plausible for the clutch size:
    # To win a 1vN, you need at least N-1 kills (could win by spike timer/defuse)
    min_kills_needed = max_enemies - 1
    if total_kills < min_kills_needed:
        return None

    # Cap max_enemies at 5 (can't be more than a full team)
    max_enemies = min(max_enemies, 5)

    return f"1v{max_enemies}"


def _detect_round_outcome_aggregate(analyses: list[FrameAnalysis]) -> str | None:
    """Aggregate round outcome from multiple frames."""
    wins = sum(1 for a in analyses if a.round_outcome == "win")
    losses = sum(1 for a in analyses if a.round_outcome == "loss")

    if wins > losses and wins > 0:
        return "win"
    if losses > wins and losses > 0:
        return "loss"
    return None


class LocalAnalyzer(ClipAnalyzer):
    """Analyze Valorant clips using local computer vision.

    No API keys needed. Uses OpenCV to analyze the Valorant HUD.
    Detects: kills, clutches, aces, round outcomes, maps, gameplay vs menus.
    """

    def __init__(self, num_frames: int = 8):
        self.num_frames = num_frames
        self.max_frames = num_frames

    def analyze(self, clip_path: str, keyframes: list[Path]) -> AnalysisResult:
        """Analyze a clip using local CV."""
        if not keyframes:
            return AnalysisResult(
                agent="local-cv",
                summary="No frames available",
                score=1,
                confidence=0.0,
            )

        analyses: list[FrameAnalysis] = []
        for frame_path in keyframes[:self.num_frames]:
            try:
                img = cv2.imread(str(frame_path))
                if img is None:
                    continue
                fa = _analyze_frame(img)
                analyses.append(fa)
            except Exception:
                continue

        if not analyses:
            return AnalysisResult(
                agent="local-cv",
                summary="Could not analyze frames",
                score=1,
                confidence=0.0,
            )

        # Portrait/non-landscape clips
        landscape_count = sum(1 for a in analyses if a.is_landscape)
        if landscape_count < len(analyses) / 2:
            avg_intensity = np.mean([a.scene_intensity for a in analyses])
            return AnalysisResult(
                agent="local-cv",
                summary="Valorant replay/portrait clip",
                score=3 if avg_intensity > 0.3 else 2,
                highlight_type="regular-round",
                confidence=0.2,
            )

        # Gameplay detection
        gameplay_frames = sum(1 for a in analyses if a.is_gameplay)
        gameplay_ratio = gameplay_frames / len(analyses)
        is_gameplay = gameplay_ratio > 0.3

        kills = _estimate_kills(analyses) if is_gameplay else 0
        map_name = _guess_map(analyses) if is_gameplay else None
        clutch_type = _detect_clutch(analyses, kills) if is_gameplay else None
        round_outcome = _detect_round_outcome_aggregate(analyses)

        if not is_gameplay:
            return AnalysisResult(
                agent="local-cv",
                summary="Non-gameplay content (menus, loading, or non-Valorant)",
                score=1,
                highlight_type="non-gameplay",
                confidence=0.6,
            )

        avg_intensity = np.mean([a.scene_intensity for a in analyses])

        # --- Classify highlight type and score ---
        is_ace = kills >= 5

        if is_ace:
            score = 10 if clutch_type else 9
            highlight_type = "ace"
        elif clutch_type:
            # Clutch scoring based on difficulty
            clutch_n = int(clutch_type[2:]) if clutch_type else 0
            if clutch_n >= 4:
                score = 9
            elif clutch_n >= 3:
                score = 8
            elif clutch_n >= 2:
                score = 7
            else:
                score = 6
            highlight_type = "clutch"
            # Bonus for clutch with many kills
            if kills >= 3:
                score = min(10, score + 1)
        elif kills >= 4:
            score = 8
            highlight_type = "multi-kill"
        elif kills >= 3:
            score = 6
            highlight_type = "multi-kill"
        elif kills >= 2:
            score = 5
            highlight_type = "multi-kill"
        elif kills >= 1:
            score = 3 + (1 if avg_intensity > 0.3 else 0)
            highlight_type = "entry-frag" if avg_intensity > 0.3 else "regular-round"
        else:
            score = 2 if avg_intensity > 0.2 else 1
            highlight_type = "regular-round"

        # Intensity bonus
        if avg_intensity > 0.4 and kills >= 1:
            score = min(10, score + 1)

        # Build summary
        parts = []
        if is_ace:
            parts.append("ACE!")
        if clutch_type:
            parts.append(f"{clutch_type} clutch")
        if kills:
            parts.append(f"{kills} kill{'s' if kills != 1 else ''}")
        if map_name:
            parts.append(f"on {map_name}")
        if round_outcome:
            parts.append(f"(round {'won' if round_outcome == 'win' else 'lost'})")

        action = 'high' if avg_intensity > 0.3 else 'moderate' if avg_intensity > 0.15 else 'low'
        if not parts:
            parts.append(f"{action} action gameplay")
        summary = f"Valorant: {' '.join(parts)}"

        # Confidence based on how many signals we detected
        confidence = 0.3 + min(0.3, gameplay_ratio * 0.3)
        if kills > 0:
            confidence += 0.1
        if clutch_type:
            confidence += 0.1

        # Tags
        tags = []
        if map_name:
            tags.append(map_name)
        if kills >= 3:
            tags.append("multi-kill")
        if is_ace:
            tags.append("ace")
        if clutch_type:
            tags.append("clutch")
            tags.append(clutch_type)
        if round_outcome:
            tags.append(f"round-{round_outcome}")

        return AnalysisResult(
            agent="local-cv",
            map_name=map_name,
            kills=kills,
            deaths=0,
            is_ace=is_ace,
            clutch_type=clutch_type,
            highlight_type=highlight_type,
            round_outcome=round_outcome,
            score=max(1, min(10, score)),
            summary=summary,
            confidence=confidence,
            tags=tags,
        )
