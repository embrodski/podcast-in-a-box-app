#!/usr/bin/env python3
"""
Extract the audio stream from an MP4 to a stereo PCM WAV via ffmpeg.

Used by the video-sync workflow to write the anchor clip's final audio
(-synced or -prepped) to a WAV in the same folder as the final MP4 deliverables.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from win_hidden_console import install_hidden_console

install_hidden_console()


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(
            f"Command failed ({r.returncode}): {' '.join(cmd)}\n{r.stderr or r.stdout}"
        )


def extract_mp4_audio_wav(
    video: Path,
    out_wav: Path,
    *,
    sample_rate: int = 48000,
) -> None:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video.resolve()),
            "-vn",
            "-ac",
            "2",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(out_wav.resolve()),
        ]
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("video", type=Path, help="Input MP4 (or any container ffmpeg reads).")
    p.add_argument(
        "output_wav",
        type=Path,
        help="Output WAV path (PCM s16le stereo).",
    )
    p.add_argument(
        "--sample-rate",
        type=int,
        default=48000,
        help="Sample rate for output WAV (default: 48000).",
    )
    args = p.parse_args()

    if not args.video.is_file():
        print(f"Missing file: {args.video}", file=sys.stderr)
        return 2

    try:
        extract_mp4_audio_wav(
            args.video.resolve(),
            args.output_wav.resolve(),
            sample_rate=args.sample_rate,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Wrote {args.output_wav.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
