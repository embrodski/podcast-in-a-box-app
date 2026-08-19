"""Disk-space gates before recording, prep, and full render."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from app.controller.paths import (
    DEFAULT_SCAN_ROOT,
    MIN_DISK_BYTES_FAIL,
    MIN_DISK_BYTES_WARN,
)

HALF_GIB = 512 * 1024**2
PREP_MULTIPLIER = 4

StorageLevel = Literal["ok", "warn", "critical", "insufficient"]


@dataclass(frozen=True)
class StorageAssessment:
    free_bytes: int
    required_bytes: int
    level: StorageLevel
    message: str
    reference_bytes: int = 0


def bytes_human(n: int) -> str:
    gib = n / (1024**3)
    if gib >= 10:
        return f"{gib:.0f} GB"
    return f"{gib:.1f} GB"


CLEAN_WORKING_FILES_BUTTON = "Clean Old Working Files"


def clean_working_files_button_text(free_bytes: int | None) -> str:
    if free_bytes is None:
        return CLEAN_WORKING_FILES_BUTTON
    gb = max(0, int(round(free_bytes / (1024**3))))
    return f"{CLEAN_WORKING_FILES_BUTTON} ({gb} GB free)"


def free_bytes_at(
    root: Path,
    *,
    disk_usage: Callable[[Path], tuple[int, int, int]] | None = None,
) -> int:
    usage_fn = disk_usage or shutil.disk_usage
    root.mkdir(parents=True, exist_ok=True)
    _total, _used, free = usage_fn(root)
    return int(free)


def largest_file_size(paths: list[Path]) -> int:
    largest = 0
    for path in paths:
        try:
            if path.is_file():
                largest = max(largest, path.stat().st_size)
        except OSError:
            continue
    return largest


def raw_video_candidates(session_folder: Path) -> list[Path]:
    raw = session_folder / "Raw"
    names = (
        "Host Raw Video.mp4",
        "Guest Raw Video.mp4",
        "Wide Raw Video.mp4",
    )
    found = [raw / name for name in names if (raw / name).is_file()]
    if found:
        return found
    if not raw.is_dir():
        return []
    return sorted(raw.glob("*.mp4"))


def prep_file_candidates(session_folder: Path) -> list[Path]:
    input_dir = session_folder / "Input"
    if not input_dir.is_dir():
        return []
    return sorted(input_dir.glob("*-prepped.mp4"))


def largest_raw_video_bytes(session_folder: Path) -> int:
    return largest_file_size(raw_video_candidates(session_folder))


def largest_prep_file_bytes(session_folder: Path) -> int:
    return largest_file_size(prep_file_candidates(session_folder))


def assess_recording_storage(
    root: Path = DEFAULT_SCAN_ROOT,
    *,
    disk_usage: Callable[[Path], tuple[int, int, int]] | None = None,
) -> StorageAssessment:
    free = free_bytes_at(root, disk_usage=disk_usage)
    if free < MIN_DISK_BYTES_FAIL:
        return StorageAssessment(
            free_bytes=free,
            required_bytes=MIN_DISK_BYTES_FAIL,
            level="critical",
            message=(
                f"Critically low disk space: {bytes_human(free)} free "
                f"(need at least {bytes_human(MIN_DISK_BYTES_FAIL)}). "
                "Recording uses about 1.2 GB per minute."
            ),
        )
    if free < MIN_DISK_BYTES_WARN:
        return StorageAssessment(
            free_bytes=free,
            required_bytes=MIN_DISK_BYTES_WARN,
            level="warn",
            message=(
                f"Low disk space: {bytes_human(free)} free "
                f"(recommend ≥ {bytes_human(MIN_DISK_BYTES_WARN)}). "
                "Recording uses about 1.2 GB per minute."
            ),
        )
    return StorageAssessment(
        free_bytes=free,
        required_bytes=0,
        level="ok",
        message=f"{bytes_human(free)} free.",
    )


def assess_prep_storage(
    session_folder: Path,
    *,
    root: Path | None = None,
    disk_usage: Callable[[Path], tuple[int, int, int]] | None = None,
) -> StorageAssessment:
    check_root = root or session_folder
    free = free_bytes_at(check_root, disk_usage=disk_usage)
    reference = largest_raw_video_bytes(session_folder)
    if reference > 0:
        required = reference * PREP_MULTIPLIER
        detail = (
            f"Prep needs about 4× the largest source video "
            f"({bytes_human(reference)} → {bytes_human(required)})."
        )
    else:
        required = MIN_DISK_BYTES_WARN
        detail = (
            f"Could not size source videos; requiring ≥ {bytes_human(required)} free."
        )
    if free < required:
        return StorageAssessment(
            free_bytes=free,
            required_bytes=required,
            level="insufficient",
            message=(
                f"Low disk space: {bytes_human(free)} free; "
                f"need about {bytes_human(required)}. {detail}"
            ),
            reference_bytes=reference,
        )
    return StorageAssessment(
        free_bytes=free,
        required_bytes=required,
        level="ok",
        message=f"{bytes_human(free)} free (need ~{bytes_human(required)}).",
        reference_bytes=reference,
    )


def assess_render_storage(
    session_folder: Path,
    *,
    root: Path | None = None,
    disk_usage: Callable[[Path], tuple[int, int, int]] | None = None,
) -> StorageAssessment:
    check_root = root or session_folder
    free = free_bytes_at(check_root, disk_usage=disk_usage)
    reference = largest_prep_file_bytes(session_folder)
    if reference > 0:
        required = reference + HALF_GIB
        detail = (
            f"Full render needs about the largest prep file "
            f"({bytes_human(reference)}) plus 0.5 GB → {bytes_human(required)}."
        )
    else:
        required = MIN_DISK_BYTES_WARN
        detail = (
            f"Could not size prep files; requiring ≥ {bytes_human(required)} free."
        )
    if free < required:
        return StorageAssessment(
            free_bytes=free,
            required_bytes=required,
            level="insufficient",
            message=(
                f"Low disk space: {bytes_human(free)} free; "
                f"need about {bytes_human(required)}. {detail}"
            ),
            reference_bytes=reference,
        )
    return StorageAssessment(
        free_bytes=free,
        required_bytes=required,
        level="ok",
        message=f"{bytes_human(free)} free (need ~{bytes_human(required)}).",
        reference_bytes=reference,
    )
