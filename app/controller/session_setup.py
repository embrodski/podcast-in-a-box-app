"""Session scan and delivery helpers for the controller."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.controller.paths import ensure_scripts_path


def scan_session_folder(
    scan_dir: Path,
    *,
    date_filter: date | None = None,
    cluster_index: int = 0,
) -> dict:
    ensure_scripts_path()
    from piab_lib import collect_session_scan

    return collect_session_scan(
        scan_dir,
        date_filter=date_filter,
        cluster_index=cluster_index,
    )


def validate_delivery_email(email: str) -> tuple[bool, str]:
    ensure_scripts_path()
    from harness_delivery_prompt import is_valid_email, normalize_email

    normalized = normalize_email(email)
    if not is_valid_email(normalized):
        return False, "Enter a valid email address."
    return True, normalized
