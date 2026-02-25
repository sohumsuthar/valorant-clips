"""API + page routes + video streaming proxy."""

import os
import re
import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..config import TEMPLATE_DIR, DEFAULT_PAGE_SIZE
from ..db import (
    get_connection, list_clips, get_clip, get_adjacent_clip_ids,
    add_tag, remove_tag, get_all_tags, get_stats, get_timeline,
    get_top_clips, get_sessions, get_filter_options, get_related_clips,
    get_insights,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def _format_clip_name(filename: str) -> str:
    """Clean up Valorant clip filenames for display."""
    # DVR: "Valorant YYYY.MM.DD - HH.MM.SS.NN.DVR.mp4"
    m = re.match(
        r"Valorant\s+(\d{4})\.(\d{2})\.(\d{2})\s*-\s*(\d{2})\.(\d{2})\.\d+\.\d+\.DVR\.mp4",
        filename, re.IGNORECASE,
    )
    if m:
        y, mo, d, h, mi = m.groups()
        return f"DVR {y}-{mo}-{d} {h}:{mi}"
    # Replay: "VALORANT_replay_YYYY.MM.DD-HH.MM.mp4"
    m = re.match(
        r"VALORANT_replay_(\d{4})\.(\d{2})\.(\d{2})-(\d{2})\.(\d{2})\.mp4",
        filename, re.IGNORECASE,
    )
    if m:
        y, mo, d, h, mi = m.groups()
        return f"Replay {y}-{mo}-{d} {h}:{mi}"
    return filename.removesuffix(".mp4").removesuffix(".MP4")


templates.env.filters["format_clip_name"] = _format_clip_name


# ---- HTML Pages ----

@router.get("/", response_class=HTMLResponse)
def index_page(request: Request):
    with get_connection() as conn:
        stats = get_stats(conn)
        all_tags = get_all_tags(conn)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "stats": stats,
        "tags": all_tags,
        "active_page": "gallery",
    })


@router.get("/clips/{clip_id}", response_class=HTMLResponse)
def clip_page(request: Request, clip_id: int):
    with get_connection() as conn:
        clip = get_clip(conn, clip_id)
        if not clip:
            raise HTTPException(404, "Clip not found")
        prev_id, next_id = get_adjacent_clip_ids(conn, clip_id)
        related = get_related_clips(conn, clip_id, limit=8)
    return templates.TemplateResponse("clip.html", {
        "request": request,
        "clip": clip,
        "prev_id": prev_id,
        "next_id": next_id,
        "related": related,
        "active_page": "clip",
    })


# ---- JSON API ----

@router.get("/api/clips")
def api_list_clips(
    page: int = Query(1, ge=1),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=200),
    sort: str = Query("date"),
    tag: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    share: str | None = None,
    search: str | None = None,
    hide_dupes: bool = Query(False),
    score_min: int | None = None,
    highlight_type: str | None = None,
    map_name: str | None = None,
    clutch_type: str | None = None,
    weapon: str | None = None,
    player_agent: str | None = None,
    aces_only: bool = Query(False),
    kills_min: int | None = None,
):
    with get_connection() as conn:
        result = list_clips(
            conn, page=page, page_size=limit, sort=sort,
            tag=tag, date_from=date_from, date_to=date_to,
            share=share, search=search, hide_dupes=hide_dupes,
            score_min=score_min, highlight_type=highlight_type,
            map_name=map_name, clutch_type=clutch_type,
            weapon=weapon, player_agent=player_agent,
            aces_only=aces_only, kills_min=kills_min,
        )
    return result.model_dump()


@router.get("/api/clips/{clip_id}")
def api_get_clip(clip_id: int):
    with get_connection() as conn:
        clip = get_clip(conn, clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")
    return clip.model_dump()


@router.post("/api/clips/{clip_id}/tags")
async def api_add_tag(clip_id: int, request: Request):
    body = await request.json()
    tag_name = body.get("name", "").strip().lower()
    if not tag_name:
        raise HTTPException(400, "Tag name required")
    with get_connection() as conn:
        clip = get_clip(conn, clip_id)
        if not clip:
            raise HTTPException(404, "Clip not found")
        add_tag(conn, clip_id, tag_name)
    return {"ok": True, "tag": tag_name}


@router.delete("/api/clips/{clip_id}/tags/{tag_name}")
def api_remove_tag(clip_id: int, tag_name: str):
    with get_connection() as conn:
        remove_tag(conn, clip_id, tag_name.lower().strip())
    return {"ok": True}


@router.get("/api/tags")
def api_tags():
    with get_connection() as conn:
        return get_all_tags(conn)


@router.get("/api/stats")
def api_stats():
    with get_connection() as conn:
        return get_stats(conn)


@router.get("/api/timeline")
def api_timeline():
    with get_connection() as conn:
        return get_timeline(conn)


@router.get("/api/filters")
def api_filters():
    with get_connection() as conn:
        return get_filter_options(conn)


@router.get("/api/top")
def api_top(limit: int = Query(20, ge=1, le=100)):
    with get_connection() as conn:
        clips = get_top_clips(conn, limit=limit)
    return [c.model_dump() for c in clips]


@router.get("/api/sessions")
def api_sessions(limit: int = Query(50, ge=1, le=200), gap: int = Query(30, ge=5, le=180)):
    with get_connection() as conn:
        return get_sessions(conn, gap_minutes=gap)[:limit]


@router.post("/api/clips/{clip_id}/analyze")
async def api_analyze_clip(clip_id: int):
    """Trigger re-analysis of a single clip via Gemini."""
    import os
    with get_connection() as conn:
        clip = get_clip(conn, clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise HTTPException(503, "GEMINI_API_KEY not set")

    try:
        from ..ai.gemini_analyzer import GeminiAnalyzer
        from ..ai.highlights import get_clip_highlights

        analyzer = GeminiAnalyzer(api_key=api_key)
        segments, frames = get_clip_highlights(
            clip.file_path, duration=clip.duration_seconds, max_frames=8,
        )
        ar = analyzer.analyze(clip.file_path, frames)

        with get_connection() as conn:
            from ..db import update_clip_ai
            update_clip_ai(
                conn, clip_id, agent=ar.agent,
                map_name=ar.map_name, summary=ar.summary,
                tags=ar.tags, score=ar.score, kills=ar.kills,
                highlight_type=ar.highlight_type,
                clutch_type=ar.clutch_type, weapon=ar.weapon,
                player_agent=ar.player_agent, deaths=ar.deaths,
                is_ace=ar.is_ace,
                round_outcome=ar.round_outcome,
                confidence=ar.confidence,
            )

        # Clean up frames
        for f in frames:
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass

        return {"ok": True, "score": ar.score, "summary": ar.summary}
    except Exception as e:
        raise HTTPException(500, f"Analysis failed: {str(e)[:200]}")


@router.get("/api/clips/{clip_id}/cv")
def api_get_cv(clip_id: int):
    """Get CV analysis detail JSON for a clip."""
    with get_connection() as conn:
        clip = get_clip(conn, clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")
    if not clip.cv_detail_json:
        raise HTTPException(404, "No CV analysis available")
    import json
    return json.loads(clip.cv_detail_json)


@router.post("/api/clips/{clip_id}/cv-analyze")
async def api_cv_analyze_clip(clip_id: int):
    """Trigger CV mechanics analysis for a single clip."""
    with get_connection() as conn:
        clip = get_clip(conn, clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")

    try:
        from ..ai.cv_analyzer import CVFrameAnalyzer
        from ..db import update_clip_cv

        analyzer = CVFrameAnalyzer()
        result = analyzer.analyze(clip.file_path)

        with get_connection() as conn:
            update_clip_cv(
                conn, clip_id,
                flick_count=len(result.flicks),
                avg_flick_speed=result.avg_flick_speed,
                max_flick_speed=result.max_flick_speed,
                fastest_flick_ms=result.fastest_flick_ms,
                crosshair_score=result.crosshair_score,
                counter_strafe_count=result.counter_strafe_count,
                avg_reaction_ms=result.avg_reaction_ms,
                min_reaction_ms=result.min_reaction_ms,
                movement_intensity=result.movement_intensity,
                detail_json=result.to_json(),
            )

        return {
            "ok": True,
            "flick_count": len(result.flicks),
            "crosshair_score": result.crosshair_score,
            "max_flick_speed": result.max_flick_speed,
            "min_reaction_ms": result.min_reaction_ms,
        }
    except Exception as e:
        raise HTTPException(500, f"CV analysis failed: {str(e)[:200]}")


@router.get("/api/insights")
def api_insights():
    with get_connection() as conn:
        return get_insights(conn)


@router.get("/insights", response_class=HTMLResponse)
def insights_page(request: Request):
    with get_connection() as conn:
        insights = get_insights(conn)
        stats = get_stats(conn)
    return templates.TemplateResponse("insights.html", {
        "request": request,
        "insights": insights,
        "stats": stats,
        "active_page": "insights",
    })


@router.get("/top", response_class=HTMLResponse)
def top_page(request: Request):
    with get_connection() as conn:
        clips = get_top_clips(conn, limit=50)
        stats = get_stats(conn)
    return templates.TemplateResponse("top.html", {
        "request": request,
        "clips": clips,
        "stats": stats,
        "active_page": "top",
    })


@router.get("/sessions", response_class=HTMLResponse)
def sessions_page(request: Request):
    with get_connection() as conn:
        sessions = get_sessions(conn)
        stats = get_stats(conn)
    return templates.TemplateResponse("sessions.html", {
        "request": request,
        "sessions": sessions[:100],
        "stats": stats,
        "active_page": "sessions",
    })


# ---- Video Streaming with Range Support ----

CHUNK_SIZE = 1024 * 1024  # 1MB chunks


@router.get("/api/clips/{clip_id}/stream")
def stream_clip(clip_id: int, request: Request):
    """Stream a clip with HTTP Range support for seeking."""
    with get_connection() as conn:
        clip = get_clip(conn, clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")

    file_path = Path(clip.file_path)
    if not file_path.exists():
        raise HTTPException(404, "File not accessible")

    file_size = file_path.stat().st_size
    content_type = mimetypes.guess_type(str(file_path))[0] or "video/mp4"

    range_header = request.headers.get("range")
    if range_header:
        # Parse Range: bytes=START-END
        range_spec = range_header.replace("bytes=", "").strip()
        parts = range_spec.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if parts[1] else file_size - 1
        end = min(end, file_size - 1)
        length = end - start + 1

        def iter_range():
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return StreamingResponse(
            iter_range(),
            status_code=206,
            media_type=content_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(length),
            },
        )
    else:
        def iter_file():
            with open(file_path, "rb") as f:
                while chunk := f.read(CHUNK_SIZE):
                    yield chunk

        return StreamingResponse(
            iter_file(),
            media_type=content_type,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
            },
        )
