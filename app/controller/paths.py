"""Repository paths and script import helpers."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
ASSETS_DIR = REPO_ROOT / "assets"
APP_DISPLAY_NAME = "Podcast in a Box"
APP_ORGANIZATION_NAME = "Lighthaven"
APP_USER_MODEL_ID = "Lighthaven.PodcastInABox"
APP_ICON_ICO = ASSETS_DIR / "piab.ico"
DEFAULT_SCAN_ROOT = Path(r"E:\PodcastRoom")
DEFAULT_APP_HOME = DEFAULT_SCAN_ROOT / "PodcastInABox"
DEFAULT_WORK_ROOT = DEFAULT_APP_HOME / "Sessions"
DEFAULT_VMIX_PRESET_DIRS = (DEFAULT_SCAN_ROOT / "vMix Configs",)
DEFAULT_VMIX_PRESET_NAME = "4 People - 5 Cameras - Default.vmix"
APP_LOCK_PATH = DEFAULT_WORK_ROOT / ".piab-app.lock"
PROCESS_LOG_FILENAME = "piab-process-log.json"
PROCESS_LOG_PATH = DEFAULT_WORK_ROOT / PROCESS_LOG_FILENAME
JOB_QUEUE_FILENAME = "piab-job-queue.json"
JOB_QUEUE_PATH = DEFAULT_WORK_ROOT / JOB_QUEUE_FILENAME
_LEGACY_WORK_FILENAMES = (
    PROCESS_LOG_FILENAME,
    JOB_QUEUE_FILENAME,
    ".piab-app.lock",
)


def ensure_work_root(root: Path = DEFAULT_WORK_ROOT) -> Path:
    folder = root.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def migrate_legacy_work_files(
    *,
    legacy_root: Path = DEFAULT_SCAN_ROOT,
    work_root: Path = DEFAULT_WORK_ROOT,
    extra_legacy_roots: tuple[Path, ...] | None = None,
) -> None:
    """Move PIAB lock/log/queue files from older work roots into Sessions."""
    dest_root = ensure_work_root(work_root)
    dest_resolved = dest_root.resolve()
    if extra_legacy_roots is None:
        extra: tuple[Path, ...] = ()
        if Path(legacy_root).resolve() == DEFAULT_SCAN_ROOT.resolve():
            extra = (DEFAULT_APP_HOME,)
    else:
        extra = extra_legacy_roots
    seen: set[Path] = set()
    for src_root in (legacy_root, *extra):
        src_resolved = Path(src_root).resolve()
        if src_resolved == dest_resolved or src_resolved in seen:
            continue
        seen.add(src_resolved)
        for name in _LEGACY_WORK_FILENAMES:
            src = src_resolved / name
            dest = dest_resolved / name
            if not src.is_file() or dest.exists():
                continue
            try:
                src.replace(dest)
            except OSError:
                pass

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
