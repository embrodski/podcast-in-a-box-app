"""Shared render helpers for harness reading / podcast autocut."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from episode_segments import podcast_dsl_segments_args, segments_path
from harness_episode_lib import REPO_ROOT
from harness_overwrite_guard import refuse_overwrite
from win_hidden_console import install_hidden_console

install_hidden_console()


def run_cmd(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None) -> None:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )


def temp_env(temp_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["TEMP"] = str(temp_dir)
    env["TMP"] = str(temp_dir)
    seg = segments_path(temp_dir)
    if seg.is_file():
        env["PODCAST_DSL_SEGMENTS_FILE"] = str(seg)
    return env


def render_dsl(
    dsl_path: Path,
    output_mp4: Path,
    temp_dir: Path,
    *,
    max_seconds: int | None = None,
    workers: int = 6,
    allow_overwrite: bool = False,
) -> None:
    refuse_overwrite(output_mp4, allow_overwrite=allow_overwrite)
    cmd = [
        sys.executable,
        "-m",
        "podcast_dsl",
        str(dsl_path),
        "-o",
        str(output_mp4),
        "--workers",
        str(workers),
    ]
    if max_seconds is not None:
        cmd.extend(["--max-seconds", str(max_seconds)])
    cmd.extend(podcast_dsl_segments_args(temp_dir))
    run_cmd(cmd, cwd=REPO_ROOT / "src", env=temp_env(temp_dir))
