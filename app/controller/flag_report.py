"""Load final-edit flag / pause-flag report text for the done screen."""

from __future__ import annotations

from pathlib import Path


def load_flag_report_text(state: dict, *, working_folder: Path | None = None) -> str:
    """Return flag report text from session state or Temp artifacts."""
    from app.controller.paths import ensure_scripts_path

    ensure_scripts_path()
    from podcast_flag_phrases import load_flag_report_text as _load

    return _load(state, working_folder=working_folder)
