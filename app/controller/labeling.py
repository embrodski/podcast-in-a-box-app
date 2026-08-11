"""Labeling helpers wrapping PIAB preview/apply scripts."""

from __future__ import annotations

import json
import subprocess
import sys
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
) -> dict:
    argv = [
        str(working_folder.resolve()),
        "--video-labels-json",
        json.dumps(video_labels),
        "--audio-labels-json",
        json.dumps(audio_labels),
    ]
    if allow_overwrite:
        argv.append("--allow-overwrite")
    return _run_json_script(SCRIPTS_DIR / "piab_apply_labels.py", argv)


def validate_video_labels(labels: dict[str, str]) -> None:
    ensure_scripts_path()
    from piab_lib import validate_video_labels as _validate

    _validate(labels)


def validate_audio_labels(labels: dict[str, str]) -> None:
    ensure_scripts_path()
    from piab_lib import validate_audio_labels as _validate

    _validate(labels)
