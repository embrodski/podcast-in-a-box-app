"""
Shared utilities for reference-based video color matching.
"""

import math
import os
import re
import subprocess
import sys
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

try:
    from win_hidden_console import install_hidden_console
except ImportError:
    _scripts = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
    if _scripts not in sys.path:
        sys.path.insert(0, _scripts)
    from win_hidden_console import install_hidden_console

install_hidden_console()


def ffmpeg_cmd_base() -> List[str]:
    """Build a low-noise FFmpeg command prefix."""
    return ['ffmpeg', '-hide_banner', '-nostats', '-loglevel', 'error', '-y']


@lru_cache(maxsize=None)
def _get_video_duration(video_file: str) -> float:
    """Return the duration of a video file in seconds."""
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        video_file,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


@lru_cache(maxsize=None)
def probe_mean_signalstats_at_offset(
    video_file: str,
    *,
    start_seconds: float = 0.0,
    sample_seconds: float = 8.0,
    max_frames: int = 240,
    vf_prefix: str = "",
) -> Tuple[float, float, float]:
    """
    Estimate mean Y/U/V averages for a video using ffmpeg signalstats on a short sample.

    The sample is measured on the center crop so framing differences and
    letterboxing do not dominate the statistic.

    Optional ``vf_prefix`` is prepended before the crop (e.g. scale-to-1080p so
    analysis matches a downscale-first render pipeline).
    """
    crop_chain = "crop=iw*0.6:ih*0.6:(iw-ow)/2:(ih-oh)/2,signalstats,metadata=print:file=-"
    vf = f"{vf_prefix},{crop_chain}" if vf_prefix else crop_chain
    cmd = ffmpeg_cmd_base() + [
        '-ss', str(max(0.0, start_seconds)),
        '-i', video_file,
        '-t', str(sample_seconds),
        '-vf', vf,
        '-frames:v', str(max_frames),
        '-f', 'null',
        '-',
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg signalstats failed for {video_file}:\n{result.stderr.strip()}")

    vals: Dict[str, List[float]] = {
        'YAVG': [],
        'UAVG': [],
        'VAVG': [],
    }
    for line in result.stdout.splitlines():
        match = re.search(
            r'(?:^|\b)([YUV]AVG)=(\d+(?:\.\d+)?)\b',
            line,
            flags=re.IGNORECASE,
        )
        if match:
            vals[match.group(1).upper()].append(float(match.group(2)))
            continue
        # Alternate formatting seen in some builds.
        for key in ('yavg', 'uavg', 'vavg'):
            alt = re.search(rf'\b{key}:(\d+(?:\.\d+)?)\b', line, flags=re.IGNORECASE)
            if alt:
                vals[key.upper()].append(float(alt.group(1)))

    if not vals['YAVG']:
        raise RuntimeError(f"No YAVG values parsed for {video_file}")

    return (
        sum(vals['YAVG']) / len(vals['YAVG']),
        sum(vals['UAVG']) / len(vals['UAVG']) if vals['UAVG'] else 128.0,
        sum(vals['VAVG']) / len(vals['VAVG']) if vals['VAVG'] else 128.0,
    )


@lru_cache(maxsize=None)
def probe_mean_yavg_at_offset(
    video_file: str,
    *,
    start_seconds: float = 0.0,
    sample_seconds: float = 8.0,
    max_frames: int = 240,
    vf_prefix: str = "",
) -> float:
    """
    Estimate mean luma (YAVG) for a video using ffmpeg signalstats on a short sample.
    """
    yavg, _, _ = probe_mean_signalstats_at_offset(
        video_file,
        start_seconds=start_seconds,
        sample_seconds=sample_seconds,
        max_frames=max_frames,
        vf_prefix=vf_prefix,
    )
    return yavg


@lru_cache(maxsize=None)
def probe_mean_yavg(
    video_file: str,
    *,
    sample_seconds: float = 8.0,
    max_frames: int = 240,
    vf_prefix: str = "",
) -> float:
    """
    Estimate mean luma (YAVG) for a video using the start of the file.

    This preserves the historical behavior used by the podcast renderer.
    """
    return probe_mean_yavg_at_offset(
        video_file,
        start_seconds=0.0,
        sample_seconds=sample_seconds,
        max_frames=max_frames,
        vf_prefix=vf_prefix,
    )


@lru_cache(maxsize=None)
def probe_mean_signalstats_multi(
    video_file: str,
    *,
    sample_seconds: float = 8.0,
    max_frames: int = 240,
    sample_offsets: Tuple[float, ...] = (0.0, 1 / 3, 2 / 3),
    vf_prefix: str = "",
) -> Tuple[float, float, float]:
    """
    Estimate mean Y/U/V across multiple windows distributed through the file.

    `sample_offsets` are fractions of the total duration in [0, 1].
    """
    duration = _get_video_duration(video_file)
    if duration <= 0:
        raise RuntimeError(f"Invalid video duration for {video_file}: {duration}")

    max_start = max(0.0, duration - sample_seconds)
    starts = []
    for offset in sample_offsets:
        clamped = max(0.0, min(1.0, offset))
        starts.append(min(max_start, duration * clamped))

    vals = [
        probe_mean_signalstats_at_offset(
            video_file,
            start_seconds=start,
            sample_seconds=sample_seconds,
            max_frames=max_frames,
            vf_prefix=vf_prefix,
        )
        for start in starts
    ]
    count = len(vals)
    return (
        sum(v[0] for v in vals) / count,
        sum(v[1] for v in vals) / count,
        sum(v[2] for v in vals) / count,
    )


@lru_cache(maxsize=None)
def probe_mean_yavg_multi(
    video_file: str,
    *,
    sample_seconds: float = 8.0,
    max_frames: int = 240,
    sample_offsets: Tuple[float, ...] = (0.0, 1 / 3, 2 / 3),
    vf_prefix: str = "",
) -> float:
    """
    Estimate mean luma across multiple windows distributed through the file.

    `sample_offsets` are fractions of the total duration in [0, 1].
    """
    yavg, _, _ = probe_mean_signalstats_multi(
        video_file,
        sample_seconds=sample_seconds,
        max_frames=max_frames,
        sample_offsets=sample_offsets,
        vf_prefix=vf_prefix,
    )
    return yavg


def build_color_match_vf_from_yavg(
    reference_yavg: float,
    target_yavg: float,
    *,
    reference_uavg: Optional[float] = None,
    target_uavg: Optional[float] = None,
    reference_vavg: Optional[float] = None,
    target_vavg: Optional[float] = None,
    strength: float = 1.0,
    gamma_scale: float = 1.0,
    brightness_scale: float = 1.0,
    saturation_scale: float = 1.0,
    vibrance_scale: float = 1.0,
    chroma_strength: float = 0.0,
    chroma_scale: float = 1.0,
    chroma_midtone_max: float = 0.08,
    max_abs_d: float = 0.35,
    gamma_min: float = 0.95,
    gamma_max: float = 1.14,
    brightness_min: float = -0.04,
    brightness_max: float = 0.06,
    saturation_min: float = 0.95,
    saturation_max: float = 1.18,
    vibrance_max: float = 0.45,
) -> str:
    """
    Build an ffmpeg filter snippet that gently nudges a target toward a reference.

    This intentionally produces a pragmatic exposure/chroma stabilization rather than
    a full creative grade.
    """
    if reference_yavg <= 1.0 or target_yavg <= 1.0:
        return ''

    d = (reference_yavg - target_yavg) / 255.0
    d *= max(0.0, strength)
    d = max(-max_abs_d, min(max_abs_d, d))

    if abs(d) < 0.004:
        return ''

    x = d * 6.0
    t = math.tanh(x)

    gamma = 1.0 + 0.12 * gamma_scale * t
    gamma = max(gamma_min, min(gamma_max, gamma))

    brightness = 0.35 * brightness_scale * d
    brightness = max(brightness_min, min(brightness_max, brightness))

    sat_k = 0.28 if d > 0 else 0.22
    saturation = 1.0 + saturation_scale * sat_k * max(0.0, d) * 2.0
    saturation = max(saturation_min, min(saturation_max, saturation))

    vibrance = 0.22 * vibrance_scale * max(0.0, t)
    vibrance = max(0.0, min(vibrance_max, vibrance))

    unsharp = ""
    if d > 0.02:
        unsharp = "unsharp=5:5:0.65:3:3:0.0"

    parts = [
        f"eq=gamma={gamma:.6f}:brightness={brightness:.6f}:saturation={saturation:.6f}",
    ]
    if vibrance > 0:
        parts.append(f"vibrance={vibrance:.6f}")
    if unsharp:
        parts.append(unsharp)

    if (
        chroma_strength > 0
        and reference_uavg is not None
        and target_uavg is not None
        and reference_vavg is not None
        and target_vavg is not None
    ):
        du = ((reference_uavg - target_uavg) / 255.0) * chroma_strength
        dv = ((reference_vavg - target_vavg) / 255.0) * chroma_strength

        # Approximate chroma-only RGB deltas from YUV deltas, then map them into a
        # tightly clamped midtone colorbalance correction with preserved lightness.
        rm = chroma_scale * (1.402 * dv)
        gm = chroma_scale * (-0.344136 * du - 0.714136 * dv)
        bm = chroma_scale * (1.772 * du)

        rm = max(-chroma_midtone_max, min(chroma_midtone_max, rm))
        gm = max(-chroma_midtone_max, min(chroma_midtone_max, gm))
        bm = max(-chroma_midtone_max, min(chroma_midtone_max, bm))

        if max(abs(rm), abs(gm), abs(bm)) >= 0.002:
            parts.append(
                f"colorbalance=rm={rm:.6f}:gm={gm:.6f}:bm={bm:.6f}:pl=1"
            )

    return ",".join(parts)


@lru_cache(maxsize=None)
def build_color_match_vf(
    reference_file: str,
    target_file: str,
    *,
    sample_seconds: float = 8.0,
    max_frames: int = 240,
    sample_offsets: Optional[Tuple[float, ...]] = None,
    analysis_vf_prefix: str = "",
    strength: float = 1.0,
    gamma_scale: float = 1.0,
    brightness_scale: float = 1.0,
    saturation_scale: float = 1.0,
    vibrance_scale: float = 1.0,
    chroma_strength: float = 0.0,
    chroma_scale: float = 1.0,
    chroma_midtone_max: float = 0.08,
    max_abs_d: float = 0.35,
    gamma_min: float = 0.95,
    gamma_max: float = 1.14,
    brightness_min: float = -0.04,
    brightness_max: float = 0.06,
    saturation_min: float = 0.95,
    saturation_max: float = 1.18,
    vibrance_max: float = 0.45,
) -> str:
    """
    Probe both files and build a filter snippet for the target.

    When rendering with a downscale-first pipeline, pass the same leading scale/pad
    chain used at encode time as ``analysis_vf_prefix`` so probes match geometry.
    """
    if sample_offsets is None:
        reference_yavg = probe_mean_yavg(
            reference_file,
            sample_seconds=sample_seconds,
            max_frames=max_frames,
            vf_prefix=analysis_vf_prefix,
        )
        target_yavg = probe_mean_yavg(
            target_file,
            sample_seconds=sample_seconds,
            max_frames=max_frames,
            vf_prefix=analysis_vf_prefix,
        )
        reference_uavg = target_uavg = reference_vavg = target_vavg = None
    else:
        reference_yavg, reference_uavg, reference_vavg = probe_mean_signalstats_multi(
            reference_file,
            sample_seconds=sample_seconds,
            max_frames=max_frames,
            sample_offsets=sample_offsets,
            vf_prefix=analysis_vf_prefix,
        )
        target_yavg, target_uavg, target_vavg = probe_mean_signalstats_multi(
            target_file,
            sample_seconds=sample_seconds,
            max_frames=max_frames,
            sample_offsets=sample_offsets,
            vf_prefix=analysis_vf_prefix,
        )
    return build_color_match_vf_from_yavg(
        reference_yavg,
        target_yavg,
        reference_uavg=reference_uavg,
        target_uavg=target_uavg,
        reference_vavg=reference_vavg,
        target_vavg=target_vavg,
        strength=strength,
        gamma_scale=gamma_scale,
        brightness_scale=brightness_scale,
        saturation_scale=saturation_scale,
        vibrance_scale=vibrance_scale,
        chroma_strength=chroma_strength,
        chroma_scale=chroma_scale,
        chroma_midtone_max=chroma_midtone_max,
        max_abs_d=max_abs_d,
        gamma_min=gamma_min,
        gamma_max=gamma_max,
        brightness_min=brightness_min,
        brightness_max=brightness_max,
        saturation_min=saturation_min,
        saturation_max=saturation_max,
        vibrance_max=vibrance_max,
    )
