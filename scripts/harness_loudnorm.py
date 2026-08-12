"""
Two-pass EBU R128 loudness normalization for prep deliverables (ffmpeg loudnorm).

Used when writing *-prepped.mp4 and *-prepped.wav during video-sync. Targets
-14 LUFS integrated loudness (streaming/podcast), with true-peak limiting.

See docs/loudness-normalization.md for algorithm details and porting notes.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Prep / streaming target (YouTube/Spotify-class; louder than broadcast -23).
PREPPED_TARGET_I_LUFS = -14.0
PREPPED_TARGET_TP_DBTP = -1.5
PREPPED_TARGET_LRA_LU = 11.0
PREPPED_AAC_BITRATE = "192k"  # match multicam_align_trim default
PREPPED_WAV_SAMPLE_RATE = 48000


@dataclass(frozen=True)
class LoudnormMeasurement:
    input_i: float
    input_tp: float
    input_lra: float
    input_thresh: float
    target_offset: float

    @classmethod
    def from_loudnorm_json(cls, payload: dict) -> LoudnormMeasurement:
        def _f(key: str) -> float:
            if key not in payload:
                raise ValueError(f"loudnorm JSON missing {key!r}")
            return float(payload[key])

        return cls(
            input_i=_f("input_i"),
            input_tp=_f("input_tp"),
            input_lra=_f("input_lra"),
            input_thresh=_f("input_thresh"),
            target_offset=_f("target_offset"),
        )


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{detail}"
        )
    return proc


def parse_loudnorm_json(stderr_or_stdout: str) -> dict:
    """Extract the loudnorm JSON object ffmpeg prints with print_format=json."""
    match = re.search(r"\{[\s\S]*\}", stderr_or_stdout)
    if not match:
        raise RuntimeError("loudnorm pass 1 did not emit JSON stats")
    return json.loads(match.group(0))


def build_loudnorm_pass1_filter(
    *,
    target_i: float = PREPPED_TARGET_I_LUFS,
    target_tp: float = PREPPED_TARGET_TP_DBTP,
    target_lra: float = PREPPED_TARGET_LRA_LU,
) -> str:
    return (
        f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}:print_format=json"
    )


def build_loudnorm_pass2_filter(
    measured: LoudnormMeasurement,
    *,
    target_i: float = PREPPED_TARGET_I_LUFS,
    target_tp: float = PREPPED_TARGET_TP_DBTP,
    target_lra: float = PREPPED_TARGET_LRA_LU,
) -> str:
    return (
        f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}:"
        f"measured_I={measured.input_i:.2f}:"
        f"measured_TP={measured.input_tp:.2f}:"
        f"measured_LRA={measured.input_lra:.2f}:"
        f"measured_thresh={measured.input_thresh:.2f}:"
        f"offset={measured.target_offset:.2f}:linear=true"
    )


def build_trimmed_audio_atrim_chain(trim_sec: float) -> str:
    """Audio filter prefix matching multicam head trim (seconds)."""
    return f"atrim=start={trim_sec},asetpts=PTS-STARTPTS"


def build_trimmed_audio_pass1_filter(
    trim_sec: float,
    *,
    target_i: float = PREPPED_TARGET_I_LUFS,
    target_tp: float = PREPPED_TARGET_TP_DBTP,
    target_lra: float = PREPPED_TARGET_LRA_LU,
) -> str:
    """Pass 1 loudnorm on post-trim audio (for multicam-integrated prep)."""
    return (
        f"{build_trimmed_audio_atrim_chain(trim_sec)},"
        f"{build_loudnorm_pass1_filter(target_i=target_i, target_tp=target_tp, target_lra=target_lra)}"
    )


def build_trimmed_audio_pass2_filter_chain(
    trim_sec: float,
    measured: LoudnormMeasurement,
    *,
    target_i: float = PREPPED_TARGET_I_LUFS,
    target_tp: float = PREPPED_TARGET_TP_DBTP,
    target_lra: float = PREPPED_TARGET_LRA_LU,
) -> str:
    """Pass 2 loudnorm chained after multicam head trim."""
    return (
        f"{build_trimmed_audio_atrim_chain(trim_sec)},"
        f"{build_loudnorm_pass2_filter(measured, target_i=target_i, target_tp=target_tp, target_lra=target_lra)}"
    )


def _measure_loudnorm_with_audio_filter(
    input_path: Path,
    audio_filter: str,
    *,
    target_i: float = PREPPED_TARGET_I_LUFS,
) -> LoudnormMeasurement:
    """Pass 1: audio-only loudnorm analysis (-vn skips video decode)."""
    proc = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "info",
            "-i",
            str(input_path.resolve()),
            "-vn",
            "-map",
            "0:a:0?",
            "-af",
            audio_filter,
            "-f",
            "null",
            "-",
        ]
    )
    payload = parse_loudnorm_json(proc.stderr)
    return LoudnormMeasurement.from_loudnorm_json(payload)


def measure_loudnorm(
    input_path: Path,
    *,
    target_i: float = PREPPED_TARGET_I_LUFS,
    target_tp: float = PREPPED_TARGET_TP_DBTP,
    target_lra: float = PREPPED_TARGET_LRA_LU,
) -> LoudnormMeasurement:
    """Pass 1: analyze integrated loudness; does not write media output."""
    return _measure_loudnorm_with_audio_filter(
        input_path,
        build_loudnorm_pass1_filter(
            target_i=target_i, target_tp=target_tp, target_lra=target_lra
        ),
        target_i=target_i,
    )


def measure_loudnorm_trimmed(
    input_path: Path,
    trim_sec: float,
    *,
    target_i: float = PREPPED_TARGET_I_LUFS,
    target_tp: float = PREPPED_TARGET_TP_DBTP,
    target_lra: float = PREPPED_TARGET_LRA_LU,
) -> LoudnormMeasurement:
    """Pass 1 on head-trimmed audio (matches multicam prepped timeline)."""
    return _measure_loudnorm_with_audio_filter(
        input_path,
        build_trimmed_audio_pass1_filter(
            trim_sec,
            target_i=target_i,
            target_tp=target_tp,
            target_lra=target_lra,
        ),
        target_i=target_i,
    )


def normalize_wav_two_pass(
    input_path: Path,
    output_path: Path,
    *,
    target_i: float = PREPPED_TARGET_I_LUFS,
    target_tp: float = PREPPED_TARGET_TP_DBTP,
    target_lra: float = PREPPED_TARGET_LRA_LU,
    sample_rate: int = PREPPED_WAV_SAMPLE_RATE,
) -> LoudnormMeasurement:
    """Pass 1 + 2: write normalized stereo PCM WAV."""
    measured = measure_loudnorm(
        input_path,
        target_i=target_i,
        target_tp=target_tp,
        target_lra=target_lra,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path.resolve()),
            "-af",
            build_loudnorm_pass2_filter(
                measured,
                target_i=target_i,
                target_tp=target_tp,
                target_lra=target_lra,
            ),
            "-ac",
            "2",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(output_path.resolve()),
        ]
    )
    return measured


def normalize_mp4_audio_two_pass(
    input_path: Path,
    output_path: Path,
    *,
    target_i: float = PREPPED_TARGET_I_LUFS,
    target_tp: float = PREPPED_TARGET_TP_DBTP,
    target_lra: float = PREPPED_TARGET_LRA_LU,
    audio_bitrate: str = PREPPED_AAC_BITRATE,
    trim_sec: float | None = None,
) -> LoudnormMeasurement:
    """Pass 1 + 2: copy video stream; re-encode audio with loudnorm."""
    if trim_sec is not None and trim_sec > 0.0:
        measured = measure_loudnorm_trimmed(
            input_path,
            trim_sec,
            target_i=target_i,
            target_tp=target_tp,
            target_lra=target_lra,
        )
        audio_filter = build_trimmed_audio_pass2_filter_chain(
            trim_sec,
            measured,
            target_i=target_i,
            target_tp=target_tp,
            target_lra=target_lra,
        )
    else:
        measured = measure_loudnorm(
            input_path,
            target_i=target_i,
            target_tp=target_tp,
            target_lra=target_lra,
        )
        audio_filter = build_loudnorm_pass2_filter(
            measured,
            target_i=target_i,
            target_tp=target_tp,
            target_lra=target_lra,
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path.resolve()),
            "-af",
            audio_filter,
            "-map",
            "0:v:0?",
            "-map",
            "0:a:0?",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            audio_bitrate,
            str(output_path.resolve()),
        ]
    )
    return measured


def normalize_prepped_media_inplace(
    path: Path,
    *,
    target_i: float = PREPPED_TARGET_I_LUFS,
    target_tp: float = PREPPED_TARGET_TP_DBTP,
    target_lra: float = PREPPED_TARGET_LRA_LU,
) -> LoudnormMeasurement:
    """
    Replace ``path`` with a two-pass loudnorm version (same extension).

    Supports ``.mp4`` (video copy + AAC audio) and ``.wav`` (PCM s16le stereo).
    """
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    suffix = path.suffix.lower()
    with tempfile.NamedTemporaryFile(
        prefix=f"{path.stem}.loudnorm.",
        suffix=path.suffix,
        dir=str(path.parent),
        delete=False,
    ) as tmp:
        tmp_path = Path(tmp.name)

    try:
        if suffix == ".mp4":
            measured = normalize_mp4_audio_two_pass(
                path,
                tmp_path,
                target_i=target_i,
                target_tp=target_tp,
                target_lra=target_lra,
            )
        elif suffix == ".wav":
            measured = normalize_wav_two_pass(
                path,
                tmp_path,
                target_i=target_i,
                target_tp=target_tp,
                target_lra=target_lra,
            )
        else:
            raise ValueError(f"Unsupported media type for loudnorm: {path}")

        tmp_path.replace(path)
        return measured
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def normalize_prepped_outputs(
    paths: list[Path],
    *,
    target_i: float = PREPPED_TARGET_I_LUFS,
    target_tp: float = PREPPED_TARGET_TP_DBTP,
    target_lra: float = PREPPED_TARGET_LRA_LU,
) -> list[tuple[Path, LoudnormMeasurement]]:
    """Normalize each prep deliverable in place; returns per-file measurements."""
    results: list[tuple[Path, LoudnormMeasurement]] = []
    for path in paths:
        measured = normalize_prepped_media_inplace(
            path,
            target_i=target_i,
            target_tp=target_tp,
            target_lra=target_lra,
        )
        results.append((path.resolve(), measured))
        print(
            f"Loudnorm {path.name}: input_i={measured.input_i:.1f} LUFS "
            f"-> target {target_i:.1f} LUFS (two-pass linear)",
            file=sys.stderr,
        )
    return results


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Two-pass loudnorm for prep MP4/WAV (-14 LUFS default)."
    )
    parser.add_argument("media", type=Path, nargs="+", help="Files to normalize in place.")
    parser.add_argument("--target-i", type=float, default=PREPPED_TARGET_I_LUFS)
    parser.add_argument("--target-tp", type=float, default=PREPPED_TARGET_TP_DBTP)
    parser.add_argument("--target-lra", type=float, default=PREPPED_TARGET_LRA_LU)
    args = parser.parse_args()

    try:
        normalize_prepped_outputs(
            [p.resolve() for p in args.media],
            target_i=args.target_i,
            target_tp=args.target_tp,
            target_lra=args.target_lra,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
