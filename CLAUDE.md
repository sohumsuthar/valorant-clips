# Valorant Clip Manager

## What This Is

A Python system to index, browse, tag, and analyze a personal archive of Valorant
gameplay (NVIDIA ShadowPlay DVR `.mp4` recordings, ~2023-2026). Clips stay where
they are; this indexes their locations into a local SQLite database.

**Status: stalled since 2026-03-03.** 7,303 lines landed in a single commit on
2026-02-24, one 1-line fix followed, then six months of silence. A 31-agent audit
on 2026-09-13 established what actually works and what never did — see
`docs/research/` for the full reports and `docs/research/README.md` for the index.

Read this file before planning work here. Several things that look finished are
not, and one central component is actively misleading.

## Verified state (2026-09-13, by execution, not inspection)

### Works
`scan --dir`, ffprobe metadata extraction, incremental re-scan skip, filename
parsing (DVR + replay), folder auto-tagging, thumbnails, `list`/`search`/`info`/
`stats`/`tags`/`dupes`/`sessions`/`top`, `export -o <file>`, the JSON API, and
Range-based video streaming (verified HTTP 206 with correct `Content-Range`).
SQL is parameterised throughout; no injection. Concurrent `analyze -w N` writes
are safe — 8 threads x 25 writes, 0 errors.

The core is not broken. It is misconfigured for anything but the original Linux
GVFS host.

### Broken
1. **Every HTML page returns HTTP 500.** `TemplateResponse(name, context)` is the
   removed legacy signature. Five call sites: `web/routes.py:30,45,216,228,240`.
   Pure dependency rot — pinning `starlette 0.38.6` makes all five return 200.
   Fixing this is a five-argument swap and is the highest-value hour in the repo.
2. **`config.py:19`** gates share discovery on `sys.platform == "linux"`, so
   `SHARES` is empty everywhere else. `scan --dir` works regardless.
3. **`export` to stdout** crashes after writing (`cli.py:363` passes `err=` to
   `Console.print`, which has no such parameter). Proof that path never ran.
4. **One malformed filename aborts an entire scan.** `parser.py:23` builds a
   `datetime` with no validation; the exception propagates uncaught through
   `scanner.py:246`. Orphans the `scan_log` row at `status='running'`.
5. **`--force` rescan silently destroys AI analysis.** `db.py:159-162` clobbers
   `ai_agent`/`ai_map`/`ai_summary`/`ai_analyzed_at` with `None` but leaves
   `ai_score`/`ai_kills`/`ai_is_ace` intact, producing rows that are internally
   inconsistent — re-queued by `clips_without_analysis()`, dropped by
   `get_insights()`, still ranked by `list --sort score`.
6. **`_ffprobe` failures are invisible** (`scanner.py:44-61` → `:250`): the clip
   is indexed with null metadata and `errors` is not incremented.

### Do not trust `ai/local_analyzer.py`
It is not merely inaccurate, it is non-functional, and its confident docstring
is the most dangerous thing in the repo:

- The same frame scores **1** at 1080p and **9 / ace / 8 kills** upscaled to 4K,
  because `yellow_sum > 500` (`local_analyzer.py:156`) is an absolute pixel count
  with no area normalisation.
- FFmpeg's `testsrc2` colour-bar pattern classifies as
  `is_gameplay=True, allies_alive=5, round_outcome='win'`.
- `detect_scene_changes` (`ai/highlights.py:43-48`) returns **zero** detections on
  continuous gameplay *and* on five hard cuts, so `find_best_segment` always
  falls back and `extract` has always returned the first ~35 seconds of every
  clip. The "activity: 50%" it prints is a hard-coded constant.
- The calibration docstring at `:60-63` cites thresholds "calibrated from real
  clips". There is no calibration set, no fixtures, and no ground truth anywhere
  in the repo, and that docstring was never modified after the minute the file
  was created.

Delete it rather than tune it. The recall figure that killed the project is a
property of `num_frames=8` at `:390`, not of the footage.

## Architecture

- **CLI** (`valclips`): Click, entry at `src/valclips/cli.py`
- **Database**: SQLite WAL at `clips.db` (gitignored). Schema `db.py:11-64`,
  migrations `db.py:85-101`
- **Web UI**: FastAPI + Jinja2 + vanilla JS, no build step
- **AI**: `src/valclips/ai/` — see the warning above

## Key Paths

- `src/valclips/config.py` — share paths, clip dirs, folder-tag mappings
- `src/valclips/db.py` — schema and every query, `get_connection()` context manager
- `src/valclips/models.py` — `Clip`, `Tag`, `AnalysisResult`, `ClipPage`
- `src/valclips/scanner.py` — discovery + ffprobe
- `src/valclips/web/routes.py` — routes and the streaming proxy

## Conventions

- DB access via `with get_connection() as conn:`
- Analysis results through `update_clip_ai()`
- Tags lowercase, `tags` table, `source` field (`manual`/`ai`/`folder`)

## Branches — read before any git operation

**`origin/dev` shares NO common ancestor with `main`.** `git merge-base main
origin/dev` returns empty; roots are `7958ea7` (main) and `6fcb757` (dev, shared
with `origin/master`). `git merge` will refuse.

`dev` is the Windows/`blackbird` build — commits `all clip drives (K:, L:, C:, P:)`
and `Auto-detect FFmpeg on Windows via winget`. Cherry-pick files out of it
(`git checkout origin/dev -- <path>`); never merge.

That branch is also the best evidence of where the archive lives: four drive
letters on the Windows recorder.

## Data location

Split across machines. The `chaos.local` SMB shares this repo targets
(`prometheus`/`kronos`/`atlas`) are on a TrueNAS box that has been offline since
2026-09-12 — see `.private/nas-recovery.md`. The Windows recorder is reachable
and holds clips on `K:`, `L:`, `C:`, `P:`.

No inventory has ever been taken. The "~2,800 clips" figure in the git history is
unverified and inconsistent with 10TB; adversarial checking put the archive at
150-400+ hours, central estimate ~395h. **Every plan should wait on a real
inventory.**

## Measured facts worth keeping

- ShadowPlay writes an IDR keyframe every **0.50 s** uniformly, so
  `ffmpeg -discard nokey` yields 2 fps essentially free and a 4-second kill
  banner spans ~8 consecutive keyframes. *Measured on one file only.*
- `-discard:v all` is a **no-op** — `fftools/ffmpeg_demux.c` already initialises
  every input stream to `AVDISCARD_ALL`. Byte-identical I/O with and without it.
- Audio-only extraction reads ~1.9% of file bytes, not the 0.25% the bitrate
  ratio suggests, because `probesize` forces a fixed ~5.15 MB linear read first.
  Add `-probesize 32768 -analyzeduration 0`.
- Dual audio tracks (game + mic) are **not** archive-wide: a 2024 file has one
  stream, 2026 files have two. Establish coverage before relying on it.
- `format.tags.creation_time` is genuine UTC, enabling time-based joins.

## Running

```bash
python -m venv .venv && source .venv/bin/activate && pip install -e .
valclips scan --dir /path/to/clips   # --dir works on any OS; bare `scan` needs Linux+GVFS
valclips thumbnails
valclips serve                        # 500s on every page until routes.py is fixed
```

Requires `ffmpeg`/`ffprobe` on PATH.

## Publishing

**This repository is public.** Agent reports quote whatever they read.
`scripts/persist_workflow.py` enforces a leak gate: anything matching a host
address, MAC, tailnet domain, personal email, or a private-repo name goes to
`.private/` (gitignored) instead of `docs/research/`. Run it rather than copying
agent output by hand.
