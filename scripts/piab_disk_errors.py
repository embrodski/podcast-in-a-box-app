"""Detect disk-full failures and format user-facing storage guidance."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

DISK_FULL_RE = re.compile(
    r"no space left on device|not enough space on the disk|"
    r"error code:\s*-28|\berrno\s*28\b|disk full|there is not enough space",
    re.IGNORECASE,
)
_WINDOWS_PATH_RE = re.compile(r"([A-Za-z]):\\")


def is_disk_full_error(text: str) -> bool:
    return bool(text and DISK_FULL_RE.search(text))


def drive_letter_from_text(text: str, *, fallback: Path | None = None) -> str | None:
    for match in _WINDOWS_PATH_RE.finditer(text or ""):
        return match.group(1).upper()
    if fallback is not None:
        drive = fallback.drive
        if drive and len(drive) >= 2 and drive[1] == ":":
            return drive[0].upper()
    return None


def source_duration_sec_from_folder(working_folder: Path | None) -> float | None:
    if working_folder is None:
        return None
    state_path = working_folder / "podcast-in-a-box.json"
    if not state_path.is_file():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = state.get("source_duration_sec")
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


def estimate_session_disk_gb(source_duration_sec: float | None) -> float:
    """Rough free-space target for prep (matches preflight ~70 GB/hour)."""
    if source_duration_sec is None or source_duration_sec <= 0:
        return 70.0
    minutes = source_duration_sec / 60.0
    return max(10.0, round(minutes * 1.2, 1))


def free_bytes_on_drive(drive_letter: str) -> int | None:
    root = Path(f"{drive_letter.upper()}:\\")
    try:
        _total, _used, free = shutil.disk_usage(root)
    except OSError:
        return None
    return free


def _bytes_human(n: int) -> str:
    gib = n / (1024**3)
    if gib >= 10:
        return f"{gib:.0f} GB"
    return f"{gib:.1f} GB"


def format_disk_full_user_message(
    error_text: str,
    *,
    working_folder: Path | None = None,
    source_duration_sec: float | None = None,
) -> str:
    if source_duration_sec is None and working_folder is not None:
        source_duration_sec = source_duration_sec_from_folder(working_folder)

    drive = drive_letter_from_text(error_text, fallback=working_folder)
    needed_gb = estimate_session_disk_gb(source_duration_sec)

    if drive:
        drive_label = f"drive {drive}:"
        free = free_bytes_on_drive(drive)
        free_clause = (
            f" It currently has about {_bytes_human(free)} free."
            if free is not None
            else ""
        )
    else:
        drive_label = "the drive where your session is stored"
        free_clause = ""

    if source_duration_sec:
        minutes = int(round(source_duration_sec / 60.0))
        need_clause = (
            f"Plan for about {needed_gb:.0f} GB free on {drive_label} "
            f"for this ~{minutes}-minute session (~1.2 GB per minute of recording, "
            f"including synced and prepped copies)."
        )
    else:
        need_clause = (
            f"Plan for about {needed_gb:.0f} GB free on {drive_label} "
            f"(~70 GB per hour of source recording, or ~1.2 GB per minute)."
        )

    return (
        f"Not enough free disk space on {drive_label}{free_clause} "
        f"{need_clause} Free up space on that drive, then try again."
    )


def summarize_disk_full_if_applicable(
    exc: BaseException,
    *,
    working_folder: Path | None = None,
    source_duration_sec: float | None = None,
) -> str | None:
    text = str(exc).strip() or repr(exc)
    if not is_disk_full_error(text):
        return None
    return format_disk_full_user_message(
        text,
        working_folder=working_folder,
        source_duration_sec=source_duration_sec,
    )
