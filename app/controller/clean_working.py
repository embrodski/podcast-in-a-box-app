"""Controller helpers for Clean Old Working Files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.controller.paths import DEFAULT_WORK_ROOT, PROCESS_LOG_PATH, ensure_scripts_path


def list_clean_working_candidates() -> list[dict[str, Any]]:
    ensure_scripts_path()
    from piab_clean_working_files import list_clean_candidates

    return [c.to_dict() for c in list_clean_candidates(log_path=PROCESS_LOG_PATH)]


def scan_lost_clean_sessions() -> list[dict[str, Any]]:
    ensure_scripts_path()
    from piab_clean_working_files import scan_lost_clean_candidates

    return [
        c.to_dict()
        for c in scan_lost_clean_candidates(
            root=DEFAULT_WORK_ROOT,
            log_path=PROCESS_LOG_PATH,
        )
    ]


def clean_working_files(project_folders: list[Path]) -> list[dict[str, Any]]:
    ensure_scripts_path()
    from piab_clean_working_files import clean_selected_projects

    results = clean_selected_projects(
        [Path(p).resolve() for p in project_folders],
        log_path=PROCESS_LOG_PATH,
    )
    return [r.to_dict() for r in results]
