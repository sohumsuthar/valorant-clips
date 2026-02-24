# valclips

A personal Valorant gameplay clip manager. Index, browse, tag, and AI-analyze your Valorant recordings from local directories or network shares.

## Features

- **Clip Indexing** -- Discover `.mp4` recordings from local directories or GVFS-mounted SMB shares. Parses ShadowPlay DVR and Valorant replay filename patterns to extract recording dates and metadata. Probes video files with FFprobe for duration, resolution, FPS, codec, and bitrate.

- **Web UI** -- Browse your clip library in a dark-themed gallery with thumbnails, search, tag filtering, date range selection, and pagination. View individual clips with an in-browser video player (HTTP Range support for seeking). Filter by AI score, kill count, highlight type, map, weapon, agent, clutch type, or aces.

- **AI Analysis** -- Analyze gameplay clips using multiple backends:
  - **Gemini Vision** (free tier) -- Sends extracted keyframes to Google Gemini for detailed gameplay analysis: map, agent, weapon, kills, deaths, aces, clutches, highlight type, and an impressiveness score (1--10).
  - **Claude Vision** -- Same approach using Anthropic's Claude API.
  - **FFmpeg Heuristics** -- Scores clips based on scene change density, bitrate variance, folder curation signals, and duration -- no API key needed.
  - **Quick Mode** -- Metadata-only scoring for instant results.

- **Thumbnails & Previews** -- Generate JPEG thumbnails and animated WebP hover previews from the most action-packed moments of each clip.

- **Highlight Extraction** -- Detect the most active segment within a long DVR recording using scene change analysis, then export just that portion as a trimmed clip.

- **Tagging** -- Manual and automatic tagging. Folder-based auto-tags (e.g., clips in a `best/` folder get tagged `highlight`). AI analysis generates tags for agents, weapons, maps, and play types.

- **Duplicate Detection** -- Find and mark duplicate clips across shares by matching filename, file size, and duration.

- **Gaming Sessions** -- Group clips into sessions by recording time proximity. View session summaries with clip counts, total duration, and best scores.

- **Insights Dashboard** -- Aggregate statistics across your library: agent performance, map win rates, weapon usage, kill distribution, and overall gameplay stats.

- **CLI** -- Full-featured command-line interface for all operations: scanning, browsing, searching, tagging, analysis, export, and more.

## Requirements

- Python 3.12+
- FFmpeg and FFprobe (for video probing, thumbnails, and analysis)

## Installation

```bash
git clone <repo-url>
cd valorant-clips
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Quick Start

```bash
# Index clips from a directory
valclips scan --dir /path/to/your/clips

# Generate thumbnails
valclips thumbnails

# Run AI analysis (pick one)
export GEMINI_API_KEY="your-key"       # Free at https://aistudio.google.com/apikey
valclips analyze --gemini              # Gemini Vision (best accuracy)
valclips analyze --ffmpeg              # FFmpeg heuristics (no API needed)
valclips analyze --quick               # Metadata-only (instant)

# Start the web UI
valclips serve
# Open http://localhost:8000
```

## CLI Reference

| Command | Description |
|---------|-------------|
| `valclips scan` | Discover and index clips from shares or directories |
| `valclips list` | Browse indexed clips with sorting and filtering |
| `valclips search <query>` | Search filenames, tags, and directories |
| `valclips info <id>` | Show full metadata for a clip |
| `valclips open <id>` | Open a clip in the system video player |
| `valclips tag <id> <tags...>` | Add tags to a clip |
| `valclips untag <id> <tag>` | Remove a tag from a clip |
| `valclips tags` | List all tags with counts |
| `valclips analyze` | Run AI analysis on unanalyzed clips |
| `valclips top` | Show the highest-scoring clips |
| `valclips sessions` | Show gaming sessions grouped by time |
| `valclips stats` | Show library summary statistics |
| `valclips dupes` | Find and optionally mark duplicates |
| `valclips thumbnails` | Generate missing thumbnails |
| `valclips previews` | Generate animated WebP previews for top clips |
| `valclips extract <id>` | Extract the highlight segment from a DVR recording |
| `valclips extract-top` | Extract highlights from the top-scored DVR clips |
| `valclips export` | Export clip data as JSON or CSV |
| `valclips serve` | Start the web UI |
| `valclips random` | Show a random clip |

## Web UI Pages

- **Gallery** (`/`) -- Thumbnail grid with search, filters, timeline chart, and score distribution.
- **Clip Detail** (`/clips/<id>`) -- Video player, metadata panel, tags, AI analysis results, and related clips.
- **Top Clips** (`/top`) -- Ranked list of highest-scoring clips.
- **Sessions** (`/sessions`) -- Gaming sessions grouped by recording time.
- **Insights** (`/insights`) -- Analytics dashboard with agent, map, weapon, and performance stats.

## Environment Variables

| Variable | Required For | Description |
|----------|-------------|-------------|
| `GEMINI_API_KEY` | `--gemini` analysis | Google Gemini API key ([free tier](https://aistudio.google.com/apikey)) |
| `ANTHROPIC_API_KEY` | Default analysis | Anthropic Claude API key |

## Tech Stack

Python 3.12, FastAPI, Uvicorn, Click, Rich, Pydantic v2, SQLite (WAL mode), Jinja2, FFmpeg, vanilla JavaScript, Pico CSS.
