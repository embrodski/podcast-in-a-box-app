"""Labeling helpers wrapping PIAB preview/apply scripts."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from app.controller.paths import REPO_ROOT, SCRIPTS_DIR, ensure_scripts_path


def _run_json_script(script: Path, args: list[str]) -> dict:
    proc = subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        message = f"{script.name} failed with code {proc.returncode}"
        if detail:
            message = f"{message}:\n{detail}"
        raise RuntimeError(message)
    stdout = (proc.stdout or "").strip()
    if not stdout:
        return {}
    return json.loads(stdout)


def extract_video_previews(working_folder: Path) -> dict:
    return _run_json_script(
        SCRIPTS_DIR / "piab_extract_video_previews.py",
        [str(working_folder.resolve())],
    )


def extract_audio_previews(working_folder: Path) -> dict:
    return _run_json_script(
        SCRIPTS_DIR / "piab_extract_audio_previews.py",
        [str(working_folder.resolve())],
    )


def apply_labels(
    working_folder: Path,
    *,
    video_labels: dict[str, str],
    audio_labels: dict[str, str],
    allow_overwrite: bool = False,
    on_copy: Callable[[Path, Path, int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict:
    ensure_scripts_path()
    from harness_overwrite_guard import HarnessOverwriteError
    from piab_apply_labels import apply_labeled_media_session

    try:
        return apply_labeled_media_session(
            working_folder,
            video_labels=video_labels,
            audio_labels=audio_labels,
            allow_overwrite=allow_overwrite,
            on_copy=on_copy,
            should_cancel=should_cancel,
        )
    except HarnessOverwriteError as exc:
        raise RuntimeError(str(exc)) from exc


def validate_video_labels(labels: dict[str, str]) -> None:
    ensure_scripts_path()
    from piab_lib import validate_video_labels as _validate

    _validate(labels)


def validate_audio_labels(labels: dict[str, str]) -> None:
    ensure_scripts_path()
    from piab_lib import validate_audio_labels as _validate

    _validate(labels)
