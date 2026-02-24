"""Paths, constants, and settings."""

import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = PROJECT_DIR / "clips.db"
THUMBNAIL_DIR = PROJECT_DIR / "thumbnails"
STATIC_DIR = PROJECT_DIR / "static"
TEMPLATE_DIR = Path(__file__).resolve().parent / "web" / "templates"

# SMB share paths -- Linux GVFS by default, override via env or --dir flag
# On Windows/Mac, use `valclips scan --dir "Z:\clips"` or similar instead.
_uid = os.getuid() if hasattr(os, "getuid") else 1000
GVFS_BASE = Path(f"/run/user/{_uid}/gvfs")
SHARES: dict[str, Path] = {}

if sys.platform == "linux" and GVFS_BASE.exists():
    for share_name in ("prometheus", "kronos", "atlas"):
        share_path = GVFS_BASE / f"smb-share:server=chaos.local,share={share_name}"
        if share_path.exists():
            SHARES[share_name] = share_path

# Known clip directories within each share -- scan these instead of walking
# the entire multi-TB share. Paths are relative to the share root.
# "valorant_only" means only index files matching Valorant filename patterns.
# "all_mp4" means index every .mp4 (for dirs with custom-named clips).
CLIP_DIRS: dict[str, list[dict]] = {
    "prometheus": [
        {"path": "poseidon/theta/older", "mode": "valorant_only"},
        {"path": "poseidon/theta/Valorant", "mode": "valorant_only"},
        {"path": "poseidon/theta/clips/older", "mode": "valorant_only"},
        {"path": "poseidon/theta/best", "mode": "all_mp4"},
        {"path": "poseidon/theta/sherf", "mode": "all_mp4"},
        {"path": "poseidon/theta/New folder", "mode": "valorant_only"},
        {"path": "poseidon/theta/phx", "mode": "all_mp4"},
    ],
    "kronos": [
        {"path": "theta/Valorant", "mode": "valorant_only"},
        {"path": "theta/Valorant_3_8_25", "mode": "valorant_only"},
        {"path": "theta/scort aum", "mode": "valorant_only"},
        {"path": "theta/clips", "mode": "valorant_only"},
        {"path": "data/0424 scort/sohum/Videos/VALORANT", "mode": "valorant_only"},
        {"path": "data/SHIT 7 4 23", "mode": "valorant_only"},
        {"path": "data/vid", "mode": "valorant_only"},
        {"path": "data/0424 scort/scort", "mode": "valorant_only"},
        {"path": "data/val", "mode": "all_mp4"},
        {"path": "data/new val 127", "mode": "valorant_only"},
        {"path": "data/val 5 13 2023", "mode": "valorant_only"},
        {"path": "data/finished vids", "mode": "all_mp4"},
        {"path": "data/from data/op", "mode": "all_mp4"},
        {"path": "data/from data/knives", "mode": "all_mp4"},
        {"path": "data/from data/guardian", "mode": "all_mp4"},
        {"path": "data/0424 scort/VALORANT", "mode": "valorant_only"},
        # Skipping data/from data/new val 127 -- known exact duplicate of data/new val 127
    ],
}

# Folder name -> auto-tag mappings
FOLDER_TAGS: dict[str, list[str]] = {
    "best": ["highlight"],
    "sherf": ["sheriff"],
    "phx": ["phoenix"],
    "guardian": ["guardian"],
    "knives": ["knife-kill"],
    "op": ["operator"],
    "finished vids": ["edited"],
    "new skin reyna": ["reyna"],
    "clips": ["clip"],
}

# Local directories to scan (Windows / non-GVFS paths).
# Each entry has a root path, a list of subdirectories, and scan mode.
LOCAL_DIRS: dict[str, list[dict]] = {}

if sys.platform == "win32":
    # S: drive -- NVIDIA ShadowPlay recordings
    _nvidia = Path("S:/NVIDIA")
    if _nvidia.exists():
        LOCAL_DIRS["nvidia-s"] = [
            {"path": "Valorant", "mode": "valorant_only"},
        ]
        SHARES["nvidia-s"] = _nvidia

    # S: drive -- test clips
    _test = Path("S:/test clips")
    if _test.exists():
        LOCAL_DIRS["test-clips"] = [
            {"path": ".", "mode": "all_mp4"},
        ]
        SHARES["test-clips"] = _test

    # K: drive -- bulk archive (multiple backup snapshots)
    _k = Path("K:/")
    if _k.exists():
        LOCAL_DIRS["archive-k"] = [
            {"path": "theta/Valorant", "mode": "valorant_only"},
            {"path": "theta/Valorant_3_8_25", "mode": "valorant_only"},
            {"path": "theta/clips", "mode": "valorant_only"},
            {"path": "theta/New folder (2)/Videos/Valorant", "mode": "valorant_only"},
            {"path": "data/SHIT 7 4 23/VALORANT", "mode": "valorant_only"},
            {"path": "data/vid/VALORANT", "mode": "all_mp4"},
            {"path": "data/vid/VALORANT/old clips that are good", "mode": "all_mp4"},
            {"path": "data/0424 scort/scort/VALORANT", "mode": "valorant_only"},
            {"path": "data/val 5 13 2023/VALORANT", "mode": "valorant_only"},
            {"path": "data/0424 scort/VALORANT", "mode": "valorant_only"},
        ]
        SHARES["archive-k"] = _k

    # L: drive -- mixed clips
    _l = Path("L:/")
    if _l.exists():
        LOCAL_DIRS["clips-l"] = [
            {"path": "VALORANT", "mode": "all_mp4"},
            {"path": "clips", "mode": "all_mp4"},
        ]
        SHARES["clips-l"] = _l

    # P: drive -- network/shared storage (poseidon)
    _p = Path("P:/poseidon/theta")
    if _p.exists():
        LOCAL_DIRS["poseidon-p"] = [
            {"path": "Valorant", "mode": "valorant_only"},
            {"path": "clips", "mode": "valorant_only"},
            {"path": "clips/older", "mode": "valorant_only"},
            {"path": "older", "mode": "valorant_only"},
            {"path": "best", "mode": "all_mp4"},
            {"path": "sherf", "mode": "all_mp4"},
            {"path": "phx", "mode": "all_mp4"},
            {"path": "New folder", "mode": "valorant_only"},
        ]
        SHARES["poseidon-p"] = _p

    # C: drive -- current system ShadowPlay output
    _c_vid = Path("C:/Users/sohum/Videos")
    if _c_vid.exists():
        LOCAL_DIRS["local-c"] = [
            {"path": "Valorant", "mode": "valorant_only"},
            {"path": "NVIDIA/Valorant", "mode": "valorant_only"},
        ]
        SHARES["local-c"] = _c_vid

# Auto-detect FFmpeg/FFprobe on Windows (winget installs to a deep path)
if sys.platform == "win32":
    import glob as _glob
    _winget_pattern = os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\ffmpeg-*\bin"
    )
    for _bin_dir in _glob.glob(_winget_pattern):
        if os.path.isfile(os.path.join(_bin_dir, "ffprobe.exe")):
            os.environ["PATH"] = _bin_dir + ";" + os.environ.get("PATH", "")
            break

FFPROBE_TIMEOUT = 30  # seconds
BATCH_SIZE = 50
DEFAULT_PAGE_SIZE = 50
WEB_HOST = "0.0.0.0"
WEB_PORT = 8000
