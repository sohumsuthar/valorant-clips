"""Local GPU vision analyzer using Ollama.

Runs a multimodal model (e.g. gemma3:12b-it-qat) locally on the GPU via
Ollama.  No API keys, no rate limits, full visual understanding of gameplay.
Requires Ollama to be installed and running (https://ollama.com).
"""

import logging
from pathlib import Path

from pydantic import BaseModel, Field

from .base import ClipAnalyzer
from ..models import AnalysisResult

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemma3:12b-it-qat"
MAX_FRAMES = 6

SYSTEM_PROMPT = """\
Analyze these Valorant gameplay screenshots. You MUST examine the HUD elements carefully and fill in ALL fields.

WHERE TO LOOK IN EACH FRAME:
- KILL FEED (top-right corner): Shows recent kills as "PlayerName > VictimName". Count how many kills are attributed to the same player (the one recording). Each line = 1 kill. If you see 3 lines with the same killer, kills=3.
- ABILITY BAR (bottom-center): Four ability icons identify the agent. Jett has updraft/dash, Reyna has dismiss/devour, Chamber has trademark/headhunter, etc.
- MINIMAP (top-left corner): Shows the map name and layout. Read the map name text or recognize the layout.
- WEAPON (center-right): The held weapon model. Vandal has a curved magazine, Phantom is straighter, Operator is a long sniper.
- ROUND SCORE (top-center): Shows "Team1 - Team2" score and round result.
- SCOREBOARD: If visible, read kills/deaths/assists directly.

FIELD INSTRUCTIONS:
- kills: Count kills from the kill feed. If summary mentions kills, this number MUST match. Never leave as 0 if any kills are visible.
- map: Read from minimap label or recognize the environment. Ascent has the large tower/market, Bind has teleporters, Haven has 3 sites, Split has ropes, Icebox has the shipping container site, Breeze is tropical/open, Lotus has rotating doors, Sunset has a street/taqueria.
- player_agent: Identify from ability bar icons or agent model/hands visible in first-person view.
- weapon: Identify the gun model in the player's hands.
- deaths: 0 or 1. Set 1 if you see the player die (death screen / spectating).
- is_ace: true ONLY if player gets all 5 kills in one round.
- clutch_type: "1v1", "1v2", "1v3" etc. if player is last alive. null otherwise.
- highlight_type: Pick the most specific type that applies. multi-kill if 3+ kills, ace if 5 kills, clutch if last-alive situation, entry-frag if first kill of round, one-tap if headshot kill.
- round_outcome: "win" or "loss" based on round result. null if unclear.
- score: 1=menu/nothing, 2-3=walking/boring, 4-5=normal round, 6-7=good play with kills, 8-9=multi-kill or clutch, 10=ace or incredible play.
- summary: One sentence describing what happens. Be specific about agent, kills, weapon.
- confidence: 0.0 to 1.0.

VALID VALUES:
agents: jett, reyna, phoenix, sage, omen, killjoy, cypher, sova, breach, raze, brimstone, viper, skye, yoru, astra, kay/o, chamber, neon, fade, harbor, gekko, deadlock, iso, clove, vyse, tejo
maps: bind, haven, split, ascent, icebox, breeze, fracture, pearl, lotus, sunset, abyss
weapons: vandal, phantom, operator, sheriff, ghost, spectre, judge, stinger, guardian, marshal, outlaw, odin, ares, bulldog, classic, shorty, frenzy, bucky, knife
highlight_types: ace, clutch, multi-kill, flick, one-tap, spray-transfer, knife-kill, wallbang, collateral, eco-ace, pistol-round, thrifty, retake, entry-frag, trade-kill, whiff, regular-round, non-gameplay

EXAMPLE OUTPUT for a clip showing Jett getting 3 kills on Ascent with a Vandal:
{"map":"ascent","player_agent":"jett","kills":3,"deaths":0,"is_ace":false,"clutch_type":null,"weapon":"vandal","highlight_type":"multi-kill","round_outcome":"win","score":7,"summary":"Jett gets a 3k with the Vandal on A site Ascent to win the round.","confidence":0.8}

If frames show menus/loading/agent select, set highlight_type to "non-gameplay" and score to 1."""


VALID_MAPS = {"bind", "haven", "split", "ascent", "icebox", "breeze", "fracture", "pearl", "lotus", "sunset", "abyss"}
VALID_AGENTS = {"jett", "reyna", "phoenix", "sage", "omen", "killjoy", "cypher", "sova", "breach", "raze", "brimstone", "viper", "skye", "yoru", "astra", "kay/o", "chamber", "neon", "fade", "harbor", "gekko", "deadlock", "iso", "clove", "vyse", "tejo"}
VALID_WEAPONS = {"vandal", "phantom", "operator", "sheriff", "ghost", "spectre", "judge", "stinger", "guardian", "marshal", "outlaw", "odin", "ares", "bulldog", "classic", "shorty", "frenzy", "bucky", "knife"}
VALID_HIGHLIGHT_TYPES = {"ace", "clutch", "multi-kill", "flick", "one-tap", "spray-transfer", "knife-kill", "wallbang", "collateral", "eco-ace", "pistol-round", "thrifty", "retake", "entry-frag", "trade-kill", "whiff", "regular-round", "non-gameplay"}

# Common misidentifications from the model → correct value
WEAPON_ALIASES = {
    "melee": "knife", "blade": "knife", "karambit": "knife", "dagger": "knife",
    "headhunter": "sheriff",  # Chamber's pistol ability is basically a sheriff
    "tour de force": "operator",  # Chamber's sniper ability
    "op": "operator", "awp": "operator", "sniper": "operator",
    "rifle": "vandal",  # generic → most common
    "smg": "spectre",
    "shotgun": "judge",
    "pistol": "classic",
    "mb4": "vandal",  # model hallucination
}


class OllamaAnalysisResponse(BaseModel):
    """Schema for structured JSON output from Ollama vision model."""
    map: str | None = None
    player_agent: str | None = None
    kills: int = Field(default=0, ge=0)
    deaths: int = Field(default=0, ge=0)
    is_ace: bool = False
    clutch_type: str | None = None
    weapon: str | None = None
    highlight_type: str = "regular-round"
    round_outcome: str | None = None
    score: int = Field(default=5, ge=1, le=10)
    summary: str = ""
    confidence: float = Field(default=0.5, ge=0.0)


class OllamaVisionAnalyzer(ClipAnalyzer):
    """Analyze clips using a local Ollama vision model on GPU."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str | None = None,
        max_frames: int = MAX_FRAMES,
        auto_pull: bool = True,
    ):
        try:
            import ollama as _ollama
        except ImportError:
            raise ImportError(
                "The 'ollama' package is required for local analysis. "
                "Install with: pip install ollama"
            )
        self._ollama = _ollama
        self.model = model
        self.max_frames = max_frames
        self.auto_pull = auto_pull

        # Create client
        if host:
            self.client = _ollama.Client(host=host)
        else:
            self.client = _ollama.Client()

        # Verify server + model
        self._check_server()
        self._ensure_model()

    def _check_server(self):
        """Verify Ollama server is running."""
        try:
            self.client.list()
        except Exception as e:
            err = str(e).lower()
            if "connect" in err or "refused" in err or "request" in err:
                raise ConnectionError(
                    "Ollama is not running. Start it with: ollama serve"
                ) from e
            raise

    def _ensure_model(self):
        """Check model is available, auto-pull if needed."""
        models = self.client.list()
        installed = {m.model for m in models.models}
        # Also check without tag suffix (e.g. "gemma3:12b" matches "gemma3:12b-it-qat")
        installed_base = {m.model.split(":")[0] for m in models.models}

        if self.model in installed:
            return
        # Check partial match (model name without specific tag)
        if any(self.model in name for name in installed):
            return

        if not self.auto_pull:
            raise RuntimeError(
                f"Model '{self.model}' not found. "
                f"Pull it with: ollama pull {self.model}\n"
                f"Installed models: {', '.join(sorted(installed)) or 'none'}"
            )

        # Auto-pull with progress
        logger.info("Pulling model %s (this may take a few minutes)...", self.model)
        try:
            from rich.progress import Progress, BarColumn, DownloadColumn, TransferSpeedColumn
            with Progress(
                "[progress.description]{task.description}",
                BarColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
            ) as progress:
                task = None
                for chunk in self.client.pull(self.model, stream=True):
                    status = chunk.get("status", "")
                    total = chunk.get("total", 0)
                    completed = chunk.get("completed", 0)
                    if total and "pulling" in status:
                        if task is None:
                            task = progress.add_task(f"Pulling {self.model}", total=total)
                        progress.update(task, completed=completed, total=total)
        except ImportError:
            # No rich, just pull silently
            for _ in self.client.pull(self.model, stream=True):
                pass

        logger.info("Model %s ready", self.model)

    def _call_model(self, frames: list[Path]) -> OllamaAnalysisResponse:
        """Send frames to the Ollama vision model and get structured response."""
        # Build image list -- Ollama SDK accepts file paths as strings
        images = [str(f) for f in frames]

        response = self.client.chat(
            model=self.model,
            messages=[{
                "role": "user",
                "content": SYSTEM_PROMPT,
                "images": images,
            }],
            format=OllamaAnalysisResponse.model_json_schema(),
            options={
                "temperature": 0.1,
                "num_predict": 1024,
                "num_ctx": 8192,
            },
            keep_alive="30m",
        )

        text = response.message.content
        return OllamaAnalysisResponse.model_validate_json(text)

    def analyze(self, clip_path: str, keyframes: list[Path]) -> AnalysisResult:
        """Analyze a clip using the local Ollama vision model."""
        if not keyframes:
            return AnalysisResult(
                agent=f"ollama:{self.model}",
                summary="No keyframes available",
                score=1,
                confidence=0.0,
            )

        frames = keyframes[:self.max_frames]
        agent_name = f"ollama:{self.model}"

        try:
            result = self._call_model(frames)
        except Exception as e:
            logger.warning("Ollama analysis failed for %s: %s", clip_path, e)
            return AnalysisResult(
                agent=agent_name,
                summary=f"Analysis failed: {str(e)[:100]}",
                score=1,
                confidence=0.0,
            )

        # Normalize confidence: some models return 1-10 instead of 0-1
        conf = result.confidence
        if conf > 1.0:
            conf = conf / 10.0
        conf = max(0.0, min(1.0, conf))

        # Post-process: validate and normalize model output
        self._normalize(result)

        return AnalysisResult(
            agent=agent_name,
            map_name=result.map,
            player_agent=result.player_agent,
            kills=result.kills,
            deaths=result.deaths,
            is_ace=result.is_ace,
            clutch_type=result.clutch_type,
            weapon=result.weapon,
            highlight_type=result.highlight_type,
            round_outcome=result.round_outcome,
            score=max(1, min(10, result.score)),
            summary=result.summary,
            confidence=conf,
            tags=self._generate_tags(result),
        )

    @staticmethod
    def _normalize(r: OllamaAnalysisResponse) -> None:
        """Validate and fix common model misidentifications in-place."""
        # Normalize map: strip to lowercase, reject if not a valid Valorant map
        if r.map:
            m = r.map.strip().lower().split("-")[0].split("/")[0]  # "mid-skirmish-c" → "mid"
            r.map = m if m in VALID_MAPS else None

        # Normalize agent
        if r.player_agent:
            a = r.player_agent.strip().lower()
            r.player_agent = a if a in VALID_AGENTS else None

        # Normalize weapon: check aliases then validate
        if r.weapon:
            w = r.weapon.strip().lower()
            w = WEAPON_ALIASES.get(w, w)
            r.weapon = w if w in VALID_WEAPONS else None

        # Normalize highlight_type
        if r.highlight_type:
            h = r.highlight_type.strip().lower()
            r.highlight_type = h if h in VALID_HIGHLIGHT_TYPES else "regular-round"

        # Normalize round_outcome
        if r.round_outcome and r.round_outcome.strip().lower() not in ("win", "loss"):
            r.round_outcome = None

        # Fix logical inconsistencies
        if r.is_ace and r.kills < 5:
            r.is_ace = False
        if r.kills >= 5:
            r.is_ace = True
            if r.highlight_type == "regular-round":
                r.highlight_type = "ace"
        if r.kills >= 3 and r.highlight_type == "regular-round":
            r.highlight_type = "multi-kill"

    def _generate_tags(self, r: OllamaAnalysisResponse) -> list[str]:
        """Generate auto-tags from analysis results."""
        tags: list[str] = []
        if r.player_agent:
            tags.append(r.player_agent)
        if r.weapon:
            tags.append(r.weapon)
        if r.map:
            tags.append(r.map)
        if r.is_ace:
            tags.append("ace")
        if r.clutch_type:
            tags.append("clutch")
            tags.append(r.clutch_type)
        if r.highlight_type and r.highlight_type not in ("regular-round", "non-gameplay"):
            tags.append(r.highlight_type)
        if r.kills >= 4:
            tags.append("multi-kill")
        if r.kills >= 3:
            tags.append("triple-kill")
        return tags
