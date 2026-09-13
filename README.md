# valclips

Index, browse, tag, and AI-analyze 2,800+ Valorant clips scattered across network shares.

> **Status (2026-09-13): stalled, under reassessment.** The web UI currently
> returns HTTP 500 on every page (dependency rot — a five-argument fix), and the
> OpenCV analyzer described below does not work: it scores the same frame `1` at
> 1080p and `9 / ace` upscaled to 4K. See [`CLAUDE.md`](CLAUDE.md) for the
> verified state, [`docs/NEXT-STEPS.md`](docs/NEXT-STEPS.md) for the plan, and
> [`docs/research/`](docs/research/) for the research behind it.

Clips stay in place on GVFS-mounted Samba shares -- valclips only indexes their locations in a local SQLite database, extracts metadata via ffprobe, generates thumbnails, and runs computer vision analysis to detect kills, aces, clutches, and round outcomes.

## Features

- **Scanner** -- Discovers `.mp4` clips across multiple SMB shares, parses NVIDIA ShadowPlay DVR filenames for timestamps, extracts video metadata via ffprobe
- **Local CV Analyzer** -- OpenCV-based HUD pixel analysis detects kills (kill feed red bands, yellow kill markers), aces (5+ kills), clutch situations (alive player counting from top bar), round outcomes (green/red text), and map identification (color heuristics). No API keys needed.
- **Web UI** -- FastAPI + Jinja2 dashboard with thumbnail grid, video streaming proxy with Range/seek support, filtering by score/tags/date, clip detail pages
- **CLI** -- Full Click CLI for scanning, browsing, tagging, analysis, highlight extraction, session grouping, and export
- **Multiple analyzers** -- Local CV (fast, free), Gemini Vision (accurate, free tier), FFmpeg heuristics, Claude Vision

## Stack

Python 3.12, SQLite (WAL mode), FastAPI, Click, OpenCV, Rich

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Requires `ffmpeg` and `ffprobe` on PATH.

## Usage

```bash
# Scan and index clips from SMB shares
valclips scan

# Analyze all clips with local CV (no API needed)
valclips analyze --local --all -w 4

# Generate thumbnails
valclips thumbnails

# Browse top clips
valclips top -n 20

# Start web UI
valclips serve

# See all commands
valclips --help
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `scan` | Discover and index clips from SMB shares |
| `list` | Browse clips with filters (date, tag, share, sort) |
| `search` | Search filenames, tags, directories |
| `info ID` | Full metadata for one clip |
| `top` | Show highest-scoring clips |
| `open ID` | Open clip in system video player |
| `tag ID TAG` | Add manual tags |
| `untag ID TAG` | Remove a tag |
| `tags` | List all tags with counts |
| `stats` | Summary statistics |
| `sessions` | Gaming sessions grouped by time |
| `thumbnails` | Generate missing thumbnails |
| `previews` | Generate animated WebP previews |
| `analyze` | Run AI analysis (--local, --gemini, --ffmpeg, --quick) |
| `extract ID` | Extract highlight segment from DVR recording |
| `extract-top` | Batch extract highlights from top clips |
| `dupes` | Find and mark duplicate clips |
| `export` | Export as JSON or CSV |
| `serve` | Start web UI |

## Analysis Scoring

| Score | Criteria |
|-------|----------|
| 10 | Ace + clutch |
| 9-10 | Ace (5+ kills) |
| 8-10 | Clutch (1vN with kills to back it up) |
| 8 | 4K |
| 7 | 3K |
| 5-6 | 2K |
| 3-4 | 1K |
| 1-2 | No kills / non-gameplay |

## Project Structure

```
src/valclips/
  cli.py          -- Click CLI entry point
  config.py       -- Paths, share configs, constants
  db.py           -- SQLite schema + queries
  models.py       -- Pydantic models
  parser.py       -- Filename -> date/metadata parsing
  scanner.py      -- File discovery + ffprobe
  thumbnails.py   -- FFmpeg thumbnail/preview extraction
  extractor.py    -- Highlight segment extraction
  ai/
    local_analyzer.py   -- OpenCV HUD analysis (kills, clutches, aces)
    gemini_analyzer.py  -- Google Gemini Vision
    ffmpeg_analyzer.py  -- FFmpeg scene-change heuristics
    keyframes.py        -- Frame extraction
    highlights.py       -- Scene-change detection
  web/
    app.py         -- FastAPI app factory
    routes.py      -- API + page routes
    templates/     -- Jinja2 HTML templates
static/
  style.css        -- Pico CSS + custom styles
  app.js           -- Vanilla JS for gallery interactions
```
