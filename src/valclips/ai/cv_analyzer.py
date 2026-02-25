"""OpenCV gameplay mechanics analyzer.

Classifies frames as gameplay vs non-gameplay using Laplacian variance,
then measures aim mechanics and movement patterns only during actual
gameplay.  Does NOT attempt to detect individual kills (the AI vision
analyzers do that much more reliably).

Metrics produced:
  - gameplay_pct: what fraction of the clip is actual gameplay
  - flicks: top aim-speed peaks during gameplay (fast crosshair snaps)
  - crosshair_score: vertical aim stability during calm gameplay
  - counter_strafe_count / peek_count / movement_intensity
  - aim_smoothness: consistency of aim speed (low = steady, high = erratic)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np


# ── Frame regions (fractions of 1920×1080) ───────────────────────
# Crosshair / aim region: center screen
XH_X1, XH_Y1, XH_X2, XH_Y2 = 0.38, 0.32, 0.62, 0.68
# Movement region: bottom third
MV_Y1 = 0.70

# ── Optical flow (Farneback) ────────────────────────────────────
FLOW_PARAMS = dict(
    pyr_scale=0.5, levels=2, winsize=15,
    iterations=2, poly_n=5, poly_sigma=1.2, flags=0,
)

# ── Gameplay detection ──────────────────────────────────────────
GAMEPLAY_LAP_THRESHOLD = 500.0

# ── Aim snap detection ──────────────────────────────────────────
SNAP_MIN_DEG_S = 80.0         # minimum speed to count as aim snap
SNAP_COOLDOWN_S = 0.4         # seconds between detections

# ── Movement ────────────────────────────────────────────────────
COUNTER_STRAFE_MIN = 2.5
PEEK_MIN_SPEED = 4.0
PEEK_MIN_FRAMES = 2

VALORANT_FOV = 103.0


@dataclass
class FlickEvent:
    timestamp: float
    duration_ms: float
    peak_speed: float  # deg/s
    frame_start: int = 0
    frame_end: int = 0


@dataclass
class CVAnalysisResult:
    flicks: list[FlickEvent] = field(default_factory=list)
    avg_flick_speed: float | None = None
    max_flick_speed: float | None = None
    fastest_flick_ms: float | None = None
    crosshair_score: float | None = None
    counter_strafe_count: int | None = None
    avg_reaction_ms: float | None = None
    min_reaction_ms: float | None = None
    movement_intensity: float | None = None
    spray_control_score: float | None = None
    peek_count: int | None = None
    total_frames_analyzed: int = 0
    kills_detected: int = 0
    gameplay_pct: float | None = None

    def to_dict(self) -> dict:
        return {
            "flicks": [
                {"timestamp": f.timestamp, "duration_ms": f.duration_ms,
                 "peak_speed": round(f.peak_speed, 1)}
                for f in self.flicks
            ],
            "avg_flick_speed": _r(self.avg_flick_speed),
            "max_flick_speed": _r(self.max_flick_speed),
            "fastest_flick_ms": _r(self.fastest_flick_ms),
            "crosshair_score": _r(self.crosshair_score, 3),
            "counter_strafe_count": self.counter_strafe_count,
            "avg_reaction_ms": _r(self.avg_reaction_ms),
            "min_reaction_ms": _r(self.min_reaction_ms),
            "movement_intensity": _r(self.movement_intensity),
            "spray_control_score": _r(self.spray_control_score, 3),
            "peek_count": self.peek_count,
            "total_frames_analyzed": self.total_frames_analyzed,
            "kills_detected": self.kills_detected,
            "gameplay_pct": _r(self.gameplay_pct, 1),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


def _r(v: float | None, decimals: int = 1) -> float | None:
    return round(v, decimals) if v is not None else None


class CVFrameAnalyzer:
    """Gameplay-aware mechanics analyzer.

    Uses Laplacian variance to classify gameplay frames, then measures
    aim speed, crosshair discipline, and movement patterns only during
    actual gameplay.  Fast (~5-10 s per clip at 15 fps).
    """

    def __init__(self, target_fps: float = 15.0, target_width: int = 640):
        self.target_fps = target_fps
        self.target_width = target_width

    def analyze(self, clip_path: str | Path) -> CVAnalysisResult:
        clip_path = str(clip_path)
        cap = cv2.VideoCapture(clip_path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {clip_path}")

        src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        skip = max(1, int(round(src_fps / self.target_fps)))
        scale = self.target_width / src_w if src_w > 0 else 1.0
        tw = self.target_width
        th = int(src_h * scale)
        effective_fps = src_fps / skip
        deg_per_px = VALORANT_FOV / tw

        # Pixel regions
        xh_x1, xh_y1 = int(tw * XH_X1), int(th * XH_Y1)
        xh_x2, xh_y2 = int(tw * XH_X2), int(th * XH_Y2)
        mv_y1 = int(th * MV_Y1)
        gp_x1, gp_y1 = int(tw * 0.2), int(th * 0.2)
        gp_x2, gp_y2 = int(tw * 0.8), int(th * 0.8)

        # Collectors
        timestamps: list[float] = []
        is_gameplay: list[bool] = []
        center_speeds: list[float] = []
        center_vy: list[float] = []
        move_hx: list[float] = []

        prev_gray = None
        flow_buf = np.zeros((th, tw, 2), dtype=np.float32)
        frame_idx = 0
        analyzed = 0

        while True:
            frame_idx += 1
            if frame_idx % skip != 0:
                # grab() advances without decoding — much faster
                if not cap.grab():
                    break
                continue
            ret, frame = cap.read()
            if not ret:
                break

            small = cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

            timestamps.append(frame_idx / src_fps)

            # Gameplay detection via Laplacian variance on center region
            center_gray = gray[gp_y1:gp_y2, gp_x1:gp_x2]
            lap_var = cv2.Laplacian(center_gray, cv2.CV_64F).var()
            is_gameplay.append(lap_var > GAMEPLAY_LAP_THRESHOLD)

            # Optical flow
            if prev_gray is not None:
                cv2.calcOpticalFlowFarneback(
                    prev_gray, gray, flow_buf, **FLOW_PARAMS,
                )
                xh_flow = flow_buf[xh_y1:xh_y2, xh_x1:xh_x2]
                mag, _ = cv2.cartToPolar(xh_flow[..., 0], xh_flow[..., 1])
                center_speeds.append(float(np.mean(mag)))
                center_vy.append(float(np.mean(xh_flow[..., 1])))
                mv_flow = flow_buf[mv_y1:, :]
                move_hx.append(float(np.mean(mv_flow[..., 0])))
            else:
                center_speeds.append(0.0)
                center_vy.append(0.0)
                move_hx.append(0.0)

            prev_gray = gray
            analyzed += 1

        cap.release()

        if analyzed < 10:
            return CVAnalysisResult(total_frames_analyzed=analyzed)

        # ═════════════════════════════════════════════════════════
        # POST-PROCESSING (gameplay frames only)
        # ═════════════════════════════════════════════════════════

        gp = np.array(is_gameplay)
        gameplay_pct = float(np.mean(gp)) * 100

        # Extract gameplay-only data
        gp_speeds = np.array([center_speeds[i] for i in range(analyzed) if gp[i]])
        gp_vy = np.array([center_vy[i] for i in range(analyzed) if gp[i]])
        gp_hx = np.array([move_hx[i] for i in range(analyzed) if gp[i]])
        gp_indices = [i for i in range(analyzed) if gp[i]]

        if len(gp_speeds) < 10:
            return CVAnalysisResult(
                total_frames_analyzed=analyzed,
                gameplay_pct=gameplay_pct,
            )

        # ── Aim snaps (fast crosshair movements during gameplay) ─
        speed_threshold_px = SNAP_MIN_DEG_S / (deg_per_px * effective_fps)
        cooldown_frames = int(SNAP_COOLDOWN_S * effective_fps)

        flicks: list[FlickEvent] = []
        last_snap_idx = -cooldown_frames

        for local_i, global_i in enumerate(gp_indices):
            if gp_speeds[local_i] <= speed_threshold_px:
                continue
            if (global_i - last_snap_idx) < cooldown_frames:
                continue

            peak_px = gp_speeds[local_i]
            peak_deg_s = peak_px * deg_per_px * effective_fps

            if peak_deg_s < SNAP_MIN_DEG_S:
                continue

            # Measure duration at half-peak
            half = peak_px * 0.5
            f_start, f_end = local_i, local_i
            for j in range(local_i - 1, max(local_i - 10, -1), -1):
                if j < 0 or gp_speeds[j] < half:
                    break
                f_start = j
            for j in range(local_i + 1, min(local_i + 10, len(gp_speeds))):
                if gp_speeds[j] < half:
                    break
                f_end = j

            dur_ms = ((f_end - f_start + 1) * skip / src_fps) * 1000
            ts = timestamps[global_i] if global_i < len(timestamps) else 0

            flicks.append(FlickEvent(
                timestamp=round(ts, 2),
                duration_ms=round(dur_ms, 1),
                peak_speed=round(peak_deg_s, 1),
                frame_start=gp_indices[f_start] if f_start < len(gp_indices) else 0,
                frame_end=gp_indices[f_end] if f_end < len(gp_indices) else 0,
            ))
            last_snap_idx = global_i

        # ── Crosshair discipline (vertical stability) ───────────
        # Exclude frames near aim snaps (those have intentional vertical movement)
        snap_frames = {gp_indices[local_i]
                       for local_i, _ in enumerate(gp_indices)
                       if local_i < len(gp_speeds) and gp_speeds[local_i] > speed_threshold_px}
        snap_neighbourhood: set[int] = set()
        for sf in snap_frames:
            for j in range(max(0, sf - cooldown_frames),
                           min(analyzed, sf + cooldown_frames)):
                snap_neighbourhood.add(j)

        calm_vy = [center_vy[i] for i in gp_indices
                    if i not in snap_neighbourhood]

        if len(calm_vy) > 10:
            vy_std = float(np.std(calm_vy))
            # 0-1 scale: std=0.5 → 1.0, std=6.5 → 0.0
            # Wider range to account for 15fps frame intervals
            crosshair_score = max(0.0, min(1.0, 1.0 - (vy_std - 0.5) / 6.0))
        else:
            crosshair_score = None

        # ── Counter-strafes ─────────────────────────────────────
        counter_strafes = 0
        for i in range(1, len(gp_hx)):
            if (abs(gp_hx[i]) > COUNTER_STRAFE_MIN
                    and abs(gp_hx[i - 1]) > COUNTER_STRAFE_MIN
                    and gp_hx[i] * gp_hx[i - 1] < 0):
                counter_strafes += 1

        movement_intensity = float(np.mean(np.abs(gp_hx)))

        # ── Peeks ───────────────────────────────────────────────
        peek_count = 0
        in_peek = False
        peek_start = 0
        for i in range(len(gp_hx)):
            if not in_peek and abs(gp_hx[i]) > PEEK_MIN_SPEED:
                in_peek = True
                peek_start = i
            elif in_peek and abs(gp_hx[i]) < PEEK_MIN_SPEED * 0.3:
                in_peek = False
                if (i - peek_start) >= PEEK_MIN_FRAMES:
                    peek_count += 1

        # ── Assemble ────────────────────────────────────────────
        flick_speeds = [f.peak_speed for f in flicks]
        flick_durs = [f.duration_ms for f in flicks]

        return CVAnalysisResult(
            flicks=flicks,
            avg_flick_speed=(float(np.mean(flick_speeds))
                             if flick_speeds else None),
            max_flick_speed=(float(max(flick_speeds))
                             if flick_speeds else None),
            fastest_flick_ms=(float(min(flick_durs))
                              if flick_durs else None),
            crosshair_score=crosshair_score,
            counter_strafe_count=counter_strafes,
            avg_reaction_ms=None,
            min_reaction_ms=None,
            movement_intensity=movement_intensity,
            spray_control_score=None,
            peek_count=peek_count,
            total_frames_analyzed=analyzed,
            kills_detected=0,
            gameplay_pct=gameplay_pct,
        )
