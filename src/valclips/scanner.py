"""Discover clip files across SMB shares and probe metadata with ffprobe."""

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

from .config import SHARES, CLIP_DIRS, FOLDER_TAGS, FFPROBE_TIMEOUT, BATCH_SIZE
from .db import (
    get_connection, init_db, upsert_clips_batch, get_existing_paths,
    log_scan_start, log_scan_finish, add_tag,
)
from .models import Clip, ScanResult
from .parser import parse_filename, is_valorant_clip

console = Console()


def _find_clips_in_dir(
    dir_path: Path,
    mode: str = "valorant_only",
) -> list[Path]:
    """Walk a directory and return matching .mp4 files.

    mode="valorant_only": only files matching Valorant filename patterns.
    mode="all_mp4": every .mp4 file in the directory tree.
    """
    clips = []
    if not dir_path.exists():
        return clips
    for root, _dirs, files in os.walk(dir_path):
        for f in files:
            if mode == "all_mp4" and f.lower().endswith(".mp4"):
                clips.append(Path(root) / f)
            elif mode == "valorant_only" and is_valorant_clip(f):
                clips.append(Path(root) / f)
    return clips


def _ffprobe(file_path: Path) -> dict | None:
    """Run ffprobe on a file, return parsed JSON or None on failure."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(file_path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=FFPROBE_TIMEOUT,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return None


def _extract_metadata(probe: dict) -> dict:
    """Pull relevant fields from ffprobe JSON output."""
    fmt = probe.get("format", {})
    video_stream = None
    for s in probe.get("streams", []):
        if s.get("codec_type") == "video":
            video_stream = s
            break

    meta = {
        "duration_seconds": None,
        "width": None,
        "height": None,
        "fps": None,
        "codec": None,
        "bitrate": None,
    }

    if fmt.get("duration"):
        try:
            meta["duration_seconds"] = float(fmt["duration"])
        except (ValueError, TypeError):
            pass
    if fmt.get("bit_rate"):
        try:
            meta["bitrate"] = int(fmt["bit_rate"])
        except (ValueError, TypeError):
            pass

    if video_stream:
        meta["width"] = video_stream.get("width")
        meta["height"] = video_stream.get("height")
        meta["codec"] = video_stream.get("codec_name")
        # Parse fps from r_frame_rate (e.g. "60/1")
        rfr = video_stream.get("r_frame_rate", "")
        if "/" in rfr:
            parts = rfr.split("/")
            try:
                num, den = float(parts[0]), float(parts[1])
                if den > 0:
                    meta["fps"] = round(num / den, 2)
            except (ValueError, IndexError):
                pass

    return meta


def _get_folder_tags(file_path: Path) -> list[str]:
    """Extract auto-tags from parent directory names."""
    tags = []
    parts = file_path.parts
    for part in parts:
        part_lower = part.lower()
        for folder_name, tag_list in FOLDER_TAGS.items():
            if part_lower == folder_name.lower():
                tags.extend(tag_list)
    return tags


def _collect_share_files(
    share_filter: str | None = None,
) -> list[tuple[Path, str]]:
    """Collect files from configured share clip directories (targeted scan)."""
    all_files: list[tuple[Path, str]] = []

    shares_to_scan = SHARES
    if share_filter:
        shares_to_scan = {k: v for k, v in SHARES.items() if k == share_filter}

    for share_name, share_root in shares_to_scan.items():
        if not share_root.exists():
            console.print(f"  [yellow]Share {share_name} not accessible, skipping[/yellow]")
            continue

        dirs_config = CLIP_DIRS.get(share_name, [])
        if not dirs_config:
            # No targeted dirs configured, skip (don't walk entire share)
            console.print(f"  [yellow]No clip directories configured for {share_name}[/yellow]")
            continue

        for entry in dirs_config:
            dir_path = share_root / entry["path"]
            mode = entry.get("mode", "valorant_only")
            if not dir_path.exists():
                continue
            found = _find_clips_in_dir(dir_path, mode=mode)
            console.print(f"  {share_name}/{entry['path']}: {len(found)} files")
            for f in found:
                all_files.append((f, share_name))

    return all_files


def scan(
    force: bool = False,
    share_filter: str | None = None,
    extra_dirs: list[str] | None = None,
) -> ScanResult:
    """Scan shares for Valorant clips. Returns scan statistics.

    Uses targeted directory scanning for configured shares (fast),
    and full recursive walk for --dir directories.
    """
    init_db()
    result = ScanResult(started_at=datetime.now())

    with get_connection() as conn:
        scan_id = log_scan_start(conn)
        existing = get_existing_paths(conn) if not force else {}

        all_files: list[tuple[Path, str | None]] = []

        # Scan configured share clip directories (targeted, fast)
        if not extra_dirs or share_filter:
            console.print("[bold]Discovering clips in shares...[/bold]")
            share_files = _collect_share_files(share_filter)
            all_files.extend(share_files)

        # Scan extra directories (--dir flag, full walk)
        if extra_dirs:
            console.print("[bold]Discovering clips in extra directories...[/bold]")
            for d in extra_dirs:
                dp = Path(d)
                if dp.exists():
                    # Use all_mp4 mode for --dir since user explicitly pointed here
                    found = _find_clips_in_dir(dp, mode="all_mp4")
                    console.print(f"  {d}: {len(found)} files")
                    for f in found:
                        all_files.append((f, None))

        result.clips_found = len(all_files)
        console.print(f"\n[bold]Total files found: {result.clips_found}[/bold]")

        # Count how many are already indexed
        skip_count = 0
        for fp, _ in all_files:
            fp_str = str(fp)
            try:
                sz = fp.stat().st_size
            except OSError:
                continue
            if fp_str in existing and existing[fp_str] == sz:
                skip_count += 1

        if skip_count > 0 and not force:
            console.print(f"  Already indexed: {skip_count} (will skip)")
            console.print(f"  To process: {result.clips_found - skip_count}")

        # Process in batches with progress
        batch: list[Clip] = []
        auto_tags: list[tuple[str, list[str]]] = []  # (file_path, tags) for post-insert

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task("Indexing clips...", total=len(all_files))

            for file_path, share_name in all_files:
                progress.update(task, advance=1)
                fp_str = str(file_path)

                # Incremental: skip if already indexed with same size
                try:
                    file_size = file_path.stat().st_size
                except OSError:
                    result.errors += 1
                    continue

                if fp_str in existing and existing[fp_str] == file_size:
                    continue

                # Skip empty files
                if file_size == 0:
                    result.errors += 1
                    continue

                # Parse filename
                parsed = parse_filename(file_path.name)

                # Probe metadata
                probe_data = _ffprobe(file_path)
                meta = _extract_metadata(probe_data) if probe_data else {}

                clip = Clip(
                    file_path=fp_str,
                    filename=file_path.name,
                    recorded_at=parsed.get("recorded_at"),
                    clip_sequence=parsed.get("clip_sequence"),
                    source_format=parsed.get("source_format"),
                    duration_seconds=meta.get("duration_seconds"),
                    width=meta.get("width"),
                    height=meta.get("height"),
                    fps=meta.get("fps"),
                    codec=meta.get("codec"),
                    file_size_bytes=file_size,
                    bitrate=meta.get("bitrate"),
                    share_name=share_name,
                    directory=str(file_path.parent),
                )

                batch.append(clip)

                # Collect folder-based auto-tags
                ftags = _get_folder_tags(file_path)
                if ftags:
                    auto_tags.append((fp_str, ftags))

                if len(batch) >= BATCH_SIZE:
                    new, updated = upsert_clips_batch(conn, batch)
                    result.clips_new += new
                    result.clips_updated += updated
                    batch.clear()

            # Flush remaining
            if batch:
                new, updated = upsert_clips_batch(conn, batch)
                result.clips_new += new
                result.clips_updated += updated

        # Apply auto-tags after all clips are inserted
        if auto_tags:
            console.print(f"Applying folder-based tags to {len(auto_tags)} clips...")
            from .db import get_clip_by_path
            for fp_str, tags in auto_tags:
                clip = get_clip_by_path(conn, fp_str)
                if clip:
                    for t in tags:
                        add_tag(conn, clip.id, t, source="folder")

        result.finished_at = datetime.now()
        result.status = "complete"
        log_scan_finish(conn, scan_id, result)

    return result
