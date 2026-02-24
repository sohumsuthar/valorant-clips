"""SQLite schema, connection management, and queries."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .config import DB_PATH
from .models import Clip, ClipPage, ScanResult, Tag

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT UNIQUE NOT NULL,
    filename TEXT NOT NULL,
    recorded_at TEXT,
    clip_sequence INTEGER,
    source_format TEXT,
    duration_seconds REAL,
    width INTEGER,
    height INTEGER,
    fps REAL,
    codec TEXT,
    file_size_bytes INTEGER,
    bitrate INTEGER,
    share_name TEXT,
    directory TEXT,
    thumbnail_path TEXT,
    ai_agent TEXT,
    ai_map TEXT,
    ai_summary TEXT,
    ai_score INTEGER,
    ai_kills INTEGER,
    ai_highlight_type TEXT,
    ai_analyzed_at TEXT,
    duplicate_of INTEGER REFERENCES clips(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    UNIQUE(clip_id, name)
);

CREATE TABLE IF NOT EXISTS scan_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    clips_found INTEGER DEFAULT 0,
    clips_new INTEGER DEFAULT 0,
    clips_updated INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running'
);

CREATE INDEX IF NOT EXISTS idx_clips_recorded_at ON clips(recorded_at);
CREATE INDEX IF NOT EXISTS idx_clips_share_name ON clips(share_name);
CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name);
CREATE INDEX IF NOT EXISTS idx_tags_clip_id ON tags(clip_id);
"""


def get_db_path() -> Path:
    return DB_PATH


@contextmanager
def get_connection(db_path: Path | None = None):
    """Yield a sqlite3 connection with WAL mode and foreign keys enabled."""
    path = db_path or get_db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


MIGRATIONS = [
    "ALTER TABLE clips ADD COLUMN duplicate_of INTEGER REFERENCES clips(id);",
    "ALTER TABLE clips ADD COLUMN ai_score INTEGER;",
    "ALTER TABLE clips ADD COLUMN ai_kills INTEGER;",
    "ALTER TABLE clips ADD COLUMN ai_highlight_type TEXT;",
    # v2: richer gameplay data
    "ALTER TABLE clips ADD COLUMN ai_clutch_type TEXT;",     # 1v1, 1v2, 1v3, 1v4, 1v5
    "ALTER TABLE clips ADD COLUMN ai_weapon TEXT;",          # vandal, phantom, operator, etc.
    "ALTER TABLE clips ADD COLUMN ai_player_agent TEXT;",    # jett, reyna, etc. (player's agent)
    "ALTER TABLE clips ADD COLUMN ai_deaths INTEGER;",       # deaths in clip
    "ALTER TABLE clips ADD COLUMN ai_is_ace INTEGER;",       # 1 if ace, 0 otherwise
    "ALTER TABLE clips ADD COLUMN ai_round_outcome TEXT;",   # win, loss, draw
    "ALTER TABLE clips ADD COLUMN ai_confidence REAL;",      # 0-1 confidence
    "CREATE INDEX IF NOT EXISTS idx_clips_ai_score ON clips(ai_score);",
    "CREATE INDEX IF NOT EXISTS idx_clips_ai_kills ON clips(ai_kills);",
    "CREATE INDEX IF NOT EXISTS idx_clips_highlight_type ON clips(ai_highlight_type);",
]


def _run_migrations(conn: sqlite3.Connection):
    """Apply schema migrations for existing databases."""
    for sql in MIGRATIONS:
        try:
            conn.execute(sql)
            conn.commit()
        except sqlite3.OperationalError:
            # Column/table already exists, skip
            pass


def init_db(db_path: Path | None = None):
    """Create tables and indexes if they don't exist."""
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)
        _run_migrations(conn)


def _row_to_clip(row: sqlite3.Row) -> Clip:
    d = dict(row)
    for field in ("recorded_at", "ai_analyzed_at", "created_at", "updated_at"):
        if d.get(field):
            d[field] = datetime.fromisoformat(d[field])
    d["tags"] = []
    return Clip(**d)


def _dt(val: datetime | None) -> str | None:
    return val.isoformat() if val else None


def upsert_clip(conn: sqlite3.Connection, clip: Clip) -> int:
    """Insert or update a clip by file_path. Returns the clip id."""
    conn.execute(
        """INSERT INTO clips (
            file_path, filename, recorded_at, clip_sequence, source_format,
            duration_seconds, width, height, fps, codec, file_size_bytes,
            bitrate, share_name, directory, thumbnail_path,
            ai_agent, ai_map, ai_summary, ai_analyzed_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(file_path) DO UPDATE SET
            filename=excluded.filename,
            recorded_at=excluded.recorded_at,
            clip_sequence=excluded.clip_sequence,
            source_format=excluded.source_format,
            duration_seconds=excluded.duration_seconds,
            width=excluded.width,
            height=excluded.height,
            fps=excluded.fps,
            codec=excluded.codec,
            file_size_bytes=excluded.file_size_bytes,
            bitrate=excluded.bitrate,
            share_name=excluded.share_name,
            directory=excluded.directory,
            thumbnail_path=excluded.thumbnail_path,
            ai_agent=excluded.ai_agent,
            ai_map=excluded.ai_map,
            ai_summary=excluded.ai_summary,
            ai_analyzed_at=excluded.ai_analyzed_at,
            updated_at=datetime('now')
        """,
        (
            clip.file_path, clip.filename, _dt(clip.recorded_at),
            clip.clip_sequence, clip.source_format,
            clip.duration_seconds, clip.width, clip.height, clip.fps,
            clip.codec, clip.file_size_bytes, clip.bitrate,
            clip.share_name, clip.directory, clip.thumbnail_path,
            clip.ai_agent, clip.ai_map, clip.ai_summary,
            _dt(clip.ai_analyzed_at),
        ),
    )
    row = conn.execute(
        "SELECT id FROM clips WHERE file_path = ?", (clip.file_path,)
    ).fetchone()
    return row["id"]


def upsert_clips_batch(conn: sqlite3.Connection, clips: list[Clip]) -> tuple[int, int]:
    """Upsert a batch within a transaction. Returns (new_count, updated_count)."""
    new = 0
    updated = 0
    for clip in clips:
        existing = conn.execute(
            "SELECT id FROM clips WHERE file_path = ?", (clip.file_path,)
        ).fetchone()
        upsert_clip(conn, clip)
        if existing:
            updated += 1
        else:
            new += 1
    conn.commit()
    return new, updated


def get_clip(conn: sqlite3.Connection, clip_id: int) -> Clip | None:
    row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    if not row:
        return None
    clip = _row_to_clip(row)
    tags = conn.execute(
        "SELECT name FROM tags WHERE clip_id = ?", (clip_id,)
    ).fetchall()
    clip.tags = [t["name"] for t in tags]
    return clip


def get_adjacent_clip_ids(conn: sqlite3.Connection, clip_id: int) -> tuple[int | None, int | None]:
    """Get the previous and next clip IDs by recorded_at order."""
    # Get current clip's recorded_at
    current = conn.execute(
        "SELECT recorded_at FROM clips WHERE id = ?", (clip_id,)
    ).fetchone()
    if not current:
        return None, None

    prev_row = conn.execute(
        """SELECT id FROM clips
        WHERE (recorded_at < ? OR (recorded_at = ? AND id < ?))
        AND duplicate_of IS NULL
        ORDER BY recorded_at DESC, id DESC LIMIT 1""",
        (current["recorded_at"], current["recorded_at"], clip_id),
    ).fetchone()

    next_row = conn.execute(
        """SELECT id FROM clips
        WHERE (recorded_at > ? OR (recorded_at = ? AND id > ?))
        AND duplicate_of IS NULL
        ORDER BY recorded_at ASC, id ASC LIMIT 1""",
        (current["recorded_at"], current["recorded_at"], clip_id),
    ).fetchone()

    return (
        prev_row["id"] if prev_row else None,
        next_row["id"] if next_row else None,
    )


def get_clip_by_path(conn: sqlite3.Connection, file_path: str) -> Clip | None:
    row = conn.execute(
        "SELECT * FROM clips WHERE file_path = ?", (file_path,)
    ).fetchone()
    if not row:
        return None
    clip = _row_to_clip(row)
    tags = conn.execute(
        "SELECT name FROM tags WHERE clip_id = ?", (clip.id,)
    ).fetchall()
    clip.tags = [t["name"] for t in tags]
    return clip


def list_clips(
    conn: sqlite3.Connection,
    *,
    page: int = 1,
    page_size: int = 50,
    sort: str = "date",
    tag: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    share: str | None = None,
    search: str | None = None,
    hide_dupes: bool = False,
    score_min: int | None = None,
    highlight_type: str | None = None,
    map_name: str | None = None,
    clutch_type: str | None = None,
    weapon: str | None = None,
    player_agent: str | None = None,
    aces_only: bool = False,
    kills_min: int | None = None,
) -> ClipPage:
    """Paginated clip listing with filters."""
    sort_map = {
        "date": "c.recorded_at DESC",
        "score": "CASE WHEN c.ai_score IS NULL THEN 1 ELSE 0 END, c.ai_score DESC",
        "kills": "CASE WHEN c.ai_kills IS NULL THEN 1 ELSE 0 END, c.ai_kills DESC",
        "duration": "c.duration_seconds DESC",
        "size": "c.file_size_bytes DESC",
        "name": "c.filename ASC",
    }
    order = sort_map.get(sort, "c.recorded_at DESC")

    where_parts = []
    params: list = []

    if hide_dupes:
        where_parts.append("c.duplicate_of IS NULL")
    if tag:
        where_parts.append("c.id IN (SELECT clip_id FROM tags WHERE name = ?)")
        params.append(tag)
    if date_from:
        where_parts.append("c.recorded_at >= ?")
        params.append(date_from)
    if date_to:
        where_parts.append("c.recorded_at <= ?")
        params.append(date_to)
    if share:
        where_parts.append("c.share_name = ?")
        params.append(share)
    if score_min is not None:
        where_parts.append("c.ai_score >= ?")
        params.append(score_min)
    if highlight_type:
        where_parts.append("c.ai_highlight_type = ?")
        params.append(highlight_type)
    if map_name:
        where_parts.append("c.ai_map = ?")
        params.append(map_name)
    if clutch_type:
        where_parts.append("c.ai_clutch_type = ?")
        params.append(clutch_type)
    if weapon:
        where_parts.append("c.ai_weapon = ?")
        params.append(weapon)
    if player_agent:
        where_parts.append("c.ai_player_agent = ?")
        params.append(player_agent)
    if aces_only:
        where_parts.append("c.ai_is_ace = 1")
    if kills_min is not None:
        where_parts.append("c.ai_kills >= ?")
        params.append(kills_min)
    if search:
        where_parts.append(
            "(c.filename LIKE ? OR c.directory LIKE ? OR c.ai_summary LIKE ? OR c.id IN "
            "(SELECT clip_id FROM tags WHERE name LIKE ?))"
        )
        term = f"%{search}%"
        params.extend([term, term, term, term])

    where = " AND ".join(where_parts) if where_parts else "1=1"

    count = conn.execute(
        f"SELECT COUNT(*) as cnt FROM clips c WHERE {where}", params
    ).fetchone()["cnt"]

    offset = (page - 1) * page_size
    rows = conn.execute(
        f"SELECT c.* FROM clips c WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [page_size, offset],
    ).fetchall()

    clips = []
    for row in rows:
        clip = _row_to_clip(row)
        tags = conn.execute(
            "SELECT name FROM tags WHERE clip_id = ?", (clip.id,)
        ).fetchall()
        clip.tags = [t["name"] for t in tags]
        clips.append(clip)

    pages = max(1, (count + page_size - 1) // page_size)
    return ClipPage(clips=clips, total=count, page=page, page_size=page_size, pages=pages)


def add_tag(conn: sqlite3.Connection, clip_id: int, name: str, source: str = "manual"):
    conn.execute(
        "INSERT OR IGNORE INTO tags (clip_id, name, source) VALUES (?, ?, ?)",
        (clip_id, name, source),
    )
    conn.commit()


def remove_tag(conn: sqlite3.Connection, clip_id: int, name: str):
    conn.execute(
        "DELETE FROM tags WHERE clip_id = ? AND name = ?", (clip_id, name)
    )
    conn.commit()


def get_all_tags(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT name, COUNT(*) as count FROM tags GROUP BY name ORDER BY count DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_stats(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) as cnt FROM clips").fetchone()["cnt"]
    total_size = conn.execute(
        "SELECT COALESCE(SUM(file_size_bytes), 0) as s FROM clips"
    ).fetchone()["s"]
    total_duration = conn.execute(
        "SELECT COALESCE(SUM(duration_seconds), 0) as s FROM clips"
    ).fetchone()["s"]
    shares = conn.execute(
        "SELECT share_name, COUNT(*) as cnt FROM clips GROUP BY share_name"
    ).fetchall()
    formats = conn.execute(
        "SELECT source_format, COUNT(*) as cnt FROM clips GROUP BY source_format"
    ).fetchall()
    thumbnailed = conn.execute(
        "SELECT COUNT(*) as cnt FROM clips WHERE thumbnail_path IS NOT NULL"
    ).fetchone()["cnt"]
    analyzed = conn.execute(
        "SELECT COUNT(*) as cnt FROM clips WHERE ai_analyzed_at IS NOT NULL"
    ).fetchone()["cnt"]
    tagged = conn.execute(
        "SELECT COUNT(DISTINCT clip_id) as cnt FROM tags"
    ).fetchone()["cnt"]
    top_rated = conn.execute(
        "SELECT COUNT(*) as cnt FROM clips WHERE ai_score >= 7 AND duplicate_of IS NULL"
    ).fetchone()["cnt"]

    return {
        "total_clips": total,
        "total_size_bytes": total_size,
        "total_duration_seconds": total_duration,
        "by_share": {r["share_name"] or "unknown": r["cnt"] for r in shares},
        "by_format": {r["source_format"] or "unknown": r["cnt"] for r in formats},
        "thumbnailed": thumbnailed,
        "analyzed": analyzed,
        "tagged": tagged,
        "top_rated": top_rated,
    }


def get_timeline(conn: sqlite3.Connection) -> list[dict]:
    """Get clip counts grouped by year-month."""
    rows = conn.execute("""
        SELECT strftime('%Y-%m', recorded_at) as month, COUNT(*) as count
        FROM clips
        WHERE recorded_at IS NOT NULL AND duplicate_of IS NULL
        GROUP BY month
        ORDER BY month
    """).fetchall()
    return [dict(r) for r in rows]


def log_scan_start(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO scan_log (started_at, status) VALUES (?, 'running')",
        (datetime.now().isoformat(),),
    )
    conn.commit()
    return cur.lastrowid


def log_scan_finish(
    conn: sqlite3.Connection,
    scan_id: int,
    result: ScanResult,
):
    conn.execute(
        """UPDATE scan_log SET
            finished_at=?, clips_found=?, clips_new=?, clips_updated=?,
            errors=?, status=?
        WHERE id=?""",
        (
            _dt(result.finished_at),
            result.clips_found,
            result.clips_new,
            result.clips_updated,
            result.errors,
            result.status,
            scan_id,
        ),
    )
    conn.commit()


def update_clip_thumbnail(conn: sqlite3.Connection, clip_id: int, thumb_path: str):
    conn.execute(
        "UPDATE clips SET thumbnail_path = ?, updated_at = datetime('now') WHERE id = ?",
        (thumb_path, clip_id),
    )
    conn.commit()


def update_clip_ai(
    conn: sqlite3.Connection,
    clip_id: int,
    agent: str,
    map_name: str | None,
    summary: str | None,
    tags: list[str],
    score: int | None = None,
    kills: int | None = None,
    highlight_type: str | None = None,
    clutch_type: str | None = None,
    weapon: str | None = None,
    player_agent: str | None = None,
    deaths: int | None = None,
    is_ace: bool = False,
    round_outcome: str | None = None,
    confidence: float | None = None,
):
    now = datetime.now().isoformat()
    conn.execute(
        """UPDATE clips SET
            ai_agent=?, ai_map=?, ai_summary=?, ai_score=?, ai_kills=?,
            ai_highlight_type=?, ai_clutch_type=?, ai_weapon=?,
            ai_player_agent=?, ai_deaths=?, ai_is_ace=?,
            ai_round_outcome=?, ai_confidence=?, ai_analyzed_at=?,
            updated_at=datetime('now')
        WHERE id=?""",
        (agent, map_name, summary, score, kills, highlight_type,
         clutch_type, weapon, player_agent, deaths,
         1 if is_ace else 0, round_outcome, confidence, now, clip_id),
    )
    for tag_name in tags:
        conn.execute(
            "INSERT OR IGNORE INTO tags (clip_id, name, source) VALUES (?, ?, 'ai')",
            (clip_id, tag_name),
        )
    conn.commit()


def get_top_clips(conn: sqlite3.Connection, limit: int = 20) -> list[Clip]:
    """Get the highest-scoring clips by AI score."""
    rows = conn.execute(
        """SELECT * FROM clips
        WHERE ai_score IS NOT NULL AND duplicate_of IS NULL
        ORDER BY ai_score DESC, ai_kills DESC
        LIMIT ?""",
        (limit,),
    ).fetchall()
    clips = []
    for row in rows:
        clip = _row_to_clip(row)
        tags = conn.execute(
            "SELECT name FROM tags WHERE clip_id = ?", (clip.id,)
        ).fetchall()
        clip.tags = [t["name"] for t in tags]
        clips.append(clip)
    return clips


def clips_without_thumbnails(conn: sqlite3.Connection, limit: int | None = None) -> list[Clip]:
    q = "SELECT * FROM clips WHERE thumbnail_path IS NULL ORDER BY recorded_at DESC"
    if limit:
        q += f" LIMIT {limit}"
    rows = conn.execute(q).fetchall()
    return [_row_to_clip(r) for r in rows]


def clips_without_analysis(conn: sqlite3.Connection, limit: int | None = None) -> list[Clip]:
    q = "SELECT * FROM clips WHERE ai_analyzed_at IS NULL ORDER BY recorded_at DESC"
    if limit:
        q += f" LIMIT {limit}"
    rows = conn.execute(q).fetchall()
    return [_row_to_clip(r) for r in rows]


def get_sessions(conn: sqlite3.Connection, gap_minutes: int = 30) -> list[dict]:
    """Group clips into gaming sessions based on recording time proximity.

    Clips recorded within gap_minutes of each other are in the same session.
    Returns sessions sorted by date descending.
    """
    rows = conn.execute("""
        SELECT id, recorded_at, duration_seconds, ai_score, filename, share_name
        FROM clips
        WHERE recorded_at IS NOT NULL AND duplicate_of IS NULL
        ORDER BY recorded_at ASC
    """).fetchall()

    if not rows:
        return []

    sessions: list[dict] = []
    current_session: list[dict] = []

    for row in rows:
        clip = dict(row)
        if not current_session:
            current_session.append(clip)
            continue

        # Check time gap from last clip
        prev_time = datetime.fromisoformat(current_session[-1]["recorded_at"])
        curr_time = datetime.fromisoformat(clip["recorded_at"])
        gap = (curr_time - prev_time).total_seconds() / 60

        if gap <= gap_minutes:
            current_session.append(clip)
        else:
            # Close current session
            sessions.append(_summarize_session(current_session))
            current_session = [clip]

    if current_session:
        sessions.append(_summarize_session(current_session))

    sessions.reverse()  # Most recent first
    return sessions


def _summarize_session(clips: list[dict]) -> dict:
    """Summarize a group of clips into a session dict."""
    start = datetime.fromisoformat(clips[0]["recorded_at"])
    end = datetime.fromisoformat(clips[-1]["recorded_at"])
    total_dur = sum(c.get("duration_seconds") or 0 for c in clips)
    scores = [c["ai_score"] for c in clips if c.get("ai_score")]
    best_score = max(scores) if scores else None
    best_clip = None
    if best_score:
        best_clip = next(c for c in clips if c.get("ai_score") == best_score)

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "date": start.strftime("%Y-%m-%d"),
        "time": start.strftime("%H:%M"),
        "clip_count": len(clips),
        "total_duration": total_dur,
        "best_score": best_score,
        "best_clip_id": best_clip["id"] if best_clip else None,
        "best_clip_name": best_clip["filename"] if best_clip else None,
        "clip_ids": [c["id"] for c in clips],
        "share": clips[0].get("share_name"),
    }


def get_related_clips(conn: sqlite3.Connection, clip_id: int, limit: int = 12) -> list[Clip]:
    """Find clips related to a given clip by session proximity and shared tags."""
    clip = get_clip(conn, clip_id)
    if not clip:
        return []

    related_ids: dict[int, float] = {}  # id -> relevance score

    # 1. Same session (recorded within 30 min)
    if clip.recorded_at:
        rows = conn.execute("""
            SELECT id, recorded_at FROM clips
            WHERE id != ? AND duplicate_of IS NULL AND recorded_at IS NOT NULL
            AND abs(julianday(recorded_at) - julianday(?)) < (30.0 / 1440.0)
            ORDER BY abs(julianday(recorded_at) - julianday(?))
            LIMIT 20
        """, (clip_id, clip.recorded_at.isoformat(), clip.recorded_at.isoformat())).fetchall()
        for r in rows:
            related_ids[r["id"]] = related_ids.get(r["id"], 0) + 3.0

    # 2. Shared tags
    if clip.tags:
        placeholders = ",".join("?" * len(clip.tags))
        rows = conn.execute(f"""
            SELECT clip_id, COUNT(*) as shared FROM tags
            WHERE name IN ({placeholders}) AND clip_id != ?
            GROUP BY clip_id ORDER BY shared DESC LIMIT 20
        """, (*clip.tags, clip_id)).fetchall()
        for r in rows:
            related_ids[r["clip_id"]] = related_ids.get(r["clip_id"], 0) + r["shared"] * 1.5

    # 3. Similar score (within 1 point)
    if clip.ai_score:
        rows = conn.execute("""
            SELECT id FROM clips
            WHERE id != ? AND duplicate_of IS NULL
            AND ai_score BETWEEN ? AND ?
            ORDER BY ai_score DESC LIMIT 20
        """, (clip_id, clip.ai_score - 1, clip.ai_score + 1)).fetchall()
        for r in rows:
            related_ids[r["id"]] = related_ids.get(r["id"], 0) + 1.0

    if not related_ids:
        return []

    # Sort by relevance and fetch full clips
    sorted_ids = sorted(related_ids, key=lambda k: related_ids[k], reverse=True)[:limit]
    clips = []
    for cid in sorted_ids:
        c = get_clip(conn, cid)
        if c:
            clips.append(c)
    return clips


def get_filter_options(conn: sqlite3.Connection) -> dict:
    """Get available filter values for the gallery UI."""
    types = conn.execute("""
        SELECT ai_highlight_type, COUNT(*) as c FROM clips
        WHERE ai_highlight_type IS NOT NULL AND duplicate_of IS NULL
        GROUP BY ai_highlight_type ORDER BY c DESC
    """).fetchall()
    maps = conn.execute("""
        SELECT ai_map, COUNT(*) as c FROM clips
        WHERE ai_map IS NOT NULL AND duplicate_of IS NULL
        GROUP BY ai_map ORDER BY c DESC
    """).fetchall()
    score_dist = conn.execute("""
        SELECT ai_score, COUNT(*) as c FROM clips
        WHERE ai_score IS NOT NULL AND duplicate_of IS NULL
        GROUP BY ai_score ORDER BY ai_score DESC
    """).fetchall()
    clutch_types = conn.execute("""
        SELECT ai_clutch_type, COUNT(*) as c FROM clips
        WHERE ai_clutch_type IS NOT NULL AND duplicate_of IS NULL
        GROUP BY ai_clutch_type ORDER BY c DESC
    """).fetchall()
    weapons = conn.execute("""
        SELECT ai_weapon, COUNT(*) as c FROM clips
        WHERE ai_weapon IS NOT NULL AND duplicate_of IS NULL
        GROUP BY ai_weapon ORDER BY c DESC
    """).fetchall()
    agents = conn.execute("""
        SELECT ai_player_agent, COUNT(*) as c FROM clips
        WHERE ai_player_agent IS NOT NULL AND duplicate_of IS NULL
        GROUP BY ai_player_agent ORDER BY c DESC
    """).fetchall()
    ace_count = conn.execute("""
        SELECT COUNT(*) as c FROM clips
        WHERE ai_is_ace = 1 AND duplicate_of IS NULL
    """).fetchone()["c"]
    return {
        "highlight_types": [{"name": r["ai_highlight_type"], "count": r["c"]} for r in types],
        "maps": [{"name": r["ai_map"], "count": r["c"]} for r in maps],
        "score_distribution": [{"score": r["ai_score"], "count": r["c"]} for r in score_dist],
        "clutch_types": [{"name": r["ai_clutch_type"], "count": r["c"]} for r in clutch_types],
        "weapons": [{"name": r["ai_weapon"], "count": r["c"]} for r in weapons],
        "agents": [{"name": r["ai_player_agent"], "count": r["c"]} for r in agents],
        "ace_count": ace_count,
    }


def get_existing_paths(conn: sqlite3.Connection) -> dict[str, int]:
    """Return {file_path: file_size_bytes} for all indexed clips."""
    rows = conn.execute(
        "SELECT file_path, file_size_bytes FROM clips"
    ).fetchall()
    return {r["file_path"]: r["file_size_bytes"] for r in rows}


def find_duplicates(conn: sqlite3.Connection) -> list[list[Clip]]:
    """Find duplicate clips by matching filename + file_size_bytes + duration.

    Returns groups of clips that appear to be the same file in different locations.
    """
    rows = conn.execute("""
        SELECT filename, file_size_bytes, duration_seconds, COUNT(*) as cnt
        FROM clips
        WHERE file_size_bytes IS NOT NULL
        GROUP BY filename, file_size_bytes, duration_seconds
        HAVING cnt > 1
        ORDER BY cnt DESC, file_size_bytes DESC
    """).fetchall()

    groups = []
    for row in rows:
        clips_rows = conn.execute(
            """SELECT * FROM clips
            WHERE filename = ? AND file_size_bytes = ?
            AND (duration_seconds = ? OR (duration_seconds IS NULL AND ? IS NULL))
            ORDER BY share_name, directory""",
            (row["filename"], row["file_size_bytes"],
             row["duration_seconds"], row["duration_seconds"]),
        ).fetchall()
        group = [_row_to_clip(r) for r in clips_rows]
        if len(group) > 1:
            groups.append(group)

    return groups


def mark_duplicate(conn: sqlite3.Connection, clip_id: int, original_id: int):
    """Mark a clip as a duplicate of another."""
    conn.execute(
        "UPDATE clips SET duplicate_of = ?, updated_at = datetime('now') WHERE id = ?",
        (original_id, clip_id),
    )
    conn.commit()


def get_duplicate_stats(conn: sqlite3.Connection) -> dict:
    """Get summary of duplicate detection results."""
    total_dupes = conn.execute(
        "SELECT COUNT(*) as cnt FROM clips WHERE duplicate_of IS NOT NULL"
    ).fetchone()["cnt"]
    wasted_bytes = conn.execute(
        "SELECT COALESCE(SUM(file_size_bytes), 0) as s FROM clips WHERE duplicate_of IS NOT NULL"
    ).fetchone()["s"]
    return {"duplicate_count": total_dupes, "wasted_bytes": wasted_bytes}
