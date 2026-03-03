"""Click CLI entry point for valclips."""

import click
from rich.console import Console
from rich.table import Table

from .config import DEFAULT_PAGE_SIZE, WEB_HOST, WEB_PORT
from .db import (
    get_connection, init_db, list_clips, get_clip, add_tag, remove_tag,
    get_all_tags, get_stats, clips_without_thumbnails, clips_without_analysis,
    update_clip_ai, find_duplicates, mark_duplicate, get_duplicate_stats,
)

console = Console()


def _format_size(b: int | None) -> str:
    if b is None:
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


def _format_duration(s: float | None) -> str:
    if s is None:
        return "-"
    m, sec = divmod(int(s), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


@click.group()
def cli():
    """Valorant clip manager -- index, browse, tag, and analyze gameplay recordings."""
    init_db()


@cli.command()
@click.option("--force", is_flag=True, help="Re-probe all files, ignoring cache.")
@click.option("--share", "share_name", default=None, help="Only scan a specific share.")
@click.option("--dir", "extra_dirs", multiple=True, help="Additional directories to scan.")
def scan(force: bool, share_name: str | None, extra_dirs: tuple[str, ...]):
    """Discover and index clips from SMB shares."""
    from .scanner import scan as do_scan

    console.print("[bold]Starting scan...[/bold]")
    result = do_scan(
        force=force,
        share_filter=share_name,
        extra_dirs=list(extra_dirs) if extra_dirs else None,
    )
    console.print()
    console.print(f"[green]Scan complete![/green]")
    console.print(f"  Found: {result.clips_found}")
    console.print(f"  New:   {result.clips_new}")
    console.print(f"  Updated: {result.clips_updated}")
    console.print(f"  Errors:  {result.errors}")


@cli.command("list")
@click.option("--sort", type=click.Choice(["date", "duration", "size", "name"]), default="date")
@click.option("--tag", default=None, help="Filter by tag.")
@click.option("--from", "date_from", default=None, help="Filter from date (YYYY-MM-DD).")
@click.option("--to", "date_to", default=None, help="Filter to date (YYYY-MM-DD).")
@click.option("--share", default=None, help="Filter by share name.")
@click.option("--page", default=1, type=int, help="Page number.")
@click.option("--limit", default=DEFAULT_PAGE_SIZE, type=int, help="Results per page.")
def list_cmd(sort: str, tag: str | None, date_from: str | None, date_to: str | None,
             share: str | None, page: int, limit: int):
    """Browse indexed clips."""
    with get_connection() as conn:
        result = list_clips(
            conn, page=page, page_size=limit, sort=sort,
            tag=tag, date_from=date_from, date_to=date_to, share=share,
        )

    table = Table(title=f"Clips (page {result.page}/{result.pages}, {result.total} total)")
    table.add_column("ID", style="dim", width=6)
    table.add_column("Date", width=19)
    table.add_column("Duration", width=8)
    table.add_column("Size", width=10)
    table.add_column("Format", width=7)
    table.add_column("Share", width=10)
    table.add_column("Tags", width=20)
    table.add_column("Filename")

    for c in result.clips:
        date_str = c.recorded_at.strftime("%Y-%m-%d %H:%M:%S") if c.recorded_at else "-"
        tags_str = ", ".join(c.tags) if c.tags else ""
        table.add_row(
            str(c.id), date_str, _format_duration(c.duration_seconds),
            _format_size(c.file_size_bytes), c.source_format or "-",
            c.share_name or "-", tags_str, c.filename,
        )

    console.print(table)


@cli.command()
@click.argument("query")
@click.option("--page", default=1, type=int)
@click.option("--limit", default=DEFAULT_PAGE_SIZE, type=int)
def search(query: str, page: int, limit: int):
    """Search filenames, tags, and directories."""
    with get_connection() as conn:
        result = list_clips(conn, page=page, page_size=limit, search=query)

    table = Table(title=f"Search: '{query}' ({result.total} results)")
    table.add_column("ID", style="dim", width=6)
    table.add_column("Date", width=19)
    table.add_column("Duration", width=8)
    table.add_column("Filename")
    table.add_column("Tags", width=20)

    for c in result.clips:
        date_str = c.recorded_at.strftime("%Y-%m-%d %H:%M:%S") if c.recorded_at else "-"
        tags_str = ", ".join(c.tags) if c.tags else ""
        table.add_row(
            str(c.id), date_str, _format_duration(c.duration_seconds),
            c.filename, tags_str,
        )

    console.print(table)


@cli.command()
@click.argument("clip_id", type=int)
def info(clip_id: int):
    """Show full metadata for a clip."""
    with get_connection() as conn:
        clip = get_clip(conn, clip_id)
    if not clip:
        console.print(f"[red]Clip {clip_id} not found.[/red]")
        raise SystemExit(1)

    console.print(f"[bold]Clip #{clip.id}[/bold]: {clip.filename}")
    console.print()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="bold")
    table.add_column("Value")

    table.add_row("Path", clip.file_path)
    table.add_row("Share", clip.share_name or "-")
    table.add_row("Directory", clip.directory or "-")
    table.add_row("Recorded", clip.recorded_at.strftime("%Y-%m-%d %H:%M:%S") if clip.recorded_at else "-")
    table.add_row("Format", clip.source_format or "-")
    table.add_row("Sequence", str(clip.clip_sequence) if clip.clip_sequence is not None else "-")
    table.add_row("Duration", _format_duration(clip.duration_seconds))
    table.add_row("Resolution", f"{clip.width}x{clip.height}" if clip.width else "-")
    table.add_row("FPS", str(clip.fps) if clip.fps else "-")
    table.add_row("Codec", clip.codec or "-")
    table.add_row("Size", _format_size(clip.file_size_bytes))
    table.add_row("Bitrate", f"{clip.bitrate // 1000} kbps" if clip.bitrate else "-")
    table.add_row("Thumbnail", clip.thumbnail_path or "-")
    table.add_row("Tags", ", ".join(clip.tags) if clip.tags else "-")
    table.add_row("AI Agent", clip.ai_agent or "-")
    table.add_row("AI Map", clip.ai_map or "-")
    table.add_row("AI Summary", clip.ai_summary or "-")
    table.add_row("Analyzed", clip.ai_analyzed_at.strftime("%Y-%m-%d %H:%M:%S") if clip.ai_analyzed_at else "-")

    console.print(table)


@cli.command()
def random():
    """Show a random clip."""
    import random as rng
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM clips WHERE duplicate_of IS NULL ORDER BY RANDOM() LIMIT 1"
        ).fetchone()
    if not row:
        console.print("No clips indexed yet.")
        return
    # Reuse the info command's logic
    clip_id = row["id"]
    with get_connection() as conn:
        clip = get_clip(conn, clip_id)
    console.print(f"[bold]Random clip #{clip.id}[/bold]: {clip.filename}")
    console.print(f"  Recorded: {clip.recorded_at.strftime('%Y-%m-%d %H:%M:%S') if clip.recorded_at else '-'}")
    console.print(f"  Duration: {_format_duration(clip.duration_seconds)}")
    console.print(f"  Size:     {_format_size(clip.file_size_bytes)}")
    console.print(f"  Share:    {clip.share_name or '-'}")
    console.print(f"  Tags:     {', '.join(clip.tags) if clip.tags else '-'}")
    console.print(f"\n  [dim]valclips info {clip.id}  |  valclips open {clip.id}[/dim]")


@cli.command("open")
@click.argument("clip_id", type=int)
def open_clip(clip_id: int):
    """Open a clip in the system default video player."""
    import os
    import subprocess
    import sys

    with get_connection() as conn:
        clip = get_clip(conn, clip_id)
    if not clip:
        console.print(f"[red]Clip {clip_id} not found.[/red]")
        raise SystemExit(1)

    path = clip.file_path
    console.print(f"Opening: {clip.filename}")
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", path])


@cli.command()
@click.argument("clip_id", type=int)
@click.argument("tag_names", nargs=-1, required=True)
def tag(clip_id: int, tag_names: tuple[str, ...]):
    """Add tags to a clip."""
    with get_connection() as conn:
        clip = get_clip(conn, clip_id)
        if not clip:
            console.print(f"[red]Clip {clip_id} not found.[/red]")
            raise SystemExit(1)
        for t in tag_names:
            add_tag(conn, clip_id, t.lower().strip())
    console.print(f"Added {len(tag_names)} tag(s) to clip #{clip_id}.")


@cli.command()
@click.argument("clip_id", type=int)
@click.argument("tag_name")
def untag(clip_id: int, tag_name: str):
    """Remove a tag from a clip."""
    with get_connection() as conn:
        remove_tag(conn, clip_id, tag_name.lower().strip())
    console.print(f"Removed tag '{tag_name}' from clip #{clip_id}.")


@cli.command()
def tags():
    """List all tags with counts."""
    with get_connection() as conn:
        all_tags = get_all_tags(conn)

    if not all_tags:
        console.print("No tags yet.")
        return

    table = Table(title="Tags")
    table.add_column("Tag", style="bold")
    table.add_column("Count", justify="right")

    for t in all_tags:
        table.add_row(t["name"], str(t["count"]))

    console.print(table)


@cli.command()
def stats():
    """Show summary statistics."""
    with get_connection() as conn:
        s = get_stats(conn)
        ds = get_duplicate_stats(conn)

    console.print("[bold]Clip Library Stats[/bold]")
    console.print()
    console.print(f"  Total clips:    {s['total_clips']}")
    console.print(f"  Total size:     {_format_size(s['total_size_bytes'])}")
    console.print(f"  Total duration: {_format_duration(s['total_duration_seconds'])}")
    console.print(f"  Thumbnailed:    {s['thumbnailed']}")
    console.print(f"  AI analyzed:    {s['analyzed']}")
    console.print(f"  Tagged:         {s['tagged']}")
    console.print(f"  Duplicates:     {ds['duplicate_count']} ({_format_size(ds['wasted_bytes'])} wasted)")
    console.print()

    if s["by_share"]:
        console.print("  [bold]By share:[/bold]")
        for name, count in s["by_share"].items():
            console.print(f"    {name}: {count}")

    if s["by_format"]:
        console.print("  [bold]By format:[/bold]")
        for fmt, count in s["by_format"].items():
            console.print(f"    {fmt}: {count}")


@cli.command()
@click.option("--mark", is_flag=True, help="Auto-mark duplicates (keep earliest ID as original).")
def dupes(mark: bool):
    """Find and optionally mark duplicate clips."""
    with get_connection() as conn:
        groups = find_duplicates(conn)

    if not groups:
        console.print("No duplicates found.")
        return

    total_dupes = sum(len(g) - 1 for g in groups)
    wasted = sum(sum(c.file_size_bytes or 0 for c in g[1:]) for g in groups)
    console.print(f"[bold]Found {len(groups)} duplicate groups ({total_dupes} extra copies, {_format_size(wasted)} wasted)[/bold]\n")

    for i, group in enumerate(groups, 1):
        console.print(f"[bold]Group {i}:[/bold] {group[0].filename} ({_format_size(group[0].file_size_bytes)}, {_format_duration(group[0].duration_seconds)})")
        for j, clip in enumerate(group):
            prefix = "  [green]KEEP [/green]" if j == 0 else "  [red]DUPE [/red]"
            console.print(f"{prefix} #{clip.id} {clip.share_name or '?'}:{clip.directory}")

        if mark:
            original = group[0]
            with get_connection() as conn:
                for clip in group[1:]:
                    mark_duplicate(conn, clip.id, original.id)

        console.print()

    if mark:
        console.print(f"[green]Marked {total_dupes} clips as duplicates.[/green]")
    else:
        console.print("Run with [bold]--mark[/bold] to auto-mark duplicates.")


@cli.command()
@click.option("--format", "fmt", type=click.Choice(["json", "csv"]), default="json")
@click.option("--output", "-o", default=None, help="Output file (default: stdout).")
@click.option("--hide-dupes/--show-dupes", default=True, help="Exclude duplicates.")
def export(fmt: str, output: str | None, hide_dupes: bool):
    """Export clip data as JSON or CSV."""
    import csv
    import json
    import sys

    with get_connection() as conn:
        result = list_clips(conn, page=1, page_size=100000, hide_dupes=hide_dupes)

    clips_data = [c.model_dump() for c in result.clips]

    # Convert datetimes to strings for JSON
    for c in clips_data:
        for k, v in c.items():
            if hasattr(v, 'isoformat'):
                c[k] = v.isoformat()

    out = open(output, "w") if output else sys.stdout

    if fmt == "json":
        json.dump(clips_data, out, indent=2, default=str)
        out.write("\n")
    elif fmt == "csv":
        if clips_data:
            writer = csv.DictWriter(out, fieldnames=clips_data[0].keys())
            writer.writeheader()
            writer.writerows(clips_data)

    if output:
        out.close()
        console.print(f"Exported {len(clips_data)} clips to {output}")
    else:
        console.print(f"\n[dim]({len(clips_data)} clips)[/dim]", err=True)


@cli.command()
@click.option("--limit", default=None, type=int, help="Max thumbnails to generate.")
def thumbnails(limit: int | None):
    """Generate missing thumbnails."""
    from .thumbnails import generate_thumbnails

    with get_connection() as conn:
        missing = clips_without_thumbnails(conn, limit=limit)

    if not missing:
        console.print("All clips have thumbnails.")
        return

    console.print(f"Generating thumbnails for {len(missing)} clips...")
    generate_thumbnails(missing)
    console.print("[green]Done![/green]")


@cli.command()
@click.option("--limit", default=50, type=int, help="Max clips to generate previews for.")
@click.option("--min-score", default=5, type=int, help="Minimum score to generate preview.")
def previews(limit: int, min_score: int):
    """Generate animated WebP previews for top clips."""
    from .thumbnails import generate_previews

    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM clips
            WHERE ai_score >= ? AND duplicate_of IS NULL
            AND thumbnail_path IS NOT NULL
            ORDER BY ai_score DESC LIMIT ?""",
            (min_score, limit),
        ).fetchall()

    if not rows:
        console.print("No eligible clips found.")
        return

    from .db import _row_to_clip
    clips = [_row_to_clip(r) for r in rows]
    console.print(f"Generating animated previews for {len(clips)} clips (score >= {min_score})...")
    ok = generate_previews(clips)
    console.print(f"[green]Done! {ok}/{len(clips)} previews generated.[/green]")


@cli.command()
@click.option("--limit", default=None, type=int, help="Max clips to analyze.")
@click.option("--model", default="claude-haiku-4-5-20251001", help="Claude model to use.")
@click.option("--stub", is_flag=True, help="Use stub analyzer (no API key needed).")
@click.option("--ffmpeg", "use_ffmpeg", is_flag=True, help="Use FFmpeg heuristic analyzer (no API needed).")
@click.option("--quick", is_flag=True, help="Quick mode: metadata-only scoring (fastest, no ffmpeg).")
@click.option("--gemini", is_flag=True, help="Use Gemini Vision (needs GEMINI_API_KEY, free tier).")
@click.option("--local", "use_local", is_flag=True, help="Use local CV analyzer (no API, uses OpenCV).")
@click.option("--workers", "-w", default=1, type=int, help="Parallel workers.")
@click.option("--re-analyze", is_flag=True, help="Re-analyze already analyzed clips.")
@click.option("--min-score", default=None, type=int, help="Only re-analyze clips with heuristic score >= N.")
@click.option("--all", "analyze_all", is_flag=True, help="Analyze ALL clips (not just unanalyzed).")
def analyze(limit, model, stub, use_ffmpeg, quick, gemini, use_local, workers, re_analyze, min_score, analyze_all):
    """Run AI analysis on clips.

    Backends:
      --local:   Local CV analyzer (no API, uses OpenCV, fast)
      --gemini:  Gemini Vision (needs GEMINI_API_KEY, most accurate)
      --ffmpeg:  FFmpeg scene-change heuristics (free, no API)
      --quick:   Metadata-only scoring (instant, least accurate)
      (default): Claude Vision (needs ANTHROPIC_API_KEY)

    Examples:
      valclips analyze --local --all           # Analyze ALL clips locally
      valclips analyze --local -w 4 --all      # Parallel local analysis
      valclips analyze --gemini --re-analyze   # Upgrade local results with Gemini
      valclips analyze --gemini --min-score 5  # Only high-scoring clips via Gemini
    """
    import os
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from .ai.highlights import get_clip_highlights
    from .db import get_top_clips
    from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn, SpinnerColumn

    if stub:
        from .ai.stub import StubAnalyzer
        analyzer = StubAnalyzer()
    elif use_local:
        from .ai.local_analyzer import LocalAnalyzer
        analyzer = LocalAnalyzer()
    elif gemini:
        from .ai.gemini_analyzer import GeminiAnalyzer
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            console.print("[red]Set GEMINI_API_KEY environment variable.[/red]")
            console.print("Get a free key at: https://aistudio.google.com/apikey")
            raise SystemExit(1)
        analyzer = GeminiAnalyzer(api_key=api_key)
    elif use_ffmpeg or quick:
        from .ai.ffmpeg_analyzer import FFmpegHeuristicAnalyzer
        analyzer = FFmpegHeuristicAnalyzer(quick=quick)
    else:
        from .ai.claude_analyzer import ClaudeVisionAnalyzer
        try:
            analyzer = ClaudeVisionAnalyzer(model=model)
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise SystemExit(1)

    max_rows = limit or 100000
    with get_connection() as conn:
        from .db import _row_to_clip
        if analyze_all or re_analyze:
            conditions = ["duplicate_of IS NULL"]
            params: list = []

            if min_score:
                conditions.append("ai_score >= ?")
                params.append(min_score)

            # For Gemini --re-analyze: skip already-Gemini-analyzed unless --all
            if gemini and not analyze_all:
                conditions.append("(ai_agent IS NULL OR ai_agent != 'gemini')")

            where = " AND ".join(conditions)
            query = f"SELECT * FROM clips WHERE {where} ORDER BY ai_score DESC LIMIT ?"
            params.append(max_rows)
            rows = conn.execute(query, params).fetchall()
            pending = [_row_to_clip(r) for r in rows]

            for clip in pending:
                tags = conn.execute(
                    "SELECT name FROM tags WHERE clip_id = ?", (clip.id,)
                ).fetchall()
                clip.tags = [t["name"] for t in tags]
        else:
            pending = clips_without_analysis(conn, limit=limit)

    if not pending:
        console.print("All clips have been analyzed.")
        return

    is_heuristic = use_ffmpeg or quick
    is_vision = gemini or (not is_heuristic and not stub)
    actual_workers = min(workers, 3) if gemini else workers

    console.print(
        f"Analyzing {len(pending)} clips with [bold]{analyzer.__class__.__name__}[/bold] "
        f"({actual_workers} worker{'s' if actual_workers > 1 else ''})..."
    )
    errors = 0
    completed = 0

    def analyze_one(clip):
        """Analyze a single clip. Returns (clip_id, AnalysisResult, frames_to_clean).

        Raises QuotaExhaustedError if all Gemini models are quota-exhausted.
        """
        from .ai.gemini_analyzer import QuotaExhaustedError
        frames = []
        try:
            if is_heuristic:
                from .ai.ffmpeg_analyzer import FFmpegHeuristicAnalyzer
                assert isinstance(analyzer, FFmpegHeuristicAnalyzer)
                ar = analyzer.analyze_with_metadata(
                    clip.file_path, keyframes=[],
                    duration=clip.duration_seconds,
                    directory=clip.directory, tags=clip.tags,
                )
            elif use_local:
                # Fast evenly-spaced frame extraction (no scene detection)
                from .ai.keyframes import extract_keyframes
                max_f = getattr(analyzer, 'max_frames', 8)
                frames = extract_keyframes(clip.file_path, n=max_f)
                ar = analyzer.analyze(clip.file_path, frames)
            else:
                max_f = getattr(analyzer, 'max_frames', 8)
                segments, frames = get_clip_highlights(
                    clip.file_path, duration=clip.duration_seconds, max_frames=max_f,
                )
                ar = analyzer.analyze(clip.file_path, frames)
            return clip.id, ar, frames
        except QuotaExhaustedError:
            raise  # Propagate to stop the batch
        except Exception as e:
            return clip.id, e, frames

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Analyzing", total=len(pending))

        if actual_workers <= 1:
            # Single-threaded mode
            for i, clip in enumerate(pending, 1):
                progress.update(task, description=f"[{i}/{len(pending)}] #{clip.id}")
                try:
                    clip_id, result_or_err, frames = analyze_one(clip)
                except Exception as quota_err:
                    from .ai.gemini_analyzer import QuotaExhaustedError
                    if isinstance(quota_err, QuotaExhaustedError):
                        progress.console.print(
                            f"\n[bold red]All Gemini models quota-exhausted.[/bold red]"
                        )
                        progress.console.print(
                            f"Analyzed {completed} clips this run. "
                            f"Re-run later to continue."
                        )
                        break
                    raise

                if isinstance(result_or_err, Exception):
                    errors += 1
                    progress.console.print(f"  [red]#{clip_id} Error: {result_or_err}[/red]")
                else:
                    ar = result_or_err
                    with get_connection() as conn:
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
                    completed += 1
                    if ar.score and ar.score >= 5:
                        info = f"Score: {ar.score}/10"
                        if ar.kills: info += f" | {ar.kills}K"
                        if ar.is_ace: info += " ACE"
                        if ar.clutch_type: info += f" {ar.clutch_type}"
                        info += f" | {ar.highlight_type or 'regular'}"
                        if ar.player_agent: info += f" | {ar.player_agent}"
                        if ar.map_name: info += f" | {ar.map_name}"
                        progress.console.print(f"  [bold yellow]#{clip_id}[/bold yellow] {info}")

                for f in frames:
                    try: f.unlink(missing_ok=True)
                    except Exception: pass
                progress.advance(task)
        else:
            # Multi-worker parallel mode
            with ThreadPoolExecutor(max_workers=actual_workers) as executor:
                futures = {executor.submit(analyze_one, clip): clip for clip in pending}

                for future in as_completed(futures):
                    clip = futures[future]
                    clip_id, result_or_err, frames = future.result()

                    if isinstance(result_or_err, Exception):
                        errors += 1
                        progress.console.print(f"  [red]#{clip_id} Error: {result_or_err}[/red]")
                    else:
                        ar = result_or_err
                        with get_connection() as conn:
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
                        completed += 1
                        if ar.score and ar.score >= 5:
                            info = f"Score: {ar.score}/10"
                            if ar.kills: info += f" | {ar.kills}K"
                            if ar.is_ace: info += " ACE"
                            if ar.clutch_type: info += f" {ar.clutch_type}"
                            info += f" | {ar.highlight_type or 'regular'}"
                            progress.console.print(f"  [bold yellow]#{clip_id}[/bold yellow] {info}")

                    for f in frames:
                        try: f.unlink(missing_ok=True)
                        except Exception: pass
                    progress.advance(task)

    console.print(f"\n[green]Analysis complete![/green] {completed} analyzed, {errors} errors")

    # Show top clips
    with get_connection() as conn:
        top = get_top_clips(conn, limit=10)
    if top:
        table = Table(title="Top 10 Clips")
        table.add_column("Rank", width=4)
        table.add_column("ID", style="dim", width=6)
        table.add_column("Score", width=5)
        table.add_column("Kills", width=5)
        table.add_column("Type", width=15)
        table.add_column("Agent", width=10)
        table.add_column("Map", width=10)
        table.add_column("Summary")

        for rank, c in enumerate(top, 1):
            kills_str = str(c.ai_kills) if c.ai_kills else "-"
            if c.ai_is_ace:
                kills_str += " ACE"
            if c.ai_clutch_type:
                kills_str += f" {c.ai_clutch_type}"
            table.add_row(
                str(rank), str(c.id),
                f"[bold]{c.ai_score}[/bold]" if c.ai_score else "-",
                kills_str,
                c.ai_highlight_type or "-",
                c.ai_player_agent or "-",
                c.ai_map or "-",
                (c.ai_summary or "-")[:50],
            )
        console.print(table)


@cli.command("top")
@click.option("--limit", "-n", default=20, type=int, help="Number of clips to show.")
def top_clips(limit: int):
    """Show the highest-scoring analyzed clips."""
    from .db import get_top_clips

    with get_connection() as conn:
        top = get_top_clips(conn, limit=limit)

    if not top:
        console.print("No analyzed clips yet. Run [bold]valclips analyze[/bold] first.")
        return

    table = Table(title=f"Top {len(top)} Clips")
    table.add_column("Rank", width=4)
    table.add_column("ID", style="dim", width=6)
    table.add_column("Score", width=5)
    table.add_column("Kills", width=5)
    table.add_column("Type", width=12)
    table.add_column("Map", width=10)
    table.add_column("Date", width=10)
    table.add_column("Summary")

    for i, c in enumerate(top, 1):
        date_str = c.recorded_at.strftime("%Y-%m-%d") if c.recorded_at else "-"
        table.add_row(
            str(i), str(c.id),
            f"[bold]{c.ai_score}[/bold]" if c.ai_score else "-",
            str(c.ai_kills) if c.ai_kills is not None else "-",
            c.ai_highlight_type or "-",
            c.ai_map or "-",
            date_str,
            (c.ai_summary or "-")[:60],
        )

    console.print(table)


@cli.command("extract")
@click.argument("clip_id", type=int)
@click.option("--output", "-o", default=None, help="Output directory (default: ./highlights/).")
@click.option("--reencode", is_flag=True, help="Re-encode for precise cuts (slower, better quality).")
def extract_cmd(clip_id: int, output: str | None, reencode: bool):
    """Extract the highlight segment from a DVR recording."""
    from .extractor import extract_highlight

    with get_connection() as conn:
        clip = get_clip(conn, clip_id)
    if not clip:
        console.print(f"[red]Clip {clip_id} not found.[/red]")
        raise SystemExit(1)

    out_dir = output or "highlights"
    console.print(f"Detecting highlight in #{clip.id} ({_format_duration(clip.duration_seconds)})...")

    out_path, seg = extract_highlight(
        clip.file_path,
        output_dir=out_dir,
        reencode=reencode,
        duration=clip.duration_seconds,
    )

    if out_path and seg:
        console.print(f"[green]Extracted![/green] {seg.start:.1f}s - {seg.end:.1f}s (activity: {seg.score:.0%})")
        console.print(f"  Output: {out_path}")
    else:
        console.print("[yellow]Could not detect a highlight segment.[/yellow]")


@cli.command("extract-top")
@click.option("--limit", "-n", default=10, type=int, help="Number of top clips to extract.")
@click.option("--output", "-o", default=None, help="Output directory (default: ./highlights/).")
@click.option("--min-score", default=6, type=int, help="Minimum AI score to extract.")
@click.option("--reencode", is_flag=True, help="Re-encode for precise cuts.")
def extract_top_cmd(limit: int, output: str | None, min_score: int, reencode: bool):
    """Extract highlights from the top-scored DVR clips."""
    from .extractor import extract_highlight
    from .db import get_top_clips

    with get_connection() as conn:
        top = get_top_clips(conn, limit=limit * 2)  # Get extras to filter

    # Only extract from DVR recordings (long clips), not already-edited ones
    dvr_clips = [c for c in top if (c.duration_seconds or 0) > 60 and (c.ai_score or 0) >= min_score][:limit]

    if not dvr_clips:
        console.print("No DVR clips matching criteria.")
        return

    out_dir = output or "highlights"
    console.print(f"Extracting highlights from {len(dvr_clips)} DVR clips...")

    for i, clip in enumerate(dvr_clips, 1):
        console.print(f"\n[{i}/{len(dvr_clips)}] #{clip.id} {clip.filename} ({clip.ai_score}/10)")
        out_path, seg = extract_highlight(
            clip.file_path,
            output_dir=out_dir,
            reencode=reencode,
            duration=clip.duration_seconds,
        )
        if out_path and seg:
            console.print(f"  [green]OK[/green] {seg.start:.1f}s-{seg.end:.1f}s -> {out_path}")
        else:
            console.print("  [yellow]No highlight detected[/yellow]")

    console.print(f"\n[green]Done![/green] Highlights saved to {out_dir}/")


@cli.command("sessions")
@click.option("--limit", "-n", default=20, type=int, help="Number of sessions to show.")
@click.option("--gap", default=30, type=int, help="Minutes between clips to split sessions.")
def sessions_cmd(limit: int, gap: int):
    """Show gaming sessions grouped by time."""
    from .db import get_sessions

    with get_connection() as conn:
        sessions = get_sessions(conn, gap_minutes=gap)

    if not sessions:
        console.print("No sessions found (clips need recorded_at timestamps).")
        return

    table = Table(title=f"Gaming Sessions ({len(sessions)} total, showing {min(limit, len(sessions))})")
    table.add_column("#", width=4)
    table.add_column("Date", width=10)
    table.add_column("Time", width=5)
    table.add_column("Clips", width=5, justify="right")
    table.add_column("Duration", width=10)
    table.add_column("Best", width=5)
    table.add_column("Share", width=10)
    table.add_column("Best Clip")

    for i, s in enumerate(sessions[:limit], 1):
        table.add_row(
            str(i),
            s["date"],
            s["time"],
            str(s["clip_count"]),
            _format_duration(s["total_duration"]),
            f"[bold]{s['best_score']}[/bold]" if s["best_score"] else "-",
            s["share"] or "-",
            (s["best_clip_name"] or "-")[:40],
        )

    console.print(table)


@cli.command()
@click.option("--host", default=WEB_HOST, help="Host to bind to.")
@click.option("--port", default=WEB_PORT, type=int, help="Port to serve on.")
def serve(host: str, port: int):
    """Start the web UI."""
    import uvicorn
    from .web.app import create_app

    app = create_app()
    console.print(f"Starting web UI at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    cli()
