"""Session folder helpers and podcast-in-a-box.json access."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.controller.paths import DEFAULT_WORK_ROOT, PROCESS_LOG_PATH, ensure_scripts_path

PIAB_STATE_FILENAME = "podcast-in-a-box.json"
CURSOR_STATE_FILENAME = "cursor-podcast-in-a-box.json"


def generate_session_name(
    root: Path = DEFAULT_WORK_ROOT,
    *,
    when: datetime | None = None,
) -> str:
    stamp = (when or datetime.now()).strftime("%Y-%m-%d_%H%M")
    candidate = stamp
    suffix = 2
    while (root / candidate).exists():
        candidate = f"{stamp}_{suffix}"
        suffix += 1
    return candidate


def _session_mtime(folder: Path) -> float:
    path = piab_state_path(folder)
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def list_recent_sessions(
    root: Path = DEFAULT_WORK_ROOT,
    *,
    limit: int = 20,
    log_path: Path | None = None,
    include_process_log: bool = True,
) -> list[Path]:
    """List resumable sessions under the work root and from the process log.

    The process log is how special folders on other drives stay on Resume.
    """
    candidates: list[tuple[float, Path]] = []
    if root.is_dir():
        for state_path in root.glob(f"*/{PIAB_STATE_FILENAME}"):
            folder = state_path.parent.resolve()
            if not is_resumable_piab_session(folder, include_finished=False):
                continue
            candidates.append((_session_mtime(folder), folder))

    if include_process_log:
        ensure_scripts_path()
        from piab_process_log import load_process_log

        log = load_process_log(log_path or PROCESS_LOG_PATH)
        for row in log.get("processes") or []:
            if not isinstance(row, dict):
                continue
            raw = row.get("project_folder") or row.get("id")
            if not isinstance(raw, str) or not raw.strip():
                continue
            folder = Path(raw).resolve()
            if not is_resumable_piab_session(folder, include_finished=False):
                continue
            candidates.append((_session_mtime(folder), folder))

    candidates.sort(key=lambda item: item[0], reverse=True)
    seen: set[Path] = set()
    sessions: list[Path] = []
    for _, folder in candidates:
        if folder in seen:
            continue
        seen.add(folder)
        sessions.append(folder)
        if len(sessions) >= limit:
            break
    return sessions


def remember_session_folder(
    working_folder: Path,
    *,
    log_path: Path | None = None,
) -> None:
    """Record a session path (including special folders on any drive) in the process log."""
    ensure_scripts_path()
    from piab_process_log import upsert_process_log_entry

    folder = working_folder.resolve()
    path = piab_state_path(folder)
    state: dict = {"name": folder.name, "kind": "podcast_in_a_box"}
    if path.is_file():
        try:
            loaded = read_state_file(path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            loaded = None
        if isinstance(loaded, dict):
            state = loaded
    upsert_process_log_entry(folder, state, log_path=log_path or PROCESS_LOG_PATH)


def load_session_state(working_folder: Path) -> dict:
    ensure_scripts_path()
    from piab_lib import load_piab_state

    return load_piab_state(working_folder)


def save_session_state(working_folder: Path, state: dict) -> Path:
    ensure_scripts_path()
    from piab_lib import save_piab_state

    return save_piab_state(working_folder, state)


def read_state_file(state_path: Path) -> dict:
    return json.loads(state_path.read_text(encoding="utf-8"))


def piab_state_path(working_folder: Path) -> Path:
    return working_folder.resolve() / PIAB_STATE_FILENAME


def is_resumable_piab_session(
    working_folder: Path,
    *,
    include_finished: bool = True,
) -> bool:
    """True when a PIAB session can continue (state + Raw/ still on disk)."""
    folder = working_folder.resolve()
    path = piab_state_path(folder)
    if not path.is_file():
        return False
    try:
        state = read_state_file(path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False
    if state.get("kind") != "podcast_in_a_box":
        return False
    if state.get("resume_at") == "cleaned" or state.get("working_files_cleaned_at"):
        return False
    if not include_finished and state.get("resume_at") == "14_done":
        return False
    return (folder / "Raw").is_dir()


def cursor_state_path(working_folder: Path) -> Path:
    return working_folder.resolve() / CURSOR_STATE_FILENAME


def existing_state_conflict(working_folder: Path) -> str | None:
    """
    When a state file exists but is not a resumable PIAB session, return why.

    Returns None if there is no conflict (no file, or valid PIAB session).
    """
    cursor = cursor_state_path(working_folder)
    if cursor.is_file():
        try:
            state = read_state_file(cursor)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return "This folder has a Cursor agent session file that could not be read."
        harness = state.get("harness")
        if harness:
            return (
                f"This folder has a Cursor Inkhaven harness session ({harness}) "
                f"in {CURSOR_STATE_FILENAME}, not a GUI PIAB session."
            )
        if state.get("kind") == "podcast_in_a_box":
            return (
                f"This folder has a Cursor agent PIAB session ({CURSOR_STATE_FILENAME}). "
                "Use the Cursor workflow there, or force a new GUI session here."
            )
        return (
            f"This folder has {CURSOR_STATE_FILENAME} that is not a GUI PIAB session."
        )

    path = piab_state_path(working_folder)
    if not path.is_file():
        return None
    if is_resumable_piab_session(working_folder):
        return None
    try:
        state = read_state_file(path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return "The state file exists but could not be read."
    harness = state.get("harness")
    if harness:
        return (
            f"This folder has an Inkhaven episode harness session ({harness}), "
            "not a Podcast In A Box session."
        )
    if state.get("kind") == "podcast_in_a_box":
        return (
            "Working files were removed from this folder (Clean Old Working Files). "
            "Resume is not available. Start a new session to use the remaining source files."
        )
    return (
        "This folder has a podcast-in-a-box.json file that is not a PIAB session."
    )
