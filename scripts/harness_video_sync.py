"""Video-sync orchestration for inkhaven-episode-harness (video-sync skill rules).

Multicam prep applies two-pass -14 LUFS during re-encode when there are 2+ cameras
(see docs/loudness-normalization.md). Single-camera prep loudnorms after copy.
Anchor *-prepped.wav is extracted from the normalized anchor prepped MP4.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from harness_autocut_common import run_cmd
from harness_episode_lib import (
    BEN_HOST_RE,
    FRONT_RE,
    INTRO_RE,
    RAW_WORD_RE,
    READING_RE,
    REPO_ROOT,
    SIDE_RE,
    VID_RE,
    WIDE_RE,
)
from harness_overwrite_guard import refuse_overwrite


@dataclass
class VideoSyncDirs:
    working_folder: Path
    episode_root: Path
    input_dir: Path
    temp_dir: Path
    sync_dir: Path


def sanitize_raw_stem(stem: str) -> str:
    parts = re.split(r"[\s\-_]+", stem)
    kept = [p for p in parts if p and not RAW_WORD_RE.fullmatch(p)]
    return " ".join(kept) if kept else stem


def resolve_video_sync_dirs(working_folder: Path) -> VideoSyncDirs:
    working_folder = working_folder.resolve()
    wf_raw = working_folder.name.lower() == "raw"
    episode_root = working_folder.parent if wf_raw else working_folder

    if wf_raw:
        input_dir = episode_root / "Input"
        if not input_dir.is_dir():
            input_dir = working_folder / "Input"
        temp_candidates = [episode_root / "Temp", episode_root / "temp"]
        temp_dir = next((p for p in temp_candidates if p.is_dir()), episode_root / "Temp")
    else:
        input_dir = working_folder / "Input"
        if not input_dir.is_dir():
            alt = episode_root / "Input"
            input_dir = alt if alt.is_dir() else working_folder / "Input"
        temp_candidates = [working_folder / "Temp", working_folder / "temp", episode_root / "Temp"]
        temp_dir = next((p for p in temp_candidates if p.is_dir()), working_folder / "Temp")

    input_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    return VideoSyncDirs(
        working_folder=working_folder,
        episode_root=episode_root,
        input_dir=input_dir,
        temp_dir=temp_dir,
        sync_dir=temp_dir,
    )


def synced_basename(video_path: Path) -> str:
    stem = sanitize_raw_stem(video_path.stem)
    return f"{stem}-synced.mp4"


def prepped_basename(synced_name: str) -> str:
    return synced_name.replace("-synced.mp4", "-prepped.mp4")


def prepped_wav_basename(audio_path: Path) -> str:
    stem = sanitize_raw_stem(audio_path.stem)
    return f"{stem}-prepped.wav"


def _list_raw_mp4(raw_dir: Path) -> list[Path]:
    return sorted(
        (p for p in raw_dir.iterdir() if p.is_file() and p.suffix.lower() == ".mp4"),
        key=lambda p: p.name.lower(),
    )


def find_scope_videos(raw_dir: Path, scope: str) -> list[Path]:
    videos = _list_raw_mp4(raw_dir)
    scoped: list[Path] = []
    for path in videos:
        name = path.name
        is_intro = INTRO_RE.search(name) is not None
        is_reading = READING_RE.search(name) is not None
        if scope == "intro":
            if not is_intro or is_reading:
                continue
        elif scope == "reading":
            if not is_reading:
                continue
        else:
            if is_intro or is_reading:
                continue
        if not VID_RE.search(name) and scope != "reading":
            continue
        if scope == "reading" and not (FRONT_RE.search(name) or SIDE_RE.search(name)):
            continue
        scoped.append(path)
    if scope == "intro" or scope == "main":
        ben = [p for p in scoped if BEN_HOST_RE.search(p.name)]
        guest = [p for p in scoped if not BEN_HOST_RE.search(p.name) and not WIDE_RE.search(p.name)]
        wide = [p for p in scoped if WIDE_RE.search(p.name)]
        ordered: list[Path] = []
        if len(ben) != 1 or len(guest) != 1 or len(wide) != 1:
            raise FileNotFoundError(
                f"Expected 1 Ben/Host, 1 Guest, 1 Wide {scope} video in {raw_dir}; "
                f"got ben={len(ben)}, guest={len(guest)}, wide={len(wide)}: "
                f"{[p.name for p in scoped]}"
            )
        ordered.extend([ben[0], guest[0], wide[0]])
        return ordered
    if scope == "reading":
        front = [p for p in scoped if FRONT_RE.search(p.name)]
        side = [p for p in scoped if SIDE_RE.search(p.name)]
        if len(front) != 1 or len(side) != 1:
            raise FileNotFoundError(
                f"Expected 1 front and 1 side reading video; got "
                f"front={len(front)}, side={len(side)}: {[p.name for p in scoped]}"
            )
        return [front[0], side[0]]
    raise ValueError(f"Unknown scope: {scope}")


def run_video_sync(
    raw_dir: Path,
    audio_file: Path,
    videos: list[Path],
    *,
    no_downscale_1080p: bool = False,
    allow_overwrite: bool = False,
) -> dict:
    dirs = resolve_video_sync_dirs(raw_dir)
    synced_paths: list[Path] = []
    predicted_prepped: list[Path] = []
    anchor_wav = dirs.input_dir / prepped_wav_basename(audio_file)

    for video in videos:
        synced_name = synced_basename(video)
        synced_path = dirs.sync_dir / synced_name
        refuse_overwrite(synced_path, allow_overwrite=allow_overwrite)
        predicted_prepped.append(dirs.input_dir / prepped_basename(synced_name))
    refuse_overwrite(anchor_wav, allow_overwrite=allow_overwrite)

    for video in videos:
        synced_name = synced_basename(video)
        synced_path = dirs.sync_dir / synced_name
        report_path = dirs.sync_dir / synced_name.replace(".mp4", ".json")
        run_cmd(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "sync_video_wav_replace.py"),
                str(video.resolve()),
                str(audio_file.resolve()),
                "-o",
                str(synced_path),
                "--json-report",
                str(report_path),
            ]
        )
        synced_paths.append(synced_path)

    for prepped in predicted_prepped:
        refuse_overwrite(prepped, allow_overwrite=allow_overwrite)

    prepped_paths: list[Path] = []
    anchor_prepped: Path | None = None
    anchor_wav: Path | None = None

    if len(videos) >= 2:
        mc_cmd = [
            sys.executable,
            str(REPO_ROOT / "scripts" / "multicam_align_trim.py"),
            "--prepped-names",
            "--out-dir",
            str(dirs.input_dir),
            "--json-report",
            str(dirs.temp_dir / "video-sync-multicam.json"),
            *[str(p) for p in synced_paths],
        ]
        if no_downscale_1080p:
            mc_cmd.append("--no-downscale-1080p")
        run_cmd(mc_cmd)
        for synced in synced_paths:
            prepped = dirs.input_dir / prepped_basename(synced.name)
            if not prepped.is_file():
                raise FileNotFoundError(f"Missing multicam output: {prepped}")
            prepped_paths.append(prepped)
        anchor_prepped = dirs.input_dir / prepped_basename(synced_paths[0].name)
    else:
        for synced in synced_paths:
            dest = dirs.input_dir / synced.name
            if synced != dest:
                import shutil

                shutil.copy2(synced, dest)
            prepped_paths.append(dest)
        anchor_prepped = prepped_paths[0]

        from harness_loudnorm import normalize_prepped_outputs

        normalize_prepped_outputs(prepped_paths)

    wav_out = dirs.input_dir / prepped_wav_basename(audio_file)
    run_cmd(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "extract_mp4_audio_wav.py"),
            str(anchor_prepped),
            str(wav_out),
        ]
    )
    anchor_wav = wav_out

    return {
        "input_dir": str(dirs.input_dir),
        "temp_dir": str(dirs.temp_dir),
        "synced": [str(p) for p in synced_paths],
        "prepped_videos": [str(p) for p in prepped_paths],
        "prepped_audio_wav": str(anchor_wav),
        "loudnorm_target_lufs": -14.0,
    }
