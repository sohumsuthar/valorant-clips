"""Local GPU vision analyzer using Ollama.

Runs a multimodal model locally on the GPU via Ollama for detailed
frame-by-frame analysis.  Extracts frames densely (2fps default)
throughout the entire clip, plus zoomed kill feed crops, for maximum
accuracy.  No API keys, no rate limits.

Requires Ollama to be installed and running (https://ollama.com).
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from .base import ClipAnalyzer
from ..models import AnalysisResult
from ..config import FFPROBE_TIMEOUT

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemma3:12b-it-qat"
DENSE_FPS = 2.0       # Frames per second to extract
MAX_FRAMES = 20        # Max full frames sent to model per inference
MAX_CROPS = 10         # Max kill feed crops sent alongside frames
NUM_CTX = 32768        # Context window -- needs to be large for many images


# ---------------------------------------------------------------------------
# Prompt -- designed for sequential frame analysis with kill feed crops
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are analyzing {n_frames} sequential frames from a Valorant gameplay clip, \
extracted at {fps} frames per second in CHRONOLOGICAL ORDER, covering the full clip.

{crop_section}

=== ANALYSIS INSTRUCTIONS (follow carefully) ===

1. KILL COUNTING (HIGHEST PRIORITY):
   - The KILL FEED is in the TOP-RIGHT corner of each frame.
   - Each kill entry looks like: "PlayerName  [weapon icon]  VictimName"
   - New entries appear at the top; older entries fade out after ~5-7 seconds.
   - Scan EVERY frame's kill feed.  Track entries across frames -- the same
     kill will appear in several consecutive frames.  Count only UNIQUE kills
     by the recording player (the one whose POV we see).
   - If the scoreboard is visible, read kills/deaths/assists directly.
   {crop_instruction}

2. AGENT IDENTIFICATION:
   - Ability bar at bottom-center: 4 ability icons unique to each agent.
   - First-person hand/glove model and ability effects are agent-specific.
   - The agent is consistent across all frames (unless spectating after death).

3. MAP IDENTIFICATION:
   - Minimap in top-left shows the map name as text.
   - Environment cues: Ascent=large tower + Italian market, Bind=two teleporters,
     Haven=three bomb sites, Split=vertical ropes + mid vents, Icebox=shipping
     container site + ziplines, Breeze=tropical + open sightlines, Fracture=
     H-shaped zipline bridge, Pearl=underwater city, Lotus=rotating doors +
     breakable walls, Sunset=street + taqueria, Abyss=floating platforms.

4. WEAPON IDENTIFICATION:
   - Gun model on right side of screen.  Vandal=curved magazine + wooden stock,
     Phantom=sleek + straight magazine, Operator=very long bolt-action sniper,
     Sheriff=large revolver, Ghost=semi-auto pistol, Spectre=compact SMG,
     Guardian=single-fire DMR, Marshal=lever-action sniper, Outlaw=double-barrel sniper.
   - Note: Chamber's "Headhunter" ability looks like a Sheriff.  Report as "sheriff".
   - Chamber's "Tour de Force" looks like an Operator.  Report as "operator".

5. ROUND OUTCOME:
   - Round win/loss banner appears after a round ends.
   - Score at top-center: "Team1 -- Team2".

6. CLUTCH DETECTION:
   - If the player is the LAST one alive vs multiple enemies, it's a clutch.
   - Read remaining player counts from the top-center icons (green vs red).

=== SCORING GUIDE ===
1 = menu / loading / agent select / non-gameplay
2-3 = walking around, buying, no action
4-5 = normal gameplay round with 0-1 kills
6-7 = good play: 2+ kills or a nice shot
8-9 = multi-kill (3-4 kills) or clutch situation
10 = ace (5 kills) or an extraordinary play

=== VALID VALUES ===
agents: jett, reyna, phoenix, sage, omen, killjoy, cypher, sova, breach, raze, \
brimstone, viper, skye, yoru, astra, kay/o, chamber, neon, fade, harbor, gekko, \
deadlock, iso, clove, vyse, tejo
maps: bind, haven, split, ascent, icebox, breeze, fracture, pearl, lotus, sunset, abyss
weapons: vandal, phantom, operator, sheriff, ghost, spectre, judge, stinger, guardian, \
marshal, outlaw, odin, ares, bulldog, classic, shorty, frenzy, bucky, knife
highlight_types: ace, clutch, multi-kill, flick, one-tap, spray-transfer, knife-kill, \
wallbang, collateral, eco-ace, pistol-round, thrifty, retake, entry-frag, trade-kill, \
whiff, regular-round, non-gameplay

=== EXAMPLE OUTPUT ===
{{"map":"ascent","player_agent":"jett","kills":3,"deaths":0,"is_ace":false,\
"clutch_type":null,"weapon":"vandal","highlight_type":"multi-kill",\
"round_outcome":"win","score":7,\
"summary":"Jett gets a 3k with the Vandal holding A site on Ascent.",\
"confidence":0.85}}

If frames show menus/loading/agent select, set highlight_type to "non-gameplay" and score to 1."""


# ---------------------------------------------------------------------------
# Validation sets and aliases
# ---------------------------------------------------------------------------

VALID_MAPS = {
    "bind", "haven", "split", "ascent", "icebox", "breeze",
    "fracture", "pearl", "lotus", "sunset", "abyss",
}
VALID_AGENTS = {
    "jett", "reyna", "phoenix", "sage", "omen", "killjoy", "cypher", "sova",
    "breach", "raze", "brimstone", "viper", "skye", "yoru", "astra", "kay/o",
    "chamber", "neon", "fade", "harbor", "gekko", "deadlock", "iso", "clove",
    "vyse", "tejo",
}
VALID_WEAPONS = {
    "vandal", "phantom", "operator", "sheriff", "ghost", "spectre", "judge",
    "stinger", "guardian", "marshal", "outlaw", "odin", "ares", "bulldog",
    "classic", "shorty", "frenzy", "bucky", "knife",
}
VALID_HIGHLIGHT_TYPES = {
    "ace", "clutch", "multi-kill", "flick", "one-tap", "spray-transfer",
    "knife-kill", "wallbang", "collateral", "eco-ace", "pistol-round",
    "thrifty", "retake", "entry-frag", "trade-kill", "whiff",
    "regular-round", "non-gameplay",
}

WEAPON_ALIASES = {
    "melee": "knife", "blade": "knife", "karambit": "knife", "dagger": "knife",
    "headhunter": "sheriff",
    "tour de force": "operator",
    "op": "operator", "awp": "operator", "sniper": "operator",
    "rifle": "vandal",
    "smg": "spectre",
    "shotgun": "judge",
    "pistol": "classic",
    "mb4": "vandal",
}


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class OllamaVisionAnalyzer(ClipAnalyzer):
    """Analyze clips using a local Ollama vision model on GPU.

    By default uses *dense* extraction: frames are pulled at ``fps``
    (default 2 fps) across the entire clip duration so that no kill-feed
    entries or key moments are missed.  Kill-feed crops (top-right corner,
    upscaled 2×) are extracted alongside full frames for better text
    readability.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str | None = None,
        fps: float = DENSE_FPS,
        max_frames: int = MAX_FRAMES,
        max_crops: int = MAX_CROPS,
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
        self.fps = fps
        self.max_frames = max_frames
        self.max_crops = max_crops
        self.auto_pull = auto_pull

        if host:
            self.client = _ollama.Client(host=host)
        else:
            self.client = _ollama.Client()

        self._check_server()
        self._ensure_model()

    # -- Server / model management -----------------------------------------

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

        if self.model in installed:
            return
        if any(self.model in name for name in installed):
            return

        if not self.auto_pull:
            raise RuntimeError(
                f"Model '{self.model}' not found. "
                f"Pull it with: ollama pull {self.model}\n"
                f"Installed models: {', '.join(sorted(installed)) or 'none'}"
            )

        logger.info("Pulling model %s (this may take a few minutes)...", self.model)
        try:
            from rich.progress import (
                Progress, BarColumn, DownloadColumn, TransferSpeedColumn,
            )
            with Progress(
                "[progress.description]{task.description}",
                BarColumn(), DownloadColumn(), TransferSpeedColumn(),
            ) as progress:
                task = None
                for chunk in self.client.pull(self.model, stream=True):
                    status = chunk.get("status", "")
                    total = chunk.get("total", 0)
                    completed = chunk.get("completed", 0)
                    if total and "pulling" in status:
                        if task is None:
                            task = progress.add_task(
                                f"Pulling {self.model}", total=total,
                            )
                        progress.update(task, completed=completed, total=total)
        except ImportError:
            for _ in self.client.pull(self.model, stream=True):
                pass

        logger.info("Model %s ready", self.model)

    # -- Frame extraction --------------------------------------------------

    @staticmethod
    def _get_duration(clip_path: str) -> float | None:
        """Get clip duration via ffprobe."""
        cmd = [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "csv=p=0", clip_path,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return float(r.stdout.strip())
        except Exception:
            return None

    def _extract_frames(
        self, clip_path: str, duration: float | None = None,
    ) -> tuple[list[Path], list[Path], list[Path]]:
        """Extract dense full frames + kill-feed crops.

        Returns ``(full_frames, killfeed_crops, temp_dirs)`` where
        *temp_dirs* should be cleaned up by the caller.
        """
        frame_dir = Path(tempfile.mkdtemp(prefix="valclips_dense_"))
        crop_dir = Path(tempfile.mkdtemp(prefix="valclips_crops_"))
        timeout = max(300, int((duration or 120) * 4))

        # --- Full frames at configured FPS ---
        cmd_frames = [
            "ffmpeg", "-i", clip_path,
            "-vf", f"fps={self.fps}",
            "-q:v", "2",
            str(frame_dir / "frame_%04d.jpg"),
        ]

        # --- Kill-feed crops: top-right 27%×28%, upscaled 2× ---
        crop_vf = (
            f"fps={self.fps},"
            "crop=iw*0.27:ih*0.28:iw*0.73:0,"
            "scale=iw*2:ih*2"
        )
        cmd_crops = [
            "ffmpeg", "-i", clip_path,
            "-vf", crop_vf,
            "-q:v", "1",
            str(crop_dir / "crop_%04d.jpg"),
        ]

        # Run both extractions
        for cmd in (cmd_frames, cmd_crops):
            try:
                subprocess.run(cmd, capture_output=True, timeout=timeout)
            except (subprocess.TimeoutExpired, FileNotFoundError):
                logger.warning("FFmpeg extraction failed for %s", clip_path)

        full_frames = sorted(frame_dir.glob("frame_*.jpg"))
        crops = sorted(crop_dir.glob("crop_*.jpg"))

        # --- Subsample full frames evenly if over limit ---
        if len(full_frames) > self.max_frames:
            step = len(full_frames) / self.max_frames
            keep_idx = {int(i * step) for i in range(self.max_frames)}
            selected = []
            for i, f in enumerate(full_frames):
                if i in keep_idx:
                    selected.append(f)
                else:
                    f.unlink(missing_ok=True)
            full_frames = selected

        # --- Subsample crops to match timestamps of selected frames ---
        if len(crops) > self.max_crops:
            step = len(crops) / self.max_crops
            keep_idx = {int(i * step) for i in range(self.max_crops)}
            selected = []
            for i, f in enumerate(crops):
                if i in keep_idx:
                    selected.append(f)
                else:
                    f.unlink(missing_ok=True)
            crops = selected

        return full_frames, crops, [frame_dir, crop_dir]

    # -- Model interaction -------------------------------------------------

    def _call_model(
        self, images: list[str], prompt: str,
    ) -> OllamaAnalysisResponse:
        """Send images + prompt to Ollama and get structured JSON back."""
        response = self.client.chat(
            model=self.model,
            messages=[{
                "role": "user",
                "content": prompt,
                "images": images,
            }],
            format=OllamaAnalysisResponse.model_json_schema(),
            options={
                "temperature": 0.1,
                "num_predict": 1024,
                "num_ctx": NUM_CTX,
            },
            keep_alive="30m",
        )
        text = response.message.content
        return OllamaAnalysisResponse.model_validate_json(text)

    def _build_prompt(self, n_frames: int, n_crops: int) -> str:
        """Build the analysis prompt with frame/crop counts filled in."""
        if n_crops > 0:
            crop_section = (
                f"You also receive {n_crops} ZOOMED-IN CROPS of the KILL FEED "
                f"region (top-right corner of the screen), upscaled 2× for "
                f"readability.  These are the LAST {n_crops} images in the set.  "
                f"The first {n_frames} images are full frames; images "
                f"{n_frames + 1}–{n_frames + n_crops} are kill-feed crops."
            )
            crop_instruction = (
                f"Use the {n_crops} zoomed kill-feed crops (images "
                f"{n_frames + 1}–{n_frames + n_crops}) to read kill text more "
                f"accurately.  Cross-reference with the full frames."
            )
        else:
            crop_section = ""
            crop_instruction = (
                "Look carefully at the top-right corner of each frame "
                "for kill-feed entries."
            )

        return SYSTEM_PROMPT.format(
            n_frames=n_frames,
            fps=self.fps,
            crop_section=crop_section,
            crop_instruction=crop_instruction,
        )

    # -- Main analysis entry point -----------------------------------------

    def analyze(self, clip_path: str, keyframes: list[Path]) -> AnalysisResult:
        """Analyze a clip using dense frame extraction + local vision model.

        The *keyframes* parameter from the base class is ignored -- this
        analyzer does its own dense extraction for maximum accuracy.
        """
        agent_name = f"ollama:{self.model}"

        # Get duration
        duration = self._get_duration(clip_path)

        # Dense extraction
        frames, crops, temp_dirs = self._extract_frames(clip_path, duration)

        if not frames:
            # Fallback to caller-provided keyframes
            if keyframes:
                frames = keyframes[:self.max_frames]
                crops = []
                temp_dirs = []
            else:
                return AnalysisResult(
                    agent=agent_name,
                    summary="No frames could be extracted",
                    score=1,
                    confidence=0.0,
                )

        try:
            # Build ordered image list: full frames first, then crops
            images = [str(f) for f in frames] + [str(c) for c in crops]
            prompt = self._build_prompt(len(frames), len(crops))

            logger.info(
                "Sending %d frames + %d crops to %s for %s",
                len(frames), len(crops), self.model, clip_path,
            )

            result = self._call_model(images, prompt)

        except Exception as e:
            logger.warning("Ollama analysis failed for %s: %s", clip_path, e)
            return AnalysisResult(
                agent=agent_name,
                summary=f"Analysis failed: {str(e)[:100]}",
                score=1,
                confidence=0.0,
            )
        finally:
            # Always clean up temp directories
            for d in temp_dirs:
                shutil.rmtree(d, ignore_errors=True)

        # -- Post-process --------------------------------------------------
        self._normalize(result)

        conf = result.confidence
        if conf > 1.0:
            conf = conf / 10.0
        conf = max(0.0, min(1.0, conf))

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

    # -- Normalization & tagging -------------------------------------------

    @staticmethod
    def _normalize(r: OllamaAnalysisResponse) -> None:
        """Validate and fix common model misidentifications in-place."""
        if r.map:
            m = r.map.strip().lower().split("-")[0].split("/")[0]
            r.map = m if m in VALID_MAPS else None

        if r.player_agent:
            a = r.player_agent.strip().lower()
            r.player_agent = a if a in VALID_AGENTS else None

        if r.weapon:
            w = r.weapon.strip().lower()
            w = WEAPON_ALIASES.get(w, w)
            r.weapon = w if w in VALID_WEAPONS else None

        if r.highlight_type:
            h = r.highlight_type.strip().lower()
            r.highlight_type = h if h in VALID_HIGHLIGHT_TYPES else "regular-round"

        if r.round_outcome and r.round_outcome.strip().lower() not in ("win", "loss"):
            r.round_outcome = None

        # Logical consistency
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
        if r.highlight_type and r.highlight_type not in (
            "regular-round", "non-gameplay",
        ):
            tags.append(r.highlight_type)
        if r.kills >= 4:
            tags.append("multi-kill")
        if r.kills >= 3:
            tags.append("triple-kill")
        return tags
