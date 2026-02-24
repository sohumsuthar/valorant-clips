"""Gemini Vision analyzer for Valorant clip understanding.

Uses Google's Gemini Flash (free tier: 15 RPM, 1M tokens/day) to analyze
keyframes extracted from clips. Detects kills, aces, clutches, agents,
maps, weapons, and provides an impressiveness score.
"""

import base64
import json
import time
from pathlib import Path

import httpx

from .base import ClipAnalyzer
from ..models import AnalysisResult

# Gemini free tier: 15 requests/minute
RATE_LIMIT_RPM = 15
MIN_REQUEST_INTERVAL = 60.0 / RATE_LIMIT_RPM  # 4 seconds between requests

SYSTEM_PROMPT = """You are an expert Valorant gameplay analyst. You will be shown keyframes from a Valorant gameplay clip recording.

Analyze the frames and determine:

1. **map**: Which Valorant map is this? (bind, haven, split, ascent, icebox, breeze, fracture, pearl, lotus, sunset, abyss, or null if unclear)
2. **player_agent**: Which agent is the player playing? Look at the ability HUD at the bottom, crosshair style, hands/arms visible. (jett, reyna, phoenix, sage, omen, killjoy, cypher, sova, breach, raze, brimstone, viper, skye, yoru, astra, kay/o, chamber, neon, fade, harbor, gekko, deadlock, iso, clove, vyse, tejo, or null)
3. **kills**: How many kills can you see in the kill feed (top right) or from kill banners? Count carefully.
4. **deaths**: How many times does the player die? (0 or 1 typically)
5. **is_ace**: Is this an ace (5 kills in one round)? true/false
6. **clutch_type**: If this is a clutch situation (player is last alive vs enemies), what type? "1v1", "1v2", "1v3", "1v4", "1v5", or null
7. **weapon**: Primary weapon used? (vandal, phantom, operator, sheriff, ghost, spectre, judge, stinger, guardian, marshal, outlaw, odin, ares, bulldog, classic, shorty, frenzy, bucky, knife, or null)
8. **highlight_type**: Best description of the clip. One of: "ace", "clutch", "multi-kill", "flick", "one-tap", "spray-transfer", "knife-kill", "wallbang", "collateral", "eco-ace", "pistol-round", "thrifty", "retake", "fake-defuse", "ninja-defuse", "entry-frag", "trade-kill", "whiff" (if they miss a lot), "regular-round", or "non-gameplay" (menus, loading, etc.)
9. **round_outcome**: Did the player's team win this round? "win", "loss", or null
10. **score**: Rate the impressiveness of this clip from 1-10:
    - 1-2: Nothing interesting, regular gameplay or non-gameplay
    - 3-4: Decent play, a kill or two
    - 5-6: Good play, multi-kill or nice shots
    - 7-8: Great play, clutch, ace, or very impressive mechanics
    - 9-10: Insane play, impossible-looking shots, 1v4+, ace with style
11. **summary**: One sentence describing what happens in the clip.
12. **confidence**: How confident are you in this analysis? 0.0-1.0

IMPORTANT RULES:
- If you can clearly see the kill feed, count kills precisely
- If frames show menus, loading screens, or non-gameplay, set highlight_type to "non-gameplay" and score to 1
- If this is clearly NOT Valorant (different game), say so in summary and set score to 1
- Look at the round/score display at the top to understand the game state
- An ace requires EXACTLY 5 kills by the same player in one round
- For clutch detection, look at the alive player count indicators at the top

Respond with ONLY a valid JSON object, no markdown formatting."""

ANALYSIS_SCHEMA = {
    "map": "string or null",
    "player_agent": "string or null",
    "kills": "integer",
    "deaths": "integer",
    "is_ace": "boolean",
    "clutch_type": "string or null",
    "weapon": "string or null",
    "highlight_type": "string",
    "round_outcome": "string or null",
    "score": "integer 1-10",
    "summary": "string",
    "confidence": "float 0-1",
}


def _encode_image(path: Path) -> str:
    """Base64 encode an image file."""
    data = path.read_bytes()
    return base64.standard_b64encode(data).decode("utf-8")


def _build_request(frames: list[Path], model: str = "gemini-2.0-flash") -> dict:
    """Build the Gemini API request payload with inline images."""
    parts = []

    # Add each frame as an inline image
    for frame in frames:
        mime = "image/jpeg" if frame.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        parts.append({
            "inlineData": {
                "mimeType": mime,
                "data": _encode_image(frame),
            }
        })

    # Add the analysis prompt
    parts.append({"text": SYSTEM_PROMPT})

    return {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 1024,
            "responseMimeType": "application/json",
        },
    }


def _parse_response(text: str) -> dict:
    """Parse the JSON response from Gemini."""
    # Strip markdown code fences if present
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        raise


class GeminiAnalyzer(ClipAnalyzer):
    """Analyze clips using Google Gemini Vision API."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.0-flash",
        max_frames: int = 8,
    ):
        self.api_key = api_key
        self.model = model
        self.max_frames = max_frames
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"
        self._last_request_time = 0.0

    def _rate_limit(self):
        """Enforce rate limiting for free tier."""
        elapsed = time.time() - self._last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_time = time.time()

    def analyze(self, clip_path: str, keyframes: list[Path]) -> AnalysisResult:
        """Analyze a clip using Gemini Vision."""
        if not keyframes:
            return AnalysisResult(
                agent="gemini",
                summary="No keyframes available for analysis",
                score=1,
                confidence=0.0,
            )

        # Limit frames to avoid token limits
        frames = keyframes[:self.max_frames]

        self._rate_limit()

        url = f"{self.base_url}/models/{self.model}:generateContent?key={self.api_key}"
        payload = _build_request(frames, self.model)

        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(url, json=payload)

            if resp.status_code == 429:
                # Rate limited - wait and retry once
                time.sleep(10)
                self._rate_limit()
                with httpx.Client(timeout=60.0) as client:
                    resp = client.post(url, json=payload)

            if resp.status_code != 200:
                return AnalysisResult(
                    agent="gemini",
                    summary=f"API error: {resp.status_code}",
                    score=1,
                    confidence=0.0,
                )

            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            result = _parse_response(text)

        except Exception as e:
            return AnalysisResult(
                agent="gemini",
                summary=f"Analysis failed: {str(e)[:100]}",
                score=1,
                confidence=0.0,
            )

        return AnalysisResult(
            agent="gemini",
            map_name=result.get("map"),
            player_agent=result.get("player_agent"),
            kills=result.get("kills", 0),
            deaths=result.get("deaths", 0),
            is_ace=bool(result.get("is_ace", False)),
            clutch_type=result.get("clutch_type"),
            weapon=result.get("weapon"),
            highlight_type=result.get("highlight_type", "regular-round"),
            round_outcome=result.get("round_outcome"),
            score=max(1, min(10, result.get("score", 1))),
            summary=result.get("summary", ""),
            confidence=result.get("confidence", 0.5),
            tags=self._generate_tags(result),
        )

    def _generate_tags(self, result: dict) -> list[str]:
        """Generate auto-tags from analysis results."""
        tags = []
        if result.get("player_agent"):
            tags.append(result["player_agent"])
        if result.get("weapon"):
            tags.append(result["weapon"])
        if result.get("map"):
            tags.append(result["map"])
        if result.get("is_ace"):
            tags.append("ace")
        if result.get("clutch_type"):
            tags.append("clutch")
            tags.append(result["clutch_type"])
        ht = result.get("highlight_type", "")
        if ht and ht not in ("regular-round", "non-gameplay"):
            tags.append(ht)
        kills = result.get("kills", 0)
        if kills >= 4:
            tags.append("multi-kill")
        if kills >= 3:
            tags.append("triple-kill")
        return tags
