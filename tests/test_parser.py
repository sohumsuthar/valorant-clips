"""Tests for filename parser."""

from datetime import datetime
from valclips.parser import parse_filename, is_valorant_clip, extract_directory


class TestParseFilename:
    def test_dvr_format(self):
        result = parse_filename("Valorant 2024.03.15 - 14.30.22.01.DVR.mp4")
        assert result["recorded_at"] == datetime(2024, 3, 15, 14, 30, 22)
        assert result["clip_sequence"] == 1
        assert result["source_format"] == "dvr"

    def test_dvr_format_case_insensitive(self):
        result = parse_filename("valorant 2024.03.15 - 14.30.22.01.DVR.mp4")
        assert result["source_format"] == "dvr"
        assert result["recorded_at"] == datetime(2024, 3, 15, 14, 30, 22)

    def test_dvr_high_sequence(self):
        result = parse_filename("Valorant 2025.01.01 - 00.00.00.99.DVR.mp4")
        assert result["clip_sequence"] == 99

    def test_replay_format(self):
        result = parse_filename("VALORANT_replay_2024.05.20-19.45.mp4")
        assert result["recorded_at"] == datetime(2024, 5, 20, 19, 45)
        assert result["clip_sequence"] is None
        assert result["source_format"] == "replay"

    def test_replay_case_insensitive(self):
        result = parse_filename("valorant_replay_2023.12.01-08.30.mp4")
        assert result["source_format"] == "replay"

    def test_unknown_format(self):
        result = parse_filename("aceee.mp4")
        assert result["recorded_at"] is None
        assert result["clip_sequence"] is None
        assert result["source_format"] == "unknown"

    def test_non_valorant_mp4(self):
        result = parse_filename("random_video.mp4")
        assert result["source_format"] == "unknown"

    def test_midnight_timestamp(self):
        result = parse_filename("Valorant 2024.01.01 - 00.00.00.00.DVR.mp4")
        assert result["recorded_at"] == datetime(2024, 1, 1, 0, 0, 0)
        assert result["clip_sequence"] == 0

    def test_end_of_day_timestamp(self):
        result = parse_filename("Valorant 2024.12.31 - 23.59.59.05.DVR.mp4")
        assert result["recorded_at"] == datetime(2024, 12, 31, 23, 59, 59)


class TestIsValorantClip:
    def test_dvr_clip(self):
        assert is_valorant_clip("Valorant 2024.03.15 - 14.30.22.01.DVR.mp4")

    def test_replay_clip(self):
        assert is_valorant_clip("VALORANT_replay_2024.05.20-19.45.mp4")

    def test_case_insensitive(self):
        assert is_valorant_clip("VALORANT 2024.01.01 - 00.00.00.01.DVR.mp4")

    def test_not_mp4(self):
        assert not is_valorant_clip("Valorant 2024.03.15 - 14.30.22.01.DVR.mkv")

    def test_no_valorant(self):
        assert not is_valorant_clip("aceee.mp4")

    def test_non_video(self):
        assert not is_valorant_clip("readme.txt")


class TestExtractDirectory:
    def test_unix_path(self):
        assert extract_directory("/home/user/clips/file.mp4") == "/home/user/clips"

    def test_nested_path(self):
        assert extract_directory("/a/b/c/d/file.mp4") == "/a/b/c/d"
