"""1-minute test review and approval helpers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.controller.paths import REPO_ROOT, SCRIPTS_DIR, ensure_scripts_path


def resolve_one_min_test_path(state: dict, working_folder: Path) -> Path:
    ensure_scripts_path()
    from piab_fast_preview_lib import resolve_preview_one_min_path
    from app.controller.fast_preview import fast_preview_review_pending

    if fast_preview_review_pending(state):
        return resolve_preview_one_min_path(state, working_folder)

    raw = state.get("podcast_autocut_test_mp4")
    if raw:
        path = Path(str(raw))
        if path.is_file():
            return path.resolve()
    fallback = working_folder.resolve() / "Output" / "1 Min Test.mp4"
    if fallback.is_file():
        return fallback
    raise FileNotFoundError(
        f"1 Min Test.mp4 not found under {working_folder / 'Output'}"
    )


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


def approve_one_min_test(working_folder: Path) -> dict:
    """Mark 1-min approval complete and refresh Estimate B in session state."""
    folder = working_folder.resolve()
    ensure_scripts_path()
    from app.controller.fast_preview import fast_preview_review_pending
    from piab_lib import load_piab_state, mark_step, save_piab_state

    state = load_piab_state(folder)
    if fast_preview_review_pending(state):
        from app.controller.fast_preview import approve_fast_preview as _approve_fp

        return _approve_fp(folder)

    mark_step(
        state,
        "11_one_min_approval",
        title="1-min test approval",
        status="completed",
    )
    save_piab_state(folder, state)

    script = SCRIPTS_DIR / "piab_estimate.py"
    _run_script(script, [str(folder), "--which", "full", "--mark-awaiting"])
    return load_piab_state(folder)


def fix_audio_speaker_swap(working_folder: Path, *, allow_overwrite: bool = False) -> dict:
    """Toggle speaker IDs and re-render 1-min test; Raw/Input unchanged."""
    folder = working_folder.resolve()
    script = SCRIPTS_DIR / "piab_fix_audio_speaker_swap.py"
    argv = [str(folder)]
    if allow_overwrite:
        argv.append("--allow-overwrite")
    _run_script(script, argv)
    ensure_scripts_path()
    from piab_lib import load_piab_state

    return load_piab_state(folder)


def swap_speaker_ids_toggle(working_folder: Path) -> dict:
    folder = working_folder.resolve()
    script = SCRIPTS_DIR / "piab_swap.py"
    _run_script(script, [str(folder), "--speaker-ids", "toggle"])
    ensure_scripts_path()
    from piab_lib import load_piab_state

    return load_piab_state(folder)


def swap_labeled_files(working_folder: Path, *, kind: str) -> dict:
    if kind not in {"video", "audio", "both"}:
        raise ValueError(f"Unknown swap kind: {kind!r}")
    folder = working_folder.resolve()
    script = SCRIPTS_DIR / "piab_swap.py"
    _run_script(script, [str(folder), "--files", kind])
    ensure_scripts_path()
    from piab_lib import load_piab_state

    return load_piab_state(folder)


def rerun_one_min_test(working_folder: Path, *, allow_overwrite: bool = False) -> dict:
    folder = working_folder.resolve()
    script = SCRIPTS_DIR / "piab_rerun_one_min.py"
    argv = [str(folder)]
    if allow_overwrite:
        argv.append("--allow-overwrite")
    _run_script(script, argv)
    ensure_scripts_path()
    from piab_lib import load_piab_state

    return load_piab_state(folder)
