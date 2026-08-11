"""Wizard session state shared across autocut screens."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SessionContext:
    """In-memory choices until piab_init_session writes podcast-in-a-box.json."""

    entry_path: str = ""  # record | already_recorded | resume
    delivery_enabled: bool = False
    delivery_email: str | None = None
    source_mode: str = "default"  # default | special
    special_folder: Path | None = None
    session_name: str | None = None
    scan_data: dict | None = None
    session_folder: Path | None = None
    allow_overwrite: bool = False
    video_labels: dict[str, str] | None = None
    audio_labels: dict[str, str] | None = None
    failure_summary: str | None = None
    failure_detail: str | None = None
    failure_retry_screen: str | None = None
    failure_aborted: bool = False

    def reset(self, *, entry_path: str) -> None:
        self.entry_path = entry_path
        self.delivery_enabled = False
        self.delivery_email = None
        self.source_mode = "default"
        self.special_folder = None
        self.session_name = None
        self.scan_data = None
        self.session_folder = None
        self.allow_overwrite = False
        self.video_labels = None
        self.audio_labels = None
        self.failure_summary = None
        self.failure_detail = None
        self.failure_retry_screen = None
        self.failure_aborted = False
