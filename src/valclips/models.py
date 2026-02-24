"""Pydantic models for clips, tags, and scan results."""

from datetime import datetime
from pydantic import BaseModel


class Clip(BaseModel):
    id: int | None = None
    file_path: str
    filename: str
    recorded_at: datetime | None = None
    clip_sequence: int | None = None  # NN from DVR filename
    source_format: str | None = None  # "dvr" or "replay"
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    codec: str | None = None
    file_size_bytes: int | None = None
    bitrate: int | None = None
    share_name: str | None = None
    directory: str | None = None
    thumbnail_path: str | None = None
    ai_agent: str | None = None
    ai_map: str | None = None
    ai_summary: str | None = None
    ai_score: int | None = None
    ai_kills: int | None = None
    ai_highlight_type: str | None = None
    ai_clutch_type: str | None = None       # 1v1, 1v2, 1v3, 1v4, 1v5
    ai_weapon: str | None = None            # vandal, phantom, operator, etc.
    ai_player_agent: str | None = None      # jett, reyna, etc.
    ai_deaths: int | None = None
    ai_is_ace: int | None = None            # 1 if ace
    ai_round_outcome: str | None = None     # win, loss
    ai_confidence: float | None = None
    ai_analyzed_at: datetime | None = None
    duplicate_of: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    tags: list[str] = []


class Tag(BaseModel):
    id: int | None = None
    clip_id: int
    name: str
    source: str = "manual"  # manual | ai | folder


class ScanResult(BaseModel):
    started_at: datetime
    finished_at: datetime | None = None
    clips_found: int = 0
    clips_new: int = 0
    clips_updated: int = 0
    errors: int = 0
    status: str = "running"


class AnalysisResult(BaseModel):
    agent: str                          # analyzer name
    map_name: str | None = None
    tags: list[str] = []
    summary: str | None = None
    confidence: float | None = None
    score: int | None = None            # 1-10 impressiveness
    kills: int | None = None            # kills detected in clip
    deaths: int | None = None
    highlight_type: str | None = None   # ace, clutch, multi-kill, flick, etc.
    clutch_type: str | None = None      # 1v1, 1v2, 1v3, 1v4, 1v5
    weapon: str | None = None           # primary weapon used
    player_agent: str | None = None     # player's valorant agent
    is_ace: bool = False
    round_outcome: str | None = None    # win, loss


class ClipPage(BaseModel):
    clips: list[Clip]
    total: int
    page: int
    page_size: int
    pages: int
