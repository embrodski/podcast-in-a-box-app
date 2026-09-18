"""
Snap cut times to nearby audio zero crossings (reading / embedded-audio renders).

Uses a short ffmpeg decode window around each nominal cut so hard concatenation
of per-camera MP4 audio is less likely to click.
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

try:
    from win_hidden_console import install_hidden_console
except ImportError:
    _scripts = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
    if _scripts not in sys.path:
        sys.path.insert(0, _scripts)
    from win_hidden_console import install_hidden_console

install_hidden_console()

DEFAULT_HALF_WINDOW_SEC = 0.2
DEFAULT_SAMPLE_RATE = 48000
# Prefer crossings in low-level (room-tone) regions when multiple exist.
_AMP_WEIGHT = 8.0
# Treat |sample| below this as "near silence" at the crossing.
_NEAR_SILENCE = 0.02


def _ffmpeg_base() -> List[str]:
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]


def group_time_to_video_seek(clip_info: dict, group_audio_t: float) -> float:
    """Map a group-timeline second to a seek position in the clip's video file."""
    return clip_info["video_start"] + (group_audio_t - clip_info["audio_start"])


def video_seek_to_group_time(clip_info: dict, video_seek_t: float) -> float:
    return clip_info["audio_start"] + (video_seek_t - clip_info["video_start"])


def pick_best_zero_crossing(
    samples: Sequence[float],
    window_start_sec: float,
    sample_rate: int,
    center_sec: float,
    half_window_sec: float,
) -> Optional[float]:
    """Return the best zero-crossing time (absolute seconds) in ``samples``, or None."""
    if len(samples) < 2 or sample_rate <= 0:
        return None

    best_t: Optional[float] = None
    best_score = float("inf")

    for i in range(1, len(samples)):
        a = samples[i - 1]
        b = samples[i]
        if a * b > 0:
            continue
        denom = abs(a) + abs(b)
        frac = 0.5 if denom < 1e-12 else abs(a) / denom
        t_cross = window_start_sec + (i - 1 + frac) / float(sample_rate)
        if abs(t_cross - center_sec) > half_window_sec + 1e-9:
            continue
        amp = min(abs(a), abs(b))
        score = abs(t_cross - center_sec) + _AMP_WEIGHT * amp
        if score < best_score:
            best_score = score
            best_t = t_cross

    return best_t


@lru_cache(maxsize=256)
def _decode_mono_f32_window(
    media_path: str,
    center_sec_key: int,
    half_window_key: int,
    sample_rate: int,
) -> Tuple[Tuple[float, ...], float]:
    """Decode ``[center-half_window, center+half_window]`` as mono f32le (cached)."""
    center_sec = center_sec_key / 1000.0
    half_window_sec = half_window_key / 1000.0
    path = os.path.normpath(media_path)
    if not os.path.isfile(path):
        return tuple(), 0.0

    start = max(0.0, center_sec - half_window_sec)
    duration = 2.0 * half_window_sec
    cmd = _ffmpeg_base() + [
        "-ss",
        f"{start:.6f}",
        "-i",
        path,
        "-t",
        f"{duration:.6f}",
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "f32le",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return tuple(), start

    raw = proc.stdout
    if len(raw) < 8:
        return tuple(), start
    count = len(raw) // 4
    samples = struct.unpack(f"<{count}f", raw[: count * 4])
    return samples, start


def find_zero_crossing_in_file(
    media_path: str,
    center_sec: float,
    half_window_sec: float = DEFAULT_HALF_WINDOW_SEC,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> Optional[float]:
    """Find a zero crossing in ``media_path`` within ±half_window_sec of ``center_sec``."""
    if not media_path or half_window_sec <= 0:
        return None
    key_c = int(round(center_sec * 1000.0))
    key_h = int(round(half_window_sec * 1000.0))
    samples, window_start = _decode_mono_f32_window(
        media_path, key_c, key_h, sample_rate,
    )
    if not samples:
        return None
    return pick_best_zero_crossing(
        samples, window_start, sample_rate, center_sec, half_window_sec,
    )


def snap_boundary_group_time(
    nominal_group_t: float,
    outgoing_clip_info: Optional[dict],
    incoming_clip_info: Optional[dict],
    half_window_sec: float = DEFAULT_HALF_WINDOW_SEC,
) -> float:
    """
    Adjust a group-timeline cut time to a nearby zero crossing when one exists.

    With two cameras, considers crossings on both files and picks the candidate
    closest to ``nominal_group_t`` (still within the search window).
    """
    candidates: List[float] = []

    if outgoing_clip_info is not None:
        out_seek = group_time_to_video_seek(outgoing_clip_info, nominal_group_t)
        out_cross = find_zero_crossing_in_file(
            outgoing_clip_info["video_file"], out_seek, half_window_sec,
        )
        if out_cross is not None:
            candidates.append(video_seek_to_group_time(outgoing_clip_info, out_cross))

    if incoming_clip_info is not None:
        in_seek = group_time_to_video_seek(incoming_clip_info, nominal_group_t)
        in_cross = find_zero_crossing_in_file(
            incoming_clip_info["video_file"], in_seek, half_window_sec,
        )
        if in_cross is not None:
            candidates.append(video_seek_to_group_time(incoming_clip_info, in_cross))

    if not candidates:
        return nominal_group_t

    best = min(candidates, key=lambda t: abs(t - nominal_group_t))
    if abs(best - nominal_group_t) <= half_window_sec + 1e-6:
        return best
    return nominal_group_t


def snap_enabled() -> bool:
    raw = os.environ.get("PODCAST_DSL_ZERO_CROSS_SNAP", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def snap_interior_boundaries(
    raw_boundaries: List[float],
    clip_infos: List[dict],
    *,
    half_window_sec: float = DEFAULT_HALF_WINDOW_SEC,
    min_gap_sec: float = 1.0 / 30.0,
) -> None:
    """
    Mutate ``raw_boundaries`` in place: interior cuts snap to zero crossings.

    ``clip_infos`` entries match ``_build_camera_spans`` (dict with ``clip_info``).
    """
    if len(raw_boundaries) < 2 or len(clip_infos) < 1:
        return

    n = len(raw_boundaries)
    for b in range(1, n - 1):
        lo = raw_boundaries[b - 1] + min_gap_sec
        hi = raw_boundaries[b + 1] - min_gap_sec
        if lo >= hi:
            continue

        nominal = raw_boundaries[b]
        outgoing = clip_infos[b - 1]["clip_info"] if b - 1 < len(clip_infos) else None
        incoming = clip_infos[b]["clip_info"] if b < len(clip_infos) else None

        snapped = snap_boundary_group_time(
            nominal, outgoing, incoming, half_window_sec,
        )
        raw_boundaries[b] = max(lo, min(hi, snapped))

    # Group end: snap outgoing tail only.
    if n >= 2 and clip_infos:
        b = n - 1
        lo = raw_boundaries[b - 1] + min_gap_sec
        nominal = raw_boundaries[b]
        outgoing = clip_infos[-1]["clip_info"]
        snapped = snap_boundary_group_time(nominal, outgoing, None, half_window_sec)
        raw_boundaries[b] = max(lo, snapped)
