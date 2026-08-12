"""Fast Preview controller helpers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.controller.paths import REPO_ROOT, SCRIPTS_DIR, ensure_scripts_path


def fast_preview_eligible_for_state(state: dict) -> bool:
    ensure_scripts_path()
    from piab_fast_preview_lib import fast_preview_eligible

    raw = Path(str((state.get("paths") or {}).get("raw") or ""))
    if not raw.is_dir():
        return False
    try:
        return fast_preview_eligible(raw)
    except FileNotFoundError:
        return False


def fast_preview_review_pending(state: dict) -> bool:
    if state.get("fast_preview_approval", {}).get("approved_at"):
        return False
    if state.get("resume_at") in ("10a_sync_offset_approval", "11_one_min_approval"):
        step = (state.get("steps") or {}).get("10p_fast_preview_one_min") or {}
        if step.get("status") == "completed":
            return True
        if (state.get("fast_preview") or {}).get("enabled"):
            return True
    return False


def full_after_preview_pending(state: dict) -> bool:
    approval = state.get("fast_preview_approval") or {}
    if not approval.get("approved_at"):
        return False
    resume_at = state.get("resume_at")
    if resume_at in ("13_full_prep_after_preview", "13_full_render"):
        return True
    render_step = (state.get("steps") or {}).get("13_full_render") or {}
    return render_step.get("status") != "completed"


def should_start_fast_preview(state: dict) -> bool:
    if state.get("fast_preview_approval", {}).get("approved_at"):
        return False
    if (state.get("steps") or {}).get("10p_fast_preview_one_min", {}).get("status") == "completed":
        return False
    return fast_preview_eligible_for_state(state)


def _run_script(script: Path, argv: list[str]) -> None:
    proc = subprocess.run(
        [sys.executable, str(script), *argv],
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


def approve_fast_preview(working_folder: Path) -> dict:
    folder = working_folder.resolve()
    _run_script(SCRIPTS_DIR / "piab_approve_fast_preview.py", [str(folder)])
    ensure_scripts_path()
    from piab_lib import load_piab_state

    return load_piab_state(folder)


def clear_preview_for_relabel(working_folder: Path) -> dict:
    folder = working_folder.resolve()
    ensure_scripts_path()
    from piab_fast_preview_lib import clear_preview_sandbox
    from piab_lib import load_piab_state, save_piab_state

    state = load_piab_state(folder)
    clear_preview_sandbox(state, folder)
    save_piab_state(folder, state)
    return state
