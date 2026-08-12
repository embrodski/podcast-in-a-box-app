"""A/V sync offset A/B choice helpers (step 10a)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.controller.paths import REPO_ROOT, SCRIPTS_DIR, ensure_scripts_path


def needs_sync_offset_choice(state: dict) -> bool:
    resume_at = state.get("resume_at")
    if resume_at == "10a_sync_offset_approval":
        return True
    if state.get("sync_offset_choice_pending"):
        return True
    step = state.get("steps", {}).get("10a_sync_offset_approval", {})
    if isinstance(step, dict) and step.get("status") == "awaiting_user":
        return True
    return False


def resolve_ab_test_paths(state: dict, working_folder: Path) -> tuple[Path, Path]:
    """Return (no_offset_mp4, forced_offset_mp4)."""
    folder = working_folder.resolve()
    ensure_scripts_path()
    from app.controller.fast_preview import fast_preview_review_pending
    from harness_av_sync_lib import ONE_MIN_FORCED_OFFSET, ONE_MIN_NO_OFFSET
    from piab_fast_preview_lib import (
        PREVIEW_ONE_MIN_FORCED_OFFSET,
        PREVIEW_ONE_MIN_NO_OFFSET,
        preview_root,
    )

    if fast_preview_review_pending(state):
        output = preview_root(folder) / "Output"
        default_no = PREVIEW_ONE_MIN_NO_OFFSET
        default_forced = PREVIEW_ONE_MIN_FORCED_OFFSET
    else:
        output = folder / "Output"
        default_no = ONE_MIN_NO_OFFSET
        default_forced = ONE_MIN_FORCED_OFFSET

    no_raw = state.get("podcast_autocut_test_mp4_no_offset")
    forced_raw = state.get("podcast_autocut_test_mp4_forced_offset")
    step = state.get("steps", {}).get("10a_sync_offset_approval", {})
    if isinstance(step, dict):
        if not no_raw and step.get("one_min_no_offset"):
            no_raw = step["one_min_no_offset"]
        if not forced_raw and step.get("one_min_forced_offset"):
            forced_raw = step["one_min_forced_offset"]

    no_path = Path(str(no_raw)) if no_raw else output / default_no
    forced_path = Path(str(forced_raw)) if forced_raw else output / default_forced
    if not no_path.is_file():
        raise FileNotFoundError(f"Missing sync A/B preview: {no_path}")
    if not forced_path.is_file():
        raise FileNotFoundError(f"Missing sync A/B preview: {forced_path}")
    return no_path.resolve(), forced_path.resolve()


def record_sync_offset_choice(working_folder: Path, choice: str) -> dict:
    if choice not in {"start_aligned", "forced_offset"}:
        raise ValueError(f"Invalid sync offset choice: {choice!r}")
    folder = working_folder.resolve()
    script = SCRIPTS_DIR / "piab_record_sync_offset_choice.py"
    proc = subprocess.run(
        [sys.executable, str(script), str(folder), "--choice", choice],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"{script.name} failed (code {proc.returncode})"
            + (f":\n{detail}" if detail else "")
        )
    ensure_scripts_path()
    from piab_lib import load_piab_state

    return load_piab_state(folder)
