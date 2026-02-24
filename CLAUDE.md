# CLAUDE.md

## Project Overview

**valclips** is a personal Valorant gameplay clip manager. It indexes video recordings (DVR clips and replays) from local and SMB-mounted network shares, extracts metadata via FFmpeg, generates thumbnails, and provides AI-powered analysis of gameplay content. It includes both a CLI and a web UI for browsing, searching, filtering, tagging, and viewing clips.

## Tech Stack

- **Language**: Python 3.12+
- **Build system**: Hatchling (PEP 517, configured in `pyproject.toml`)
- **CLI framework**: Click + Rich (tables, progress bars, console formatting)
- **Web framework**: FastAPI + Uvicorn + Jinja2 templates
- **Database**: SQLite (WAL mode, stored as `clips.db` at project root)
- **Data validation**: Pydantic v2
- **Video processing**: FFmpeg / FFprobe (external system dependency)
- **AI backends**:
  - Google Gemini Vision (free tier, primary analyzer) via raw HTTP (httpx)
  - Anthropic Claude Vision via `anthropic` SDK
  - FFmpeg heuristic scoring (no API needed)
  - Stub analyzer (for testing)
- **Frontend**: Vanilla JS + Pico CSS (dark theme), no build step
- **Testing**: pytest

## Directory Structure

```
valorant-clips/
  pyproject.toml          # Package metadata, dependencies, entry point
  clips.db                # SQLite database (gitignored, generated at runtime)
  thumbnails/             # Generated JPEG thumbnails + WebP animated previews
  top_clips_frames/       # Extracted keyframes from top clips (for analysis)
  static/
    app.js                # Frontend JavaScript (gallery, filters, pagination, video player)
    style.css             # Custom dark-theme styles (Valorant red accent)
  src/valclips/
    __init__.py
    cli.py                # Click CLI: scan, list, search, info, tag, analyze, serve, etc.
    config.py             # Paths, SMB share config, clip directories, constants
    models.py             # Pydantic models: Clip, Tag, ScanResult, AnalysisResult, ClipPage
    db.py                 # SQLite schema, migrations, CRUD, queries, session grouping
    scanner.py            # File discovery across SMB shares + ffprobe metadata extraction
    parser.py             # Filename parsing for DVR/replay patterns + date extraction
    thumbnails.py         # FFmpeg thumbnail + animated WebP preview generation
    extractor.py          # Highlight segment detection + extraction from DVR recordings
    ai/
      __init__.py
      base.py             # ClipAnalyzer abstract base class
      highlights.py       # Scene change detection, activity region scoring, frame extraction
      keyframes.py        # Evenly-spaced keyframe extraction from video
      stub.py             # StubAnalyzer (dummy results for testing)
      ffmpeg_analyzer.py  # FFmpegHeuristicAnalyzer (scene density + metadata scoring)
      claude_analyzer.py  # ClaudeVisionAnalyzer (Anthropic Claude API)
      gemini_analyzer.py  # GeminiAnalyzer (Google Gemini Vision API, primary)
    web/
      __init__.py
      app.py              # FastAPI app factory
      routes.py           # HTML page routes + JSON API + video streaming proxy
      templates/
        base.html         # Base layout (Pico CSS, nav bar)
        index.html        # Gallery page with filters, search, timeline, score chart
        clip.html         # Clip detail page with video player, metadata, tags, related clips
        top.html          # Top-scored clips page
        sessions.html     # Gaming sessions grouped by time
        insights.html     # Analytics dashboard (agent/map/weapon stats)
  tests/
    test_parser.py        # Filename parsing tests
    test_db.py            # Database CRUD and query tests
```

## Key Commands

### Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Running
```bash
# Scan clips from configured SMB shares
valclips scan

# Scan a specific directory
valclips scan --dir /path/to/clips

# Generate thumbnails for indexed clips
valclips thumbnails

# Run AI analysis (multiple backends)
valclips analyze --gemini        # Gemini Vision (needs GEMINI_API_KEY)
valclips analyze --ffmpeg        # FFmpeg heuristics (free, no API)
valclips analyze --quick         # Metadata-only scoring (instant)
valclips analyze                 # Claude Vision (needs ANTHROPIC_API_KEY)
valclips analyze --gemini -w 3   # Parallel workers

# Start the web UI
valclips serve                   # http://0.0.0.0:8000

# CLI browsing
valclips list --sort date
valclips search "ace"
valclips info 42
valclips top
valclips sessions
valclips stats
valclips tags

# Tagging
valclips tag 42 ace clutch
valclips untag 42 ace

# Duplicate detection
valclips dupes --mark

# Highlight extraction from DVR recordings
valclips extract 42
valclips extract-top --min-score 6

# Export
valclips export --format json -o clips.json
```

### Testing
```bash
pytest                           # Run all tests
pytest tests/test_parser.py      # Run specific test file
pytest -v                        # Verbose output
```

## Environment Variables

- `GEMINI_API_KEY` - Required for Gemini Vision analysis (free tier available at https://aistudio.google.com/apikey)
- `ANTHROPIC_API_KEY` - Required for Claude Vision analysis

## Architecture Notes

### Database
- SQLite with WAL mode for concurrent reads. Schema is in `db.py` with incremental `ALTER TABLE` migrations.
- All queries use parameterized SQL. The `list_clips()` function builds dynamic WHERE clauses from filter parameters.
- Clip deduplication uses filename + file_size + duration matching.

### Scanner
- Scans only pre-configured directories within SMB shares (not full share walks) for performance.
- Supports two modes: `valorant_only` (pattern-matched filenames) and `all_mp4` (all .mp4 files).
- Incremental by default -- skips files already indexed with the same size.

### AI Analysis Pipeline
1. `highlights.py` detects high-activity segments via FFmpeg scene change analysis.
2. `keyframes.py` or `highlights.py` extracts representative frames from those segments.
3. Frames are sent to the chosen analyzer (Gemini, Claude, or FFmpeg heuristic).
4. Results (score, kills, agent, map, weapon, clutch type, etc.) are stored in the `clips` table.

### Web UI
- Server-rendered HTML pages with client-side JavaScript for dynamic gallery loading.
- Video streaming endpoint (`/api/clips/{id}/stream`) supports HTTP Range requests for seeking.
- Gallery supports filters for score, kills, highlight type, map, weapon, agent, clutch type, and aces.
- Hover previews use animated WebP files when available.

### Filename Patterns
- DVR: `Valorant YYYY.MM.DD - HH.MM.SS.NN.DVR.mp4` (ShadowPlay instant replay)
- Replay: `VALORANT_replay_YYYY.MM.DD-HH.MM.mp4` (in-game replay recording)

## Coding Conventions

- Type hints used throughout (Python 3.12+ union syntax: `str | None`).
- Pydantic v2 models for data validation and serialization.
- Module-level constants in UPPER_SNAKE_CASE.
- Click decorators for CLI commands; each command is a function in `cli.py`.
- SQLite connections are managed via a `get_connection()` context manager.
- No ORM -- raw SQL with `sqlite3.Row` for dict-like access.
- Tests use pytest fixtures with temporary database paths.
- Frontend uses vanilla JavaScript (no framework, no build step).

## Important Notes

- The `clips.db` file and `thumbnails/` directory contain generated data and should not be committed.
- FFmpeg and FFprobe must be installed on the system for scanning, thumbnails, and analysis.
- SMB share paths in `config.py` are specific to the author's Linux GVFS setup. Use `--dir` for other environments.
- Gemini free tier has rate limits (approximately 15 RPM). The analyzer includes built-in rate limiting and exponential backoff.
- The web UI binds to `0.0.0.0:8000` by default (all interfaces).
