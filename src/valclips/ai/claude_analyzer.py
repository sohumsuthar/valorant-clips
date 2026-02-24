"""Claude Vision-based Valorant clip analyzer.

Sends keyframes to Claude's vision API to identify:
- Map played
- Agent(s) visible
- Weapons used
- Kill feed info (kills, deaths)
- Round context (buy round, eco, etc.)
- Highlight type (ace, clutch, multi-kill, 1v5, etc.)
- Overall quality/impressiveness score
"""

import base64
import os
from pathlib import Path

from anthropic import Anthropic

from ..models import AnalysisResult
from .base import ClipAnalyzer

ANALYSIS_PROMPT = """You are analyzing keyframes from a Valorant gameplay clip. These frames are extracted from the most action-packed moments of the recording.

Analyze the frames carefully and extract:

1. **Map**: Which Valorant map is this? (Bind, Haven, Split, Ascent, Icebox, Breeze, Fracture, Pearl, Lotus, Sunset, Abyss, or unknown)
2. **Agent**: What agent is the player playing? Look at abilities, hands, UI elements.
3. **Weapons**: What weapons are visible? (Vandal, Phantom, Operator, Sheriff, etc.)
4. **Kill Feed**: Look at the kill feed (top right). How many kills does the player get? Is there an ace (5 kills)?
5. **Round Info**: Can you tell if this is a clutch situation (1vX)? What's the round score?
6. **Highlight Type**: Categorize this clip:
   - ace (5 kills in one round)
   - clutch (winning a 1vX situation)
   - multi-kill (3-4 kills)
   - flick (fast aim snap to target)
   - wallbang (kill through wall)
   - knife-kill
   - collateral (one bullet, multiple kills)
   - spray-transfer (transferring spray between targets)
   - eco-ace (ace on eco/save round)
   - regular (normal gameplay, nothing special)
7. **Score**: Rate the impressiveness of this clip from 1-10:
   - 1-3: Regular gameplay, nothing notable
   - 4-5: Decent play, nice aim or positioning
   - 6-7: Good play, multi-kill or clutch
   - 8-9: Exceptional play, ace or impressive clutch
   - 10: Insane, once-in-a-lifetime play

Respond in this EXACT JSON format (no markdown, no code blocks):
{"map": "...", "agent": "...", "weapons": ["..."], "kills": 0, "highlight_type": "...", "score": 0, "summary": "One sentence describing what happens", "tags": ["...", "..."]}

Be conservative with scores. Most clips are 3-5. Only give 8+ for truly exceptional plays. If you can't tell from the frames, make your best guess and note uncertainty in the summary."""


class ClaudeVisionAnalyzer(ClipAnalyzer):
    """Analyze clips by sending keyframes to Claude's vision API."""

    def __init__(self, model: str = "claude-haiku-4-5-20251001"):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY environment variable required. "
                "Set it before running analysis."
            )
        self.client = Anthropic(api_key=api_key)
        self.model = model

    def analyze(self, clip_path: str, keyframes: list[Path]) -> AnalysisResult:
        if not keyframes:
            return AnalysisResult(
                agent="claude-vision",
                summary="No frames available for analysis",
                confidence=0.0,
            )

        # Build message with images
        content = []
        for frame in keyframes[:12]:  # Max 12 frames
            if not frame.exists():
                continue
            data = base64.standard_b64encode(frame.read_bytes()).decode("utf-8")
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": data,
                },
            })

        if not content:
            return AnalysisResult(
                agent="claude-vision",
                summary="No valid frames to analyze",
                confidence=0.0,
            )

        content.append({"type": "text", "text": ANALYSIS_PROMPT})

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=512,
                messages=[{"role": "user", "content": content}],
            )

            text = response.content[0].text.strip()
            return self._parse_response(text)

        except Exception as e:
            return AnalysisResult(
                agent="claude-vision",
                summary=f"Analysis failed: {str(e)[:100]}",
                confidence=0.0,
            )

    def _parse_response(self, text: str) -> AnalysisResult:
        """Parse Claude's JSON response into an AnalysisResult."""
        import json

        # Strip markdown code blocks if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return AnalysisResult(
                agent="claude-vision",
                summary=text[:200],
                confidence=0.2,
            )

        tags = data.get("tags", [])
        highlight = data.get("highlight_type", "")
        if highlight and highlight != "regular" and highlight not in tags:
            tags.append(highlight)

        agent_name = data.get("agent")
        if agent_name and agent_name.lower() not in [t.lower() for t in tags]:
            tags.append(agent_name.lower())

        weapons = data.get("weapons", [])
        for w in weapons:
            wl = w.lower()
            if wl not in [t.lower() for t in tags]:
                tags.append(wl)

        score = data.get("score", 5)
        confidence = min(1.0, score / 10.0)

        return AnalysisResult(
            agent="claude-vision",
            map_name=data.get("map"),
            tags=tags,
            summary=data.get("summary", ""),
            confidence=confidence,
            score=score,
            kills=data.get("kills"),
            highlight_type=highlight or None,
        )
