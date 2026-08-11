"""Repository paths and script import helpers."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
ASSETS_DIR = REPO_ROOT / "assets"
DEFAULT_SCAN_ROOT = Path(r"E:\PodcastRoom")
DEFAULT_VMIX_PRESET_DIRS = (DEFAULT_SCAN_ROOT / "vMix Configs",)
DEFAULT_VMIX_PRESET_NAME = "4 People - 5 Cameras - Default.vmix"
APP_LOCK_PATH = DEFAULT_SCAN_ROOT / ".piab-app.lock"

MIN_DISK_BYTES_WARN = 50 * 1024**3
MIN_DISK_BYTES_FAIL = 5 * 1024**3


def ensure_scripts_path() -> Path:
    scripts = str(SCRIPTS_DIR)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return SCRIPTS_DIR


def find_vmix_preset() -> Path | None:
    for directory in DEFAULT_VMIX_PRESET_DIRS:
        for name in (DEFAULT_VMIX_PRESET_NAME, "Default .vmix"):
            path = directory / name
            if path.is_file():
                return path
    return None
