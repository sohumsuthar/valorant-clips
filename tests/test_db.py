"""Tests for database operations."""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from valclips.db import (
    init_db, get_connection, upsert_clip, upsert_clips_batch,
    get_clip, get_clip_by_path, list_clips, add_tag, remove_tag,
    get_all_tags, get_stats, find_duplicates, mark_duplicate,
    get_duplicate_stats, get_existing_paths, get_adjacent_clip_ids,
    get_timeline, clips_without_thumbnails, clips_without_analysis,
    update_clip_thumbnail, update_clip_ai,
)
from valclips.models import Clip


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    init_db(path)
    return path


@pytest.fixture
def conn(db_path):
    with get_connection(db_path) as c:
        yield c


def _make_clip(idx: int, **overrides) -> Clip:
    defaults = {
        "file_path": f"/clips/clip_{idx}.mp4",
        "filename": f"Valorant 2024.01.{idx:02d} - 12.00.00.01.DVR.mp4",
        "recorded_at": datetime(2024, 1, idx % 28 + 1, 12, 0, 0),
        "source_format": "dvr",
        "duration_seconds": 90.0,
        "width": 1920,
        "height": 1080,
        "fps": 60.0,
        "codec": "h264",
        "file_size_bytes": 500_000_000,
        "share_name": "prometheus",
        "directory": "/clips",
    }
    defaults.update(overrides)
    return Clip(**defaults)


class TestUpsert:
    def test_insert_new(self, conn):
        clip = _make_clip(1)
        clip_id = upsert_clip(conn, clip)
        conn.commit()
        assert clip_id > 0

    def test_upsert_updates_existing(self, conn):
        clip = _make_clip(1)
        id1 = upsert_clip(conn, clip)
        conn.commit()
        clip.duration_seconds = 120.0
        id2 = upsert_clip(conn, clip)
        conn.commit()
        assert id1 == id2
        fetched = get_clip(conn, id1)
        assert fetched.duration_seconds == 120.0

    def test_batch_upsert(self, conn):
        clips = [_make_clip(i) for i in range(1, 6)]
        new, updated = upsert_clips_batch(conn, clips)
        assert new == 5
        assert updated == 0

    def test_batch_upsert_mixed(self, conn):
        clips = [_make_clip(i) for i in range(1, 4)]
        upsert_clips_batch(conn, clips)
        # Upsert same first clip + a new one
        batch2 = [_make_clip(1, duration_seconds=999.0), _make_clip(10)]
        new, updated = upsert_clips_batch(conn, batch2)
        assert new == 1
        assert updated == 1


class TestGetClip:
    def test_get_by_id(self, conn):
        clip_id = upsert_clip(conn, _make_clip(1))
        conn.commit()
        fetched = get_clip(conn, clip_id)
        assert fetched is not None
        assert fetched.filename == _make_clip(1).filename

    def test_get_missing(self, conn):
        assert get_clip(conn, 99999) is None

    def test_get_by_path(self, conn):
        clip = _make_clip(1)
        upsert_clip(conn, clip)
        conn.commit()
        fetched = get_clip_by_path(conn, clip.file_path)
        assert fetched is not None
        assert fetched.file_path == clip.file_path

    def test_get_includes_tags(self, conn):
        clip_id = upsert_clip(conn, _make_clip(1))
        conn.commit()
        add_tag(conn, clip_id, "ace")
        add_tag(conn, clip_id, "clutch")
        fetched = get_clip(conn, clip_id)
        assert set(fetched.tags) == {"ace", "clutch"}


class TestListClips:
    def test_pagination(self, conn):
        for i in range(1, 11):
            upsert_clip(conn, _make_clip(i))
        conn.commit()
        page1 = list_clips(conn, page=1, page_size=3)
        assert len(page1.clips) == 3
        assert page1.total == 10
        assert page1.pages == 4

    def test_filter_by_share(self, conn):
        upsert_clip(conn, _make_clip(1, share_name="prometheus"))
        upsert_clip(conn, _make_clip(2, share_name="kronos"))
        conn.commit()
        result = list_clips(conn, share="prometheus")
        assert result.total == 1
        assert result.clips[0].share_name == "prometheus"

    def test_filter_by_tag(self, conn):
        id1 = upsert_clip(conn, _make_clip(1))
        upsert_clip(conn, _make_clip(2))
        conn.commit()
        add_tag(conn, id1, "ace")
        result = list_clips(conn, tag="ace")
        assert result.total == 1

    def test_search(self, conn):
        upsert_clip(conn, _make_clip(1, filename="ace_reyna_clutch.mp4"))
        upsert_clip(conn, _make_clip(2, filename="boring_clip.mp4"))
        conn.commit()
        result = list_clips(conn, search="reyna")
        assert result.total == 1

    def test_hide_dupes(self, conn):
        id1 = upsert_clip(conn, _make_clip(1))
        id2 = upsert_clip(conn, _make_clip(2))
        conn.commit()
        mark_duplicate(conn, id2, id1)
        result = list_clips(conn, hide_dupes=True)
        assert result.total == 1
        result2 = list_clips(conn, hide_dupes=False)
        assert result2.total == 2

    def test_sort_by_size(self, conn):
        upsert_clip(conn, _make_clip(1, file_size_bytes=100))
        upsert_clip(conn, _make_clip(2, file_size_bytes=999))
        conn.commit()
        result = list_clips(conn, sort="size")
        assert result.clips[0].file_size_bytes == 999


class TestTags:
    def test_add_and_get(self, conn):
        clip_id = upsert_clip(conn, _make_clip(1))
        conn.commit()
        add_tag(conn, clip_id, "ace")
        tags = get_all_tags(conn)
        assert len(tags) == 1
        assert tags[0]["name"] == "ace"
        assert tags[0]["count"] == 1

    def test_duplicate_tag_ignored(self, conn):
        clip_id = upsert_clip(conn, _make_clip(1))
        conn.commit()
        add_tag(conn, clip_id, "ace")
        add_tag(conn, clip_id, "ace")  # Should not error
        tags = get_all_tags(conn)
        assert tags[0]["count"] == 1

    def test_remove_tag(self, conn):
        clip_id = upsert_clip(conn, _make_clip(1))
        conn.commit()
        add_tag(conn, clip_id, "ace")
        remove_tag(conn, clip_id, "ace")
        tags = get_all_tags(conn)
        assert len(tags) == 0


class TestDuplicates:
    def test_find_duplicates(self, conn):
        # Same filename + size + duration = duplicate
        upsert_clip(conn, _make_clip(1, file_path="/a/clip.mp4", filename="clip.mp4",
                                      file_size_bytes=1000, duration_seconds=90.0))
        upsert_clip(conn, _make_clip(2, file_path="/b/clip.mp4", filename="clip.mp4",
                                      file_size_bytes=1000, duration_seconds=90.0))
        conn.commit()
        groups = find_duplicates(conn)
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_different_size_not_duplicate(self, conn):
        upsert_clip(conn, _make_clip(1, file_path="/a/clip.mp4", filename="clip.mp4",
                                      file_size_bytes=1000))
        upsert_clip(conn, _make_clip(2, file_path="/b/clip.mp4", filename="clip.mp4",
                                      file_size_bytes=2000))
        conn.commit()
        groups = find_duplicates(conn)
        assert len(groups) == 0

    def test_mark_duplicate(self, conn):
        id1 = upsert_clip(conn, _make_clip(1))
        id2 = upsert_clip(conn, _make_clip(2))
        conn.commit()
        mark_duplicate(conn, id2, id1)
        clip = get_clip(conn, id2)
        assert clip.duplicate_of == id1

    def test_duplicate_stats(self, conn):
        id1 = upsert_clip(conn, _make_clip(1, file_size_bytes=500))
        id2 = upsert_clip(conn, _make_clip(2, file_size_bytes=500))
        conn.commit()
        mark_duplicate(conn, id2, id1)
        stats = get_duplicate_stats(conn)
        assert stats["duplicate_count"] == 1
        assert stats["wasted_bytes"] == 500


class TestStats:
    def test_basic_stats(self, conn):
        upsert_clip(conn, _make_clip(1, file_size_bytes=1000, duration_seconds=60.0))
        upsert_clip(conn, _make_clip(2, file_size_bytes=2000, duration_seconds=90.0))
        conn.commit()
        stats = get_stats(conn)
        assert stats["total_clips"] == 2
        assert stats["total_size_bytes"] == 3000
        assert stats["total_duration_seconds"] == 150.0


class TestAdjacentClips:
    def test_prev_next(self, conn):
        upsert_clip(conn, _make_clip(1, recorded_at=datetime(2024, 1, 1)))
        upsert_clip(conn, _make_clip(2, recorded_at=datetime(2024, 1, 2)))
        upsert_clip(conn, _make_clip(3, recorded_at=datetime(2024, 1, 3)))
        conn.commit()
        prev_id, next_id = get_adjacent_clip_ids(conn, 2)
        assert prev_id == 1
        assert next_id == 3

    def test_first_clip_no_prev(self, conn):
        upsert_clip(conn, _make_clip(1, recorded_at=datetime(2024, 1, 1)))
        upsert_clip(conn, _make_clip(2, recorded_at=datetime(2024, 1, 2)))
        conn.commit()
        prev_id, next_id = get_adjacent_clip_ids(conn, 1)
        assert prev_id is None
        assert next_id == 2


class TestTimeline:
    def test_timeline_groups_by_month(self, conn):
        upsert_clip(conn, _make_clip(1, recorded_at=datetime(2024, 1, 5)))
        upsert_clip(conn, _make_clip(2, recorded_at=datetime(2024, 1, 15)))
        upsert_clip(conn, _make_clip(3, recorded_at=datetime(2024, 2, 1)))
        conn.commit()
        timeline = get_timeline(conn)
        assert len(timeline) == 2
        assert timeline[0]["month"] == "2024-01"
        assert timeline[0]["count"] == 2
        assert timeline[1]["month"] == "2024-02"
        assert timeline[1]["count"] == 1


class TestExistingPaths:
    def test_returns_path_size_map(self, conn):
        upsert_clip(conn, _make_clip(1, file_size_bytes=1234))
        conn.commit()
        paths = get_existing_paths(conn)
        assert paths["/clips/clip_1.mp4"] == 1234


class TestThumbnailAndAI:
    def test_update_thumbnail(self, conn):
        clip_id = upsert_clip(conn, _make_clip(1))
        conn.commit()
        update_clip_thumbnail(conn, clip_id, "/thumbs/abc.jpg")
        clip = get_clip(conn, clip_id)
        assert clip.thumbnail_path == "/thumbs/abc.jpg"

    def test_update_ai(self, conn):
        clip_id = upsert_clip(conn, _make_clip(1))
        conn.commit()
        update_clip_ai(conn, clip_id, agent="claude", map_name="Haven",
                       summary="Ace with Jett", tags=["ace", "jett"])
        clip = get_clip(conn, clip_id)
        assert clip.ai_agent == "claude"
        assert clip.ai_map == "Haven"
        assert "ace" in clip.tags
        assert "jett" in clip.tags

    def test_clips_without_thumbnails(self, conn):
        upsert_clip(conn, _make_clip(1))
        id2 = upsert_clip(conn, _make_clip(2))
        conn.commit()
        update_clip_thumbnail(conn, id2, "/thumbs/abc.jpg")
        missing = clips_without_thumbnails(conn)
        assert len(missing) == 1

    def test_clips_without_analysis(self, conn):
        upsert_clip(conn, _make_clip(1))
        id2 = upsert_clip(conn, _make_clip(2))
        conn.commit()
        update_clip_ai(conn, id2, "stub", None, None, [])
        pending = clips_without_analysis(conn)
        assert len(pending) == 1
