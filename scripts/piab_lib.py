"""Shared helpers for Lighthaven Podcast In A Box."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import wave
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np

from harness_episode_lib import (
    PIAB_STATE_FILENAME,
    SUBFOLDERS,
    load_episode_state,
    save_episode_state,
    step_state,
    utc_now_iso,
)

DEFAULT_SCAN_ROOT = Path(r"E:\PodcastRoom")

VIDEO_NAME_RE = re.compile(
    r"^MultiCorder\d+\s*-\s*DeckLink",
    re.IGNORECASE,
)
AUDIO_NAME_RE = re.compile(
    r"^MultiCorder\d+\s*-\s*Output\s+\d+",
    re.IGNORECASE,
)

HOST_RAW_VIDEO = "Host Raw Video.mp4"
GUEST_RAW_VIDEO = "Guest Raw Video.mp4"
WIDE_RAW_VIDEO = "Wide Raw Video.mp4"
HOST_RAW_AUDIO = "Host Raw Audio.wav"
GUEST_RAW_AUDIO = "Guest Raw Audio.wav"

VIDEO_ROLES = ("host", "guest", "wide", "do_not_use")
AUDIO_ROLES = ("host", "guest", "do_not_use")

# Coarse realtime multipliers for Estimate A/B (wall-clock vs source duration).
EST_CONVERSATION_SYNC_X = 0.08
EST_VIDEO_SYNC_X = 2.5  # three cameras + multicam re-encode
EST_TRANSCRIBE_X = 0.15
EST_ONE_MIN_RENDER_SEC = 12 * 60
# Parallel podcast_dsl cut/assemble from prepped media. BayesVishal (2026-07-16)
# finished at ~0.41×; 0.5× keeps a small cushion on this machine.
EST_FULL_RENDER_X = 0.5
EST_PAD_FRACTION = 0.25  # widen into a range

DEFAULT_PREVIEW_CLIP_SEC = 5.0
DEFAULT_PREVIEW_SECTIONS = (0.25, 0.62)
MIN_PREVIEW_CLIP_SEPARATION_SEC = 10.0
MIN_AUDIBLE_RMS = 0.008


@dataclass
class MediaInfo:
    path: str
    name: str
    kind: str  # "video" | "audio"
    mtime: float
    mtime_iso: str
    duration_sec: float

    @property
    def path_obj(self) -> Path:
        return Path(self.path)


def ffprobe_duration(path: Path) -> float:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}:\n{proc.stderr.strip()}")
    text = (proc.stdout or "").strip()
    if not text:
        raise RuntimeError(f"ffprobe returned no duration for {path}")
    return float(text)


def classify_multicorder(path: Path) -> str | None:
    if not path.is_file():
        return None
    suffix = path.suffix.lower()
    if suffix == ".mp4" and VIDEO_NAME_RE.search(path.name):
        return "video"
    if suffix == ".wav" and AUDIO_NAME_RE.search(path.name):
        return "audio"
    return None


def list_top_level_multicorder(
    root: Path,
    *,
    skipped: list[dict] | None = None,
) -> list[MediaInfo]:
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Scan root not found: {root}")
    out: list[MediaInfo] = []
    for path in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        kind = classify_multicorder(path)
        if kind is None:
            continue
        size = path.stat().st_size
        if size <= 0:
            if skipped is not None:
                skipped.append(
                    {
                        "path": str(path),
                        "name": path.name,
                        "kind": kind,
                        "reason": "empty file (0 bytes)",
                    }
                )
            continue
        try:
            duration = ffprobe_duration(path)
        except RuntimeError as exc:
            if skipped is not None:
                skipped.append(
                    {
                        "path": str(path),
                        "name": path.name,
                        "kind": kind,
                        "reason": str(exc).split("\n", 1)[0],
                    }
                )
            continue
        mtime = path.stat().st_mtime
        out.append(
            MediaInfo(
                path=str(path),
                name=path.name,
                kind=kind,
                mtime=mtime,
                mtime_iso=datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
                duration_sec=round(duration, 3),
            )
        )
    return out


def _grow_cluster_from_seed(
    seed: MediaInfo,
    candidates: list[MediaInfo],
    *,
    mtime_tol_sec: float,
) -> list[MediaInfo]:
    cluster = [seed]
    changed = True
    while changed:
        changed = False
        members = {f.path for f in cluster}
        for cand in candidates:
            if cand.path in members:
                continue
            if any(abs(cand.mtime - m.mtime) <= mtime_tol_sec for m in cluster):
                cluster.append(cand)
                changed = True
    return cluster


def _finalize_session_cluster(
    cluster: list[MediaInfo],
    *,
    duration_tol_sec: float,
) -> list[MediaInfo]:
    durations = sorted(f.duration_sec for f in cluster)
    median = durations[len(durations) // 2]
    filtered = [f for f in cluster if abs(f.duration_sec - median) <= duration_tol_sec]
    if not filtered:
        raise RuntimeError(
            "Session cluster found by mtime but no files share duration within "
            f"{duration_tol_sec}s of median {median:.3f}s."
        )
    videos = [f for f in filtered if f.kind == "video"]
    audios = [f for f in filtered if f.kind == "audio"]
    if not videos or not audios:
        raise RuntimeError(
            "Session cluster did not contain both MultiCorder videos and audio WAVs "
            f"after duration filter (videos={len(videos)}, audios={len(audios)})."
        )
    return sorted(filtered, key=lambda f: (f.kind, f.name.lower()))


def discover_all_clusters(
    files: list[MediaInfo],
    *,
    mtime_tol_sec: float = 60.0,
    duration_tol_sec: float = 2.0,
) -> list[list[MediaInfo]]:
    """Return all disjoint session clusters, newest first."""
    remaining = list(files)
    clusters: list[list[MediaInfo]] = []
    while remaining:
        by_mtime = sorted(remaining, key=lambda f: f.mtime, reverse=True)
        seed = by_mtime[0]
        try:
            grown = _grow_cluster_from_seed(seed, remaining, mtime_tol_sec=mtime_tol_sec)
            cluster = _finalize_session_cluster(
                grown,
                duration_tol_sec=duration_tol_sec,
            )
        except RuntimeError:
            remaining = [f for f in remaining if f.path != seed.path]
            continue
        clusters.append(cluster)
        cluster_paths = {f.path for f in cluster}
        remaining = [f for f in remaining if f.path not in cluster_paths]
    clusters.sort(key=lambda c: max(f.mtime for f in c), reverse=True)
    return clusters


def cluster_session_files(
    files: list[MediaInfo],
    *,
    mtime_tol_sec: float = 60.0,
    duration_tol_sec: float = 2.0,
) -> list[MediaInfo]:
    """Return the most recent session cluster."""
    if not files:
        raise FileNotFoundError("No MultiCorder video/audio files found.")
    clusters = discover_all_clusters(
        files,
        mtime_tol_sec=mtime_tol_sec,
        duration_tol_sec=duration_tol_sec,
    )
    if not clusters:
        raise FileNotFoundError("No MultiCorder video/audio session cluster found.")
    return clusters[0]


def _cluster_to_summary(cluster: list[MediaInfo]) -> dict:
    videos = [f for f in cluster if f.kind == "video"]
    audios = [f for f in cluster if f.kind == "audio"]
    durations = [f.duration_sec for f in cluster]
    median_duration = sorted(durations)[len(durations) // 2]
    newest = sorted(cluster, key=lambda f: f.mtime)[-1]
    return {
        "file_count": len(cluster),
        "video_count": len(videos),
        "audio_count": len(audios),
        "typical_duration_sec": median_duration,
        "typical_duration_human": format_duration(median_duration),
        "typical_mtime_iso": newest.mtime_iso,
    }


def _files_to_payload(cluster: list[MediaInfo]) -> list[dict]:
    return [
        {
            "path": f.path,
            "name": f.name,
            "kind": f.kind,
            "mtime_iso": f.mtime_iso,
            "duration_sec": f.duration_sec,
            "duration_human": format_duration(f.duration_sec),
        }
        for f in cluster
    ]


MIN_PIAB_VIDEOS = 3
MIN_PIAB_AUDIOS = 2


def resolve_scan_dir(*, root: Path, working: Path) -> Path:
    """
    Pick where to scan for MultiCorder files in default mode.

    Prefer ``working`` when it already contains sources (special-style layout);
    otherwise use ``root`` (fresh subfolder under the dump directory).
    """
    root = root.resolve()
    working = working.resolve()
    if working.is_dir() and list_top_level_multicorder(working):
        return working
    if list_top_level_multicorder(root):
        return root
    raise FileNotFoundError(
        "No MultiCorder video/audio files found in "
        f"working folder {working} or scan root {root}."
    )


def resolve_init_layout(
    *,
    mode: str | None,
    root: Path,
    name: str | None,
    working_folder: Path | None,
) -> tuple[Path, Path, str, str]:
    """
    Return ``(working_folder, scan_dir, session_name, session_mode)``.

    ``session_mode`` is ``default`` (new subfolder under ``root``) or ``special``
    (sources live inside the given working folder).
    """
    root = root.resolve()

    if working_folder is not None:
        if name is not None:
            raise ValueError("Use --working-folder or --name, not both.")
        working = working_folder.resolve()
        if not working.is_dir():
            raise FileNotFoundError(f"Working folder not found: {working}")
        return working, working, working.name, "special"

    if mode == "special":
        raise ValueError("--mode special requires --working-folder.")

    if not name or not name.strip():
        raise ValueError("--name is required in default mode.")

    session_name = name.strip()
    working = (root / session_name).resolve()
    if working.parent != root:
        raise ValueError("--name must be a single folder name, not a path.")
    scan_dir = resolve_scan_dir(root=root, working=working)
    return working, scan_dir, session_name, "default"


def assess_session_requirements(
    cluster: list[MediaInfo],
    *,
    scan_dir: Path,
    classified_files: list[MediaInfo] | None = None,
    skipped: list[dict] | None = None,
) -> dict:
    """Return requirement status and human-readable gaps for PIAB labeling."""
    videos = [f for f in cluster if f.kind == "video"]
    audios = [f for f in cluster if f.kind == "audio"]
    missing: list[str] = []
    if len(videos) < MIN_PIAB_VIDEOS:
        missing.append(
            f"Need at least {MIN_PIAB_VIDEOS} MultiCorder camera videos "
            f"(found {len(videos)})."
        )
    if len(audios) < MIN_PIAB_AUDIOS:
        missing.append(
            f"Need at least {MIN_PIAB_AUDIOS} MultiCorder Output WAV files "
            f"(found {len(audios)})."
        )

    cluster_paths = {f.path for f in cluster}
    other_session: list[str] = []
    if classified_files:
        for item in classified_files:
            if item.path not in cluster_paths:
                other_session.append(item.name)

    unrecognized: list[str] = []
    for path in sorted(scan_dir.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file():
            continue
        if classify_multicorder(path) is not None:
            continue
        if path.name.lower().endswith((".mp4", ".wav", ".m4a")):
            unrecognized.append(path.name)

    return {
        "ok": not missing,
        "missing": missing,
        "warnings": [
            f"Skipped unreadable MultiCorder file: {item['name']} ({item['reason']})"
            for item in (skipped or [])
        ],
        "other_session_files": sorted(other_session, key=str.lower),
        "unrecognized_media": unrecognized,
    }


def collect_session_scan(
    scan_dir: Path,
    *,
    mtime_tol_sec: float = 60.0,
    duration_tol_sec: float = 2.0,
    date_filter: date | None = None,
    cluster_index: int = 0,
) -> dict:
    """Scan ``scan_dir`` and return the JSON payload used by PIAB scan/init."""
    scan_dir = scan_dir.resolve()
    skipped: list[dict] = []
    files = list_top_level_multicorder(scan_dir, skipped=skipped)
    if date_filter:
        files = [
            item
            for item in files
            if date.fromisoformat(item.mtime_iso[:10]) == date_filter
        ]
    clusters = discover_all_clusters(
        files,
        mtime_tol_sec=mtime_tol_sec,
        duration_tol_sec=duration_tol_sec,
    )
    if not clusters:
        raise FileNotFoundError("No MultiCorder video/audio session cluster found.")
    if cluster_index < 0 or cluster_index >= len(clusters):
        raise ValueError(
            f"cluster_index {cluster_index} out of range (found {len(clusters)} clusters)."
        )

    cluster = clusters[cluster_index]
    videos = [f for f in cluster if f.kind == "video"]
    audios = [f for f in cluster if f.kind == "audio"]
    durations = [f.duration_sec for f in cluster]
    mtimes = [f.mtime for f in cluster]
    requirements = assess_session_requirements(
        cluster,
        scan_dir=scan_dir,
        classified_files=files,
        skipped=skipped,
    )
    cluster_options = []
    for index, candidate in enumerate(clusters):
        summary = _cluster_to_summary(candidate)
        summary["index"] = index
        cluster_options.append(summary)

    return {
        "scan_root": str(scan_dir),
        "date_filter": date_filter.isoformat() if date_filter else None,
        "cluster_index": cluster_index,
        "cluster_count": len(clusters),
        "clusters": cluster_options,
        "file_count": len(cluster),
        "video_count": len(videos),
        "audio_count": len(audios),
        "mtime_span_sec": round(max(mtimes) - min(mtimes), 3),
        "duration_span_sec": round(max(durations) - min(durations), 3),
        "typical_duration_sec": sorted(durations)[len(durations) // 2],
        "typical_duration_human": format_duration(sorted(durations)[len(durations) // 2]),
        "typical_mtime_iso": sorted(cluster, key=lambda f: f.mtime)[-1].mtime_iso,
        "skipped_unreadable": skipped,
        "requirements": requirements,
        "files": _files_to_payload(cluster),
    }


def format_duration(seconds: float) -> str:
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_eta_range(center_sec: float) -> dict:
    low = max(60.0, center_sec * (1.0 - EST_PAD_FRACTION))
    high = center_sec * (1.0 + EST_PAD_FRACTION)
    return {
        "center_sec": int(round(center_sec)),
        "low_sec": int(round(low)),
        "high_sec": int(round(high)),
        "center_human": format_duration(center_sec),
        "low_human": format_duration(low),
        "high_human": format_duration(high),
        "summary": f"about {format_duration(low)}–{format_duration(high)}",
    }


def estimate_prep_through_one_min(source_duration_sec: float) -> dict:
    center = (
        source_duration_sec * EST_CONVERSATION_SYNC_X
        + source_duration_sec * EST_VIDEO_SYNC_X
        + source_duration_sec * EST_TRANSCRIBE_X
        + EST_ONE_MIN_RENDER_SEC
    )
    detail = {
        "source_duration_sec": source_duration_sec,
        "source_duration_human": format_duration(source_duration_sec),
        "conversation_sync_sec": int(source_duration_sec * EST_CONVERSATION_SYNC_X),
        "video_sync_sec": int(source_duration_sec * EST_VIDEO_SYNC_X),
        "transcribe_sec": int(source_duration_sec * EST_TRANSCRIBE_X),
        "one_min_render_sec": EST_ONE_MIN_RENDER_SEC,
    }
    return {**format_eta_range(center), "breakdown": detail}


def estimate_full_render(source_duration_sec: float) -> dict:
    center = source_duration_sec * EST_FULL_RENDER_X
    return {
        **format_eta_range(center),
        "breakdown": {
            "source_duration_sec": source_duration_sec,
            "source_duration_human": format_duration(source_duration_sec),
            "full_render_x": EST_FULL_RENDER_X,
        },
    }


def new_piab_state(
    working_folder: Path,
    *,
    name: str,
    scan_root: Path,
    session_files: list[MediaInfo],
    session_mode: str = "default",
) -> dict:
    working_folder = working_folder.resolve()
    now = utc_now_iso()
    median_dur = sorted(f.duration_sec for f in session_files)[len(session_files) // 2]
    return {
        "kind": "podcast_in_a_box",
        "name": name,
        "session_mode": session_mode,
        "created_at": now,
        "updated_at": now,
        "skip_reading": True,
        "swap_speaker_ids": False,
        "scan_root": str(scan_root.resolve()),
        "source_duration_sec": median_dur,
        "session_files": [asdict(f) for f in session_files],
        "paths": {
            "episode_folder": str(working_folder),
            "raw": str(working_folder / "Raw"),
            "input": str(working_folder / "Input"),
            "output": str(working_folder / "Output"),
            "temp": str(working_folder / "Temp"),
            "previews": str(working_folder / "Temp" / "piab-previews"),
            "state": str(working_folder / PIAB_STATE_FILENAME),
        },
        "labels": {"videos": {}, "audios": {}},
        "original_paths": {},
        "resume_at": "03_label_videos",
        "steps": {},
    }


def ensure_subfolders(working_folder: Path) -> None:
    for sub in SUBFOLDERS:
        (working_folder / sub).mkdir(parents=True, exist_ok=True)
    (working_folder / "Temp" / "piab-previews").mkdir(parents=True, exist_ok=True)


def load_piab_state(working_folder: Path) -> dict:
    state = load_episode_state(working_folder)
    if state.get("kind") != "podcast_in_a_box":
        raise ValueError(
            f"{working_folder} is not a Podcast In A Box session "
            f"(expected {PIAB_STATE_FILENAME} with kind=podcast_in_a_box)."
        )
    return state


def save_piab_state(working_folder: Path, state: dict) -> Path:
    state["kind"] = "podcast_in_a_box"
    return save_episode_state(working_folder, state)


def mark_step(
    state: dict,
    step_id: str,
    *,
    title: str,
    status: str,
    **extra: object,
) -> None:
    steps = state.setdefault("steps", {})
    steps[step_id] = step_state(steps, step_id, title=title, status=status, **extra)


def mark_piab_sync_ab_steps(state: dict, *, ab_result: dict) -> None:
    """Gate on A/B sync offset choice before general 1-min approval."""
    mark_step(
        state,
        "10a_sync_offset_approval",
        title="Sync offset A/B approval",
        status="awaiting_user",
        one_min_no_offset=ab_result["one_min_no_offset"],
        one_min_forced_offset=ab_result["one_min_forced_offset"],
    )
    mark_step(
        state,
        "11_one_min_approval",
        title="1-min test approval",
        status="pending",
        note="Complete sync offset choice (step 10a) first.",
    )
    state["resume_at"] = "10a_sync_offset_approval"


def mark_piab_sync_choice_completed(state: dict) -> None:
    mark_step(
        state,
        "10a_sync_offset_approval",
        title="Sync offset A/B approval",
        status="completed",
        choice=state.get("sync_offset_choice"),
    )
    mark_step(
        state,
        "11_one_min_approval",
        title="1-min test approval",
        status="awaiting_user",
        chosen_test=state.get("podcast_autocut_test_mp4"),
    )
    state["resume_at"] = "11_one_min_approval"


def extract_midpoint_frame(video: Path, out_jpg: Path) -> Path:
    duration = ffprobe_duration(video)
    mid = max(0.0, duration / 2.0)
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{mid:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(out_jpg),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not out_jpg.is_file():
        raise RuntimeError(
            f"ffmpeg frame extract failed for {video}:\n{proc.stderr.strip()}"
        )
    return out_jpg


def _wav_mono_float(path: Path) -> tuple[np.ndarray, int]:
    """Load audio as mono float32 via ffmpeg (handles non-PCM MultiCorder WAVs)."""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(path),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "wav",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg decode failed for loudness scan of {path}:\n{proc.stderr.strip()}"
            )
        with wave.open(str(tmp_path), "rb") as wf:
            rate = wf.getframerate()
            n_frames = wf.getnframes()
            sampwidth = wf.getsampwidth()
            raw = wf.readframes(n_frames)
        if sampwidth == 2:
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        else:
            raise RuntimeError(f"Unexpected decoded sample width {sampwidth} for {path}")
        return data, rate
    finally:
        tmp_path.unlink(missing_ok=True)


def _max_window_rms(audio: np.ndarray, rate: int, *, window_sec: float = 0.5) -> float:
    win = max(1, int(window_sec * rate))
    hop = win // 2 or 1
    best = 0.0
    for i in range(0, max(1, len(audio) - win + 1), hop):
        seg = audio[i : i + win]
        if len(seg) < win // 2:
            break
        rms = float(np.sqrt(np.mean(np.square(seg))))
        best = max(best, rms)
    return best


def wav_has_audible_content(path: Path, *, min_rms: float = MIN_AUDIBLE_RMS) -> bool:
    """Return False when the file appears silent (no usable speech/noise for labeling)."""
    audio, rate = _wav_mono_float(path)
    if len(audio) < rate // 10:
        return False
    return _max_window_rms(audio, rate) >= min_rms


def find_loud_clip_start(
    path: Path,
    *,
    clip_sec: float = DEFAULT_PREVIEW_CLIP_SEC,
    after_fraction: float = 0.25,
    window_sec: float = 0.5,
) -> float:
    """Return start time (sec) of a loud clip_sec window after after_fraction of the file."""
    audio, rate = _wav_mono_float(path)
    n = len(audio)
    if n < rate:
        return 0.0
    start_idx = int(n * after_fraction)
    clip_samples = int(clip_sec * rate)
    win = max(1, int(window_sec * rate))
    if start_idx + clip_samples >= n:
        return max(0.0, (n - clip_samples) / rate)

    search = audio[start_idx:]
    # RMS over hopping windows; pick loudest window whose clip fits.
    best_local = 0
    best_rms = -1.0
    hop = win // 2 or 1
    max_local = len(search) - clip_samples
    for i in range(0, max(1, max_local + 1), hop):
        seg = search[i : i + win]
        if len(seg) < win // 2:
            break
        rms = float(np.sqrt(np.mean(np.square(seg))))
        if rms > best_rms:
            best_rms = rms
            best_local = i
    # Center-ish: start a bit before the loud window so speech is in the clip.
    start = start_idx + best_local
    start = max(start_idx, min(start, n - clip_samples))
    return start / rate


def find_loud_clip_start_before(
    path: Path,
    max_start_sec: float,
    *,
    clip_sec: float = DEFAULT_PREVIEW_CLIP_SEC,
    window_sec: float = 0.5,
) -> float:
    """Return start time (sec) of a loud clip window at or before ``max_start_sec``."""
    audio, rate = _wav_mono_float(path)
    n = len(audio)
    clip_samples = int(clip_sec * rate)
    if n < clip_samples:
        return 0.0

    max_start_idx = int(max_start_sec * rate)
    max_start_idx = max(0, min(max_start_idx, n - clip_samples))
    search = audio[: max_start_idx + clip_samples]
    if len(search) <= clip_samples:
        return 0.0

    win = max(1, int(window_sec * rate))
    best_local = 0
    best_rms = -1.0
    hop = win // 2 or 1
    max_local = min(max_start_idx, len(search) - clip_samples)
    for i in range(0, max(1, max_local + 1), hop):
        seg = search[i : i + win]
        if len(seg) < win // 2:
            break
        rms = float(np.sqrt(np.mean(np.square(seg))))
        if rms > best_rms:
            best_rms = rms
            best_local = i
    start = best_local
    start = max(0, min(start, max_start_idx))
    return start / rate


def find_loud_clip_start_after(
    path: Path,
    min_start_sec: float,
    *,
    clip_sec: float = DEFAULT_PREVIEW_CLIP_SEC,
    window_sec: float = 0.5,
) -> float:
    """Return start time (sec) of a loud clip window at or after ``min_start_sec``."""
    audio, rate = _wav_mono_float(path)
    n = len(audio)
    clip_samples = int(clip_sec * rate)
    if n < clip_samples:
        return 0.0

    start_idx = int(min_start_sec * rate)
    start_idx = max(0, min(start_idx, n - clip_samples))
    search = audio[start_idx:]
    if len(search) <= clip_samples:
        return start_idx / rate

    win = max(1, int(window_sec * rate))
    best_local = 0
    best_rms = -1.0
    hop = win // 2 or 1
    max_local = len(search) - clip_samples
    for i in range(0, max(1, max_local + 1), hop):
        seg = search[i : i + win]
        if len(seg) < win // 2:
            break
        rms = float(np.sqrt(np.mean(np.square(seg))))
        if rms > best_rms:
            best_rms = rms
            best_local = i
    start = start_idx + best_local
    start = max(start_idx, min(start, n - clip_samples))
    return start / rate


def find_loud_clip_starts(
    path: Path,
    *,
    clip_sec: float = DEFAULT_PREVIEW_CLIP_SEC,
    section_fractions: tuple[float, ...] = DEFAULT_PREVIEW_SECTIONS,
    window_sec: float = 0.5,
    min_separation_sec: float = MIN_PREVIEW_CLIP_SEPARATION_SEC,
) -> list[float]:
    """Return loud clip start times, at least ``min_separation_sec`` apart when possible."""
    if not section_fractions:
        return []

    duration = ffprobe_duration(path)
    max_start = max(0.0, duration - clip_sec)

    first = find_loud_clip_start(
        path,
        clip_sec=clip_sec,
        after_fraction=section_fractions[0],
        window_sec=window_sec,
    )
    first = max(0.0, min(first, max_start))
    starts = [first]

    for fraction in section_fractions[1:]:
        target = find_loud_clip_start(
            path,
            clip_sec=clip_sec,
            after_fraction=fraction,
            window_sec=window_sec,
        )
        target = max(0.0, min(target, max_start))

        if abs(target - first) >= min_separation_sec - 0.05:
            starts.append(target)
            continue

        before_limit = first - min_separation_sec
        after_min = first + min_separation_sec
        candidates: list[float] = []

        if before_limit >= 0.0:
            before = find_loud_clip_start_before(
                path,
                before_limit,
                clip_sec=clip_sec,
                window_sec=window_sec,
            )
            before = max(0.0, min(before, max_start))
            if abs(before - first) >= min_separation_sec - 0.05:
                candidates.append(before)

        if after_min <= max_start:
            after = find_loud_clip_start_after(
                path,
                after_min,
                clip_sec=clip_sec,
                window_sec=window_sec,
            )
            after = max(0.0, min(after, max_start))
            if abs(after - first) >= min_separation_sec - 0.05:
                candidates.append(after)

        if candidates:
            after_candidates = [c for c in candidates if c > first]
            before_candidates = [c for c in candidates if c < first]
            if fraction > section_fractions[0]:
                pool = after_candidates or before_candidates
            else:
                pool = before_candidates or after_candidates
            nxt = min(pool, key=lambda c: abs(c - target))
            starts.append(nxt)
            continue

        # File too short to place both clips with full separation: spread what we can.
        if after_min <= max_start:
            starts[0] = max(0.0, min(first, max_start - min_separation_sec))
            starts.append(min(max_start, starts[0] + min_separation_sec))
        elif before_limit >= 0.0:
            starts.append(max(0.0, first - min_separation_sec))
            starts[0] = min(first, starts[-1] + min_separation_sec)
        else:
            starts.append(target)

    return starts


def extract_audio_clip(
    wav: Path,
    out_wav: Path,
    *,
    start_sec: float,
    duration_sec: float = DEFAULT_PREVIEW_CLIP_SEC,
) -> Path:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start_sec:.3f}",
            "-t",
            f"{duration_sec:.3f}",
            "-i",
            str(wav),
            "-ac",
            "1",
            str(out_wav),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not out_wav.is_file():
        raise RuntimeError(
            f"ffmpeg audio clip failed for {wav}:\n{proc.stderr.strip()}"
        )
    return out_wav


def role_to_video_name(role: str) -> str:
    mapping = {
        "host": HOST_RAW_VIDEO,
        "guest": GUEST_RAW_VIDEO,
        "wide": WIDE_RAW_VIDEO,
    }
    if role not in mapping:
        raise ValueError(f"Video role {role!r} has no destination filename.")
    return mapping[role]


def role_to_audio_name(role: str) -> str:
    mapping = {"host": HOST_RAW_AUDIO, "guest": GUEST_RAW_AUDIO}
    if role not in mapping:
        raise ValueError(f"Audio role {role!r} has no destination filename.")
    return mapping[role]


def validate_video_labels(labels: dict[str, str]) -> None:
    roles = list(labels.values())
    for required in ("host", "guest", "wide"):
        if roles.count(required) != 1:
            raise ValueError(
                f"Expected exactly one video labeled {required!r}, "
                f"got {roles.count(required)} in {labels}"
            )
    for role in roles:
        if role not in VIDEO_ROLES:
            raise ValueError(f"Invalid video role {role!r}")


def validate_audio_labels(labels: dict[str, str]) -> None:
    roles = list(labels.values())
    for required in ("host", "guest"):
        if roles.count(required) != 1:
            raise ValueError(
                f"Expected exactly one audio labeled {required!r}, "
                f"got {roles.count(required)} in {labels}"
            )
    for role in roles:
        if role not in AUDIO_ROLES:
            raise ValueError(f"Invalid audio role {role!r}")


def move_labeled_media(
    state: dict,
    *,
    video_labels: dict[str, str],
    audio_labels: dict[str, str],
    allow_overwrite: bool = False,
) -> dict:
    """Copy labeled sources into Raw with standard names. Source files are never moved."""
    from harness_overwrite_guard import refuse_overwrite

    validate_video_labels(video_labels)
    validate_audio_labels(audio_labels)
    raw = Path(state["paths"]["raw"])
    raw.mkdir(parents=True, exist_ok=True)
    original_paths: dict[str, str] = {}
    copied: dict[str, str] = {}

    def _copy_labeled_source(src: Path, dest: Path) -> None:
        if src.resolve() == dest.resolve():
            return
        refuse_overwrite(dest, allow_overwrite=allow_overwrite)
        if dest.exists():
            dest.unlink()
        shutil.copy2(src, dest)

    for src_str, role in video_labels.items():
        if role == "do_not_use":
            continue
        src = Path(src_str)
        if not src.is_file():
            raise FileNotFoundError(f"Labeled video missing: {src}")
        dest = raw / role_to_video_name(role)
        _copy_labeled_source(src, dest)
        original_paths[dest.name] = str(src.resolve())
        copied[role] = str(dest.resolve())

    for src_str, role in audio_labels.items():
        if role == "do_not_use":
            continue
        src = Path(src_str)
        if not src.is_file():
            raise FileNotFoundError(f"Labeled audio missing: {src}")
        dest = raw / role_to_audio_name(role)
        _copy_labeled_source(src, dest)
        original_paths[dest.name] = str(src.resolve())
        copied[role + "_audio"] = str(dest.resolve())

    state["labels"] = {
        "videos": {Path(k).name: v for k, v in video_labels.items()},
        "audios": {Path(k).name: v for k, v in audio_labels.items()},
    }
    state["original_paths"] = original_paths
    state["copied_raw"] = copied
    state["moved_raw"] = copied
    return state


def restore_moved_sources(
    state: dict,
    working_folder: Path,
    *,
    allow_overwrite: bool = False,
) -> list[str]:
    """
    Copy labeled Raw files back to their original source paths when missing.

    Used to recover sources that were moved before copy-only labeling. Existing
    Raw copies are kept so in-progress sessions can continue.
    """
    from harness_overwrite_guard import refuse_overwrite

    original_paths = state.get("original_paths")
    if not isinstance(original_paths, dict) or not original_paths:
        return []

    raw = Path(state["paths"]["raw"])
    restored: list[str] = []
    for dest_name, original_str in original_paths.items():
        original = Path(original_str)
        if original.is_file():
            continue
        raw_copy = raw / dest_name
        if not raw_copy.is_file():
            continue
        original.parent.mkdir(parents=True, exist_ok=True)
        refuse_overwrite(original, allow_overwrite=allow_overwrite)
        shutil.copy2(raw_copy, original)
        restored.append(f"{raw_copy} -> {original}")
    return restored


def swap_host_guest_files(raw_dir: Path, *, kind: str) -> list[str]:
    """Swap Host/Guest Raw Video or Audio filenames in place. kind: video|audio|both."""
    actions: list[str] = []
    pairs: list[tuple[str, str]] = []
    if kind in ("video", "both"):
        pairs.append((HOST_RAW_VIDEO, GUEST_RAW_VIDEO))
    if kind in ("audio", "both"):
        pairs.append((HOST_RAW_AUDIO, GUEST_RAW_AUDIO))
    for a_name, b_name in pairs:
        a = raw_dir / a_name
        b = raw_dir / b_name
        if not a.is_file() or not b.is_file():
            raise FileNotFoundError(f"Cannot swap; missing {a.name} or {b.name} in {raw_dir}")
        tmp = raw_dir / f".piab-swap-tmp-{a_name}"
        if tmp.exists():
            tmp.unlink()
        a.rename(tmp)
        b.rename(a)
        tmp.rename(b)
        actions.append(f"swapped {a_name} <-> {b_name}")
    return actions


def print_json(data: object) -> None:
    print(json.dumps(data, indent=2))
