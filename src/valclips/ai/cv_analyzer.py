"""OpenCV-based gameplay mechanics analyzer.

Uses Farneback optical flow on the center crosshair region to detect
flick speed, crosshair stability, reaction time, counter-strafes,
spray control, and peek timing.  Pure CPU -- no GPU or LLM required.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np


# ── Region constants (fractions of frame) ──────────────────────────
# Crosshair region: center ~20% of screen (where the player aims)
CX_LEFT, CX_RIGHT = 0.40, 0.60
CY_TOP, CY_BOT = 0.35, 0.65

# Peripheral region: outer edges (where enemies appear before a flick)
PERIPH_TOP, PERIPH_BOT = 0.10, 0.90
PERIPH_LEFT, PERIPH_RIGHT = 0.05, 0.95

# Movement region: bottom third (player model + strafing)
MV_TOP = 0.70

# Optical flow params (Farneback)
FLOW_PARAMS = dict(
    pyr_scale=0.5,
    levels=3,
    winsize=15,
    iterations=3,
    poly_n=5,
    poly_sigma=1.2,
    flags=0,
)

# Analysis thresholds
FLICK_SPEED_THRESHOLD_MULT = 5.0  # spike must be >5x median to count as flick
FLICK_MIN_ABS_SPEED = 4.0         # absolute minimum center flow to be a flick (px/frame)
FLICK_MIN_PEAK_DEG_S = 100.0      # minimum peak speed in deg/s to keep a flick
FLICK_MIN_FRAMES = 2
FLICK_MAX_FRAMES = 6              # real flicks are fast (67-200ms at 30fps)
FLICK_COOLDOWN_S = 0.5            # minimum time between flick events
REACTION_MIN_MS = 80
REACTION_MAX_MS = 500
COUNTER_STRAFE_MIN_SPEED = 3.0    # min horizontal flow for a strafe (px/frame)


@dataclass
class FlickEvent:
    timestamp: float       # seconds into clip
    duration_ms: float     # how long the flick lasted
    peak_speed: float      # deg/s equivalent (pixels/frame normalised)
    frame_start: int = 0
    frame_end: int = 0


@dataclass
class CVAnalysisResult:
    flicks: list[FlickEvent] = field(default_factory=list)
    avg_flick_speed: float | None = None
    max_flick_speed: float | None = None
    fastest_flick_ms: float | None = None
    crosshair_score: float | None = None       # 0-1 (1 = perfectly stable)
    counter_strafe_count: int | None = None
    avg_reaction_ms: float | None = None
    min_reaction_ms: float | None = None
    movement_intensity: float | None = None     # avg horizontal flow magnitude
    spray_control_score: float | None = None    # 0-1
    peek_count: int | None = None
    total_frames_analyzed: int = 0

    def to_dict(self) -> dict:
        d = {
            "flicks": [
                {"timestamp": f.timestamp, "duration_ms": f.duration_ms, "peak_speed": round(f.peak_speed, 1)}
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
        }
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


def _r(v: float | None, decimals: int = 1) -> float | None:
    return round(v, decimals) if v is not None else None


class CVFrameAnalyzer:
    """Analyze a clip's gameplay mechanics using optical flow."""

    def __init__(self, target_fps: float = 30.0, target_width: int = 640):
        self.target_fps = target_fps
        self.target_width = target_width

    def analyze(self, clip_path: str | Path) -> CVAnalysisResult:
        clip_path = str(clip_path)
        cap = cv2.VideoCapture(clip_path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {clip_path}")

        src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        # Compute frame skip to approximate target_fps
        skip = max(1, int(round(src_fps / self.target_fps)))
        scale = self.target_width / src_w if src_w > 0 else 1.0
        target_h = int(src_h * scale)
        effective_fps = src_fps / skip

        # Region pixel coords (after resize)
        tw, th = self.target_width, target_h
        cx_l, cx_r = int(tw * CX_LEFT), int(tw * CX_RIGHT)
        cy_t, cy_b = int(th * CY_TOP), int(th * CY_BOT)
        mv_t = int(th * MV_TOP)
        p_l, p_r = int(tw * PERIPH_LEFT), int(tw * PERIPH_RIGHT)
        p_t, p_b = int(th * PERIPH_TOP), int(th * PERIPH_BOT)

        # Collectors
        center_speeds: list[float] = []    # magnitude of center-region flow per frame
        center_vy: list[float] = []        # vertical component of center flow
        move_hx: list[float] = []          # horizontal flow in movement region
        periph_speeds: list[float] = []    # peripheral motion (enemy appearance)

        prev_gray = None
        frame_idx = 0
        analyzed = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % skip != 0:
                frame_idx += 1
                continue

            small = cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

            if prev_gray is not None:
                flow = cv2.calcOpticalFlowFarneback(prev_gray, gray, None, **FLOW_PARAMS)

                # Center crosshair region
                cx_flow = flow[cy_t:cy_b, cx_l:cx_r]
                mag, _ = cv2.cartToPolar(cx_flow[..., 0], cx_flow[..., 1])
                center_speeds.append(float(np.mean(mag)))
                center_vy.append(float(np.mean(cx_flow[..., 1])))

                # Movement region (bottom of screen)
                mv_flow = flow[mv_t:, :]
                move_hx.append(float(np.mean(mv_flow[..., 0])))

                # Peripheral region (excluding center)
                p_flow = flow[p_t:p_b, p_l:p_r]
                p_mag, _ = cv2.cartToPolar(p_flow[..., 0], p_flow[..., 1])
                periph_speeds.append(float(np.mean(p_mag)))

                analyzed += 1

            prev_gray = gray
            frame_idx += 1

        cap.release()

        if analyzed < 10:
            return CVAnalysisResult(total_frames_analyzed=analyzed)

        # ── Detect flick events ─────────────────────────────────────
        speeds = np.array(center_speeds)
        median_speed = float(np.median(speeds))
        threshold = max(median_speed * FLICK_SPEED_THRESHOLD_MULT, FLICK_MIN_ABS_SPEED)

        # FOV conversion constants
        fov_deg = 103.0
        deg_per_px = fov_deg / tw
        cooldown_frames = int(FLICK_COOLDOWN_S * effective_fps)

        flicks: list[FlickEvent] = []
        in_flick = False
        flick_start = 0
        flick_peak = 0.0
        last_flick_end = -cooldown_frames  # allow first flick immediately

        for i, s in enumerate(speeds):
            if not in_flick and s > threshold:
                in_flick = True
                flick_start = i
                flick_peak = s
            elif in_flick:
                if s > flick_peak:
                    flick_peak = s
                if s <= threshold or i == len(speeds) - 1:
                    in_flick = False
                    duration_frames = i - flick_start
                    peak_deg_s = flick_peak * deg_per_px * effective_fps
                    if (FLICK_MIN_FRAMES <= duration_frames <= FLICK_MAX_FRAMES
                            and peak_deg_s >= FLICK_MIN_PEAK_DEG_S
                            and (flick_start - last_flick_end) >= cooldown_frames):
                        ts = (flick_start * skip) / src_fps
                        dur_ms = (duration_frames * skip / src_fps) * 1000
                        flicks.append(FlickEvent(
                            timestamp=round(ts, 2),
                            duration_ms=round(dur_ms, 1),
                            peak_speed=round(peak_deg_s, 1),
                            frame_start=flick_start,
                            frame_end=i,
                        ))
                        last_flick_end = i

        # ── Crosshair stability ──────────────────────────────────────
        # Low variance in vertical center flow = good head-level discipline
        vy_arr = np.array(center_vy)
        vy_std = float(np.std(vy_arr))
        # Normalise to 0-1 (lower std -> higher score)
        # Typical gameplay: std 0.5-1.5 is decent, >5 is very erratic
        crosshair_score = max(0.0, min(1.0, 1.0 - (vy_std - 0.5) / 5.0))

        # ── Counter-strafe detection ─────────────────────────────────
        hx = np.array(move_hx)
        counter_strafes = 0
        for i in range(1, len(hx)):
            if (abs(hx[i]) > COUNTER_STRAFE_MIN_SPEED and
                    abs(hx[i - 1]) > COUNTER_STRAFE_MIN_SPEED and
                    hx[i] * hx[i - 1] < 0):
                counter_strafes += 1

        # ── Movement intensity ───────────────────────────────────────
        movement_intensity = float(np.mean(np.abs(hx)))

        # ── Reaction time estimation ─────────────────────────────────
        # For each flick, look backward for a peripheral motion spike
        periph = np.array(periph_speeds)
        periph_median = float(np.median(periph)) if len(periph) > 0 else 0
        periph_thresh = max(periph_median * 2.0, 1.0)

        reaction_times: list[float] = []
        for flick in flicks:
            fs = flick.frame_start
            # Look back up to 30 analyzed frames (~1s at 30fps)
            lookback = min(fs, 30)
            for j in range(fs - 1, fs - lookback - 1, -1):
                if j < 0 or j >= len(periph):
                    break
                if periph[j] > periph_thresh:
                    gap_frames = fs - j
                    gap_ms = (gap_frames * skip / src_fps) * 1000
                    if REACTION_MIN_MS <= gap_ms <= REACTION_MAX_MS:
                        reaction_times.append(gap_ms)
                    break

        # ── Spray control ────────────────────────────────────────────
        # After each flick ends, measure how consistently the crosshair
        # tracks downward (recoil compensation) over the next ~10 frames
        spray_scores: list[float] = []
        for flick in flicks:
            fe = flick.frame_end
            window = min(10, len(center_vy) - fe)
            if window < 3:
                continue
            post_vy = np.array(center_vy[fe:fe + window])
            # Good spray control = consistently negative vy (pulling down)
            negative_frac = float(np.sum(post_vy < 0)) / len(post_vy)
            spray_scores.append(negative_frac)

        spray_control = float(np.mean(spray_scores)) if spray_scores else None

        # ── Peek detection ───────────────────────────────────────────
        # A peek is: movement starts (|hx| spikes), sustains briefly, then stops
        peek_count = 0
        in_peek = False
        peek_start = 0
        min_peek_frames = max(3, int(0.1 * effective_fps))  # at least ~100ms
        for i in range(len(hx)):
            if not in_peek and abs(hx[i]) > COUNTER_STRAFE_MIN_SPEED * 2.0:
                in_peek = True
                peek_start = i
            elif in_peek and abs(hx[i]) < COUNTER_STRAFE_MIN_SPEED * 0.3:
                in_peek = False
                if (i - peek_start) >= min_peek_frames:
                    peek_count += 1

        # ── Assemble result ──────────────────────────────────────────
        result = CVAnalysisResult(
            flicks=flicks,
            avg_flick_speed=float(np.mean([f.peak_speed for f in flicks])) if flicks else None,
            max_flick_speed=float(max(f.peak_speed for f in flicks)) if flicks else None,
            fastest_flick_ms=float(min(f.duration_ms for f in flicks)) if flicks else None,
            crosshair_score=crosshair_score,
            counter_strafe_count=counter_strafes,
            avg_reaction_ms=float(np.mean(reaction_times)) if reaction_times else None,
            min_reaction_ms=float(min(reaction_times)) if reaction_times else None,
            movement_intensity=movement_intensity,
            spray_control_score=spray_control,
            peek_count=peek_count,
            total_frames_analyzed=analyzed,
        )
        return result
