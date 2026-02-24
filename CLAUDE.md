# Valorant Clip Manager

## What This Is

A Python system to index, browse, tag, and AI-analyze ~2,800 Valorant gameplay clips stored across GVFS-mounted Samba shares on `chaos.local`. Clips are NVIDIA ShadowPlay DVR recordings (`.mp4`) that stay in place on the network shares -- this system only indexes their locations.

## Architecture

- **CLI** (`valclips`): Click-based, entry point at `src/valclips/cli.py`
- **Database**: SQLite with WAL mode at `clips.db` (gitignored, generated at runtime)
- **Web UI**: FastAPI + Jinja2 templates + vanilla JS, no build step
- **AI Analysis**: Multiple backends in `src/valclips/ai/`:
  - `local_analyzer.py` -- Primary. OpenCV HUD pixel analysis. Detects kills, aces, clutches, round outcomes, maps. No API keys.
  - `gemini_analyzer.py` -- Google Gemini Vision (most accurate, free tier quota limited)
  - `ffmpeg_analyzer.py` -- Scene-change heuristics (fast, no API)
  - `claude_analyzer.py` -- Anthropic Claude Vision

## Key Paths

- `src/valclips/config.py` -- Share paths, clip directories, folder-tag mappings, constants
- `src/valclips/db.py` -- Schema, all SQL queries, `get_connection()` context manager
- `src/valclips/models.py` -- Pydantic models: `Clip`, `Tag`, `AnalysisResult`, `ClipPage`
- `src/valclips/scanner.py` -- File discovery across SMB shares + ffprobe metadata
- `src/valclips/parser.py` -- Filename parsing for DVR/replay timestamps
- `src/valclips/web/routes.py` -- All API + page routes, video streaming proxy

## Network Shares

Shares mount via GVFS at `/run/user/1000/gvfs/smb-share:server=chaos.local,share={name}`:
- **prometheus**: `poseidon/theta/` subdirectories
- **kronos**: `theta/` and `data/` subdirectories
- **atlas**: No Valorant clips

## Conventions

- All DB access uses `with get_connection() as conn:` context manager
- Analysis results go through `update_clip_ai()` in db.py
- Frame extraction: `extract_keyframes()` for local analyzer (fast, evenly-spaced), `get_clip_highlights()` for vision APIs (scene-change based)
- Tags are lowercase, stored in `tags` table with source field (manual/ai/folder)
- Clips table has `ai_*` columns for analysis results and `duplicate_of` for dedup

## Running

```bash
source .venv/bin/activate    # venv at project root
valclips scan                # index clips
valclips analyze --local --all -w 4   # analyze with OpenCV
valclips serve               # web UI on :8000
```

## Dependencies

Requires `ffmpeg`/`ffprobe` on PATH. Python deps in `pyproject.toml`. OpenCV is headless (no GUI).
