"""Gemini Vision analyzer for Valorant clip understanding.

Uses Google's Gemini Flash (free tier: 15 RPM, 1M tokens/day) to analyze
keyframes extracted from clips. Detects kills, aces, clutches, agents,
maps, weapons, and provides an impressiveness score.
"""

import base64
import json
import re
import time
from pathlib import Path

import httpx

from .base import ClipAnalyzer
from ..models import AnalysisResult


class QuotaExhaustedError(Exception):
    """Raised when the Gemini API daily quota is exhausted."""
    pass

# Gemini free tier: varies by model. Be conservative.
RATE_LIMIT_RPM = 6  # Stay well under limits
MIN_REQUEST_INTERVAL = 60.0 / RATE_LIMIT_RPM  # 10 seconds between requests
MAX_RETRIES = 5

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
            "maxOutputTokens": 2048,
            "responseMimeType": "application/json",
        },
    }


def _fix_json(text: str) -> str:
    """Fix common JSON issues from LLM output."""
    # Remove trailing commas before } or ]
    text = re.sub(r',\s*([}\]])', r'\1', text)
    # Quote unquoted property names: { map: "bind" } -> { "map": "bind" }
    text = re.sub(r'(?<=[{,])\s*(\w+)\s*:', r' "\1":', text)
    # Replace single-quoted values with double quotes
    text = re.sub(r":\s*'([^']*)'", r': "\1"', text)
    # Fix truncated strings by closing open quotes/braces
    # Count unmatched braces
    opens = text.count('{') - text.count('}')
    if opens > 0:
        # Try to close incomplete JSON
        # If ends mid-string, close the string
        if text.rstrip().endswith(('\\', '"')) is False:
            # Check if we're in a string
            last_quote = text.rfind('"')
            if last_quote > 0:
                before = text[:last_quote]
                # Count unescaped quotes before
                q_count = len(re.findall(r'(?<!\\)"', before))
                if q_count % 2 == 0:
                    # We're inside an open string, close it
                    text = text.rstrip() + '"'
        text = text.rstrip()
        if not text.endswith('}'):
            # Remove trailing incomplete key-value pairs
            last_complete = max(text.rfind('",'), text.rfind('",'), text.rfind('null,'), text.rfind('true,'), text.rfind('false,'))
            last_num = -1
            m = list(re.finditer(r':\s*\d+\s*[,}]', text))
            if m:
                last_num = m[-1].end() - 1
            last = max(last_complete, last_num)
            if last > 0:
                text = text[:last + 1].rstrip(',')
            text += '}' * opens
    return text


def _parse_response(text: str) -> dict:
    """Parse the JSON response from Gemini."""
    # Strip markdown code fences if present
    text = text.strip()
    if text.startswith("```"):
        # Remove language identifier line (```json)
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    # Try parsing as-is first
    for attempt in range(2):
        try:
            parsed = json.loads(text)
            break
        except json.JSONDecodeError:
            if attempt == 0:
                # Try fixing common issues
                text = _fix_json(text)
                continue
            # Try to find JSON object in the response
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                snippet = _fix_json(text[start:end])
                try:
                    parsed = json.loads(snippet)
                    break
                except json.JSONDecodeError:
                    pass
            raise
    else:
        raise json.JSONDecodeError("Could not parse response", text, 0)

    # Handle case where model returns a list (take first dict)
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                return item
        return {}

    return parsed


class GeminiAnalyzer(ClipAnalyzer):
    """Analyze clips using Google Gemini Vision API."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3-flash-preview",
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

    def _call_api(self, frames: list[Path]) -> dict:
        """Call Gemini API with retry and exponential backoff."""
        url = f"{self.base_url}/models/{self.model}:generateContent?key={self.api_key}"
        payload = _build_request(frames, self.model)

        for attempt in range(MAX_RETRIES):
            self._rate_limit()
            try:
                with httpx.Client(timeout=90.0) as client:
                    resp = client.post(url, json=payload)

                if resp.status_code == 429:
                    error_msg = resp.text[:300].lower()
                    # Distinguish quota exhaustion from rate limiting
                    if "exceeded your current quota" in error_msg or "billing" in error_msg:
                        raise QuotaExhaustedError(
                            "Daily quota exhausted. Quotas reset at midnight Pacific time."
                        )
                    wait = (2 ** attempt) * 20  # 20s, 40s, 80s, 160s, 320s
                    time.sleep(wait)
                    continue

                if resp.status_code != 200:
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(5)
                        continue
                    raise RuntimeError(f"API error {resp.status_code}: {resp.text[:200]}")

                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return _parse_response(text)

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(10)
                    continue
                raise

        raise RuntimeError("Max retries exceeded")

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

        try:
            result = self._call_api(frames)
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
