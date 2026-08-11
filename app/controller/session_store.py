"""Session folder helpers and podcast-in-a-box.json access."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.controller.paths import DEFAULT_SCAN_ROOT, ensure_scripts_path

PIAB_STATE_FILENAME = "podcast-in-a-box.json"
CURSOR_STATE_FILENAME = "cursor-podcast-in-a-box.json"


def generate_session_name(
    root: Path = DEFAULT_SCAN_ROOT,
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


def list_recent_sessions(
    root: Path = DEFAULT_SCAN_ROOT,
    *,
    limit: int = 20,
) -> list[Path]:
    if not root.is_dir():
        return []

    candidates: list[tuple[float, Path]] = []
    for state_path in root.glob(f"*/{PIAB_STATE_FILENAME}"):
        folder = state_path.parent.resolve()
        if not is_resumable_piab_session(folder):
            continue
        try:
            mtime = state_path.stat().st_mtime
        except OSError:
            continue
        candidates.append((mtime, state_path.parent.resolve()))

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


def is_resumable_piab_session(working_folder: Path) -> bool:
    """True when podcast-in-a-box.json exists and is a PIAB session (not harness-only)."""
    path = piab_state_path(working_folder)
    if not path.is_file():
        return False
    try:
        state = read_state_file(path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False
    return state.get("kind") == "podcast_in_a_box"


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
    return (
        "This folder has a podcast-in-a-box.json file that is not a PIAB session."
    )
