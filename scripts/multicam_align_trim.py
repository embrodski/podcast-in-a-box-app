#!/usr/bin/env python3
"""
Multicam head trim: line up angles by the *shared program waveform* only.

Prerequisites
-------------
Each input MP4 already has **picture and muxed audio in sync** (e.g. after
sync_video_wav_replace.py). This script **does not** re-mux or resync audio vs
video inside a file. It only measures **how much extra duration** each file has
at the **head** relative to the others (same mix, shifted in time), then
applies **one identical trim in seconds** to **both** the video and audio
streams so internal A/V alignment stays unchanged.

Method
------
1. Extract **mono** from each file (analysis only).
2. Cross-correlate each file’s waveform against **Video 1** (anchor). Because
   every file carries the **same** program audio, the peak lag is stable when
   correlation is strong.
3. **Lag convention** (anchor = a, file i = b): **positive** lag means file *i*’s
   waveform is **delayed** vs Video 1 - file *i* has **more** "late" mix at the
   same sample index; equivalently it has **less** extra footage *before* the
   line-up point than a more delayed file, or **more** after the anchor for the
   same program instant (see JSON `lag_vs_anchor_ms`).
4. Choose a **common timeline zero** (which real-world instant becomes t=0 on
   every output):
   - **earliest** (default): align to the camera whose waveform is **most ahead**
     of the anchor (smallest lag vs anchor). That clip gets **no** trim; every
     other clip loses **(lag_i - min_lag)** samples from the head - i.e. drop
     each file’s **extra** footage before they all show the **same** mix phase
     as that reference.
   - **latest**: align to the **most delayed** camera (largest lag vs anchor).
     That clip gets **no** trim; others lose **(max_lag - lag_i)** from the head
     (previous behavior).

FFmpeg applies the same `trim` / `atrim` start to video and audio (re-encode by
default for sample-accurate cuts; `--stream-copy` for fast keyframe cuts).

Use **`--prepped-names`** (video-sync workflow) to write `...-prepped.mp4` by
replacing `-synced` in the input stem instead of appending `-multicamaligned`.

Requires: ffmpeg/ffprobe on PATH, numpy, scipy.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal

import numpy as np
from scipy import signal
from scipy.io import wavfile

AlignTo = Literal["earliest", "latest"]
HARDWARE_ENCODERS = ("h264_nvenc", "h264_qsv", "h264_amf")


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(
            f"Command failed ({r.returncode}): {' '.join(cmd)}\n{r.stderr or r.stdout}"
        )

def _replace_file_atomic(tmp_path: Path, final_path: Path) -> None:
    """
    Replace final_path with tmp_path atomically when possible.

    Important: MP4s are often unreadable until ffmpeg finalizes the container
    ("moov atom"). Writing to a temporary path avoids leaving behind a
    corrupt-looking final deliverable if the process is interrupted.
    """
    final_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp_path.replace(final_path)
    except OSError:
        # Cross-device or Windows weirdness fallback.
        final_path.unlink(missing_ok=True)
        tmp_path.replace(final_path)


def _extract_video_audio_mono_wav(video: Path, out_wav: Path, *, sample_hz: int) -> None:
    _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_hz),
            str(out_wav),
        ]
    )


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    rate, data = wavfile.read(str(path))
    if data.ndim == 1:
        data = data[:, np.newaxis]
    if np.issubdtype(data.dtype, np.floating):
        y = np.clip(data.astype(np.float32), -1.0, 1.0)
    else:
        maxv = np.iinfo(data.dtype).max
        y = (data.astype(np.float32) / float(maxv)).clip(-1.0, 1.0)
    return y, int(rate)


def _to_mono(y: np.ndarray) -> np.ndarray:
    if y.ndim == 1:
        return y.astype(np.float32)
    return np.mean(y.astype(np.float32), axis=1)


def _resample_poly(y: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return y.astype(np.float32)
    g = math.gcd(sr_in, sr_out)
    up = sr_out // g
    down = sr_in // g
    return signal.resample_poly(y.astype(np.float64), up, down).astype(np.float32)


def _estimate_lag_samples(
    mono_a: np.ndarray,
    mono_b: np.ndarray,
    sr: int,
    analyze_seconds: float,
) -> tuple[int, float]:
    """Return lag (samples) where positive means b is delayed vs a."""
    max_samples = int(min(len(mono_a), len(mono_b), analyze_seconds * sr))
    if max_samples < sr // 2:
        raise ValueError("Not enough audio to analyze (need at least ~0.5s).")

    target_sr = min(8000, sr)
    a_ds = _resample_poly(mono_a[:max_samples], sr, target_sr)
    b_ds = _resample_poly(mono_b[:max_samples], sr, target_sr)
    a_ds -= np.mean(a_ds)
    b_ds -= np.mean(b_ds)
    if float(np.std(a_ds) * np.std(b_ds)) < 1e-12:
        raise ValueError("Audio appears silent in the analyzed window.")

    corr = signal.correlate(a_ds, b_ds, mode="full", method="fft")
    peak = int(np.argmax(corr))
    lag_ds = peak - (len(b_ds) - 1)
    lag_samples = -int(round(lag_ds * (sr / float(target_sr))))
    peak_strength = float(
        corr[peak] / (np.linalg.norm(a_ds) * np.linalg.norm(b_ds) + 1e-12)
    )
    return lag_samples, peak_strength


def _trim_av_reencode(
    inp: Path,
    out: Path,
    *,
    trim_sec: float,
    crf: int,
    audio_bitrate: str,
    downscale_1080p: bool,
    video_encoder: str,
    loudnorm_measured: object | None = None,
) -> str:
    # Apply trim first; optionally downscale video to 1080p max width for faster
    # renders / smaller uploads (useful for YouTube workflows).
    v_chain = f"trim=start={trim_sec},setpts=PTS-STARTPTS"
    if downscale_1080p:
        # Keep aspect ratio; never upscale. Height becomes even via -2.
        v_chain += ",scale=w='min(1920,iw)':h=-2:flags=lanczos"
    a_chain = f"atrim=start={trim_sec},asetpts=PTS-STARTPTS"
    if loudnorm_measured is not None:
        from harness_loudnorm import build_loudnorm_pass2_filter

        a_chain = f"{a_chain},{build_loudnorm_pass2_filter(loudnorm_measured)}"
    fc = f"[0:v]{v_chain}[v];[0:a]{a_chain}[a]"
    cmd_prefix = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-stats",
        "-i",
        str(inp),
        "-filter_complex",
        fc,
        "-map",
        "[v]",
        "-map",
        "[a]",
    ]
    attempts = [video_encoder]
    if video_encoder in HARDWARE_ENCODERS:
        attempts.append("libx264")

    for attempt_index, encoder in enumerate(attempts):
        cmd = list(cmd_prefix)
        cmd.extend(_video_encoder_args(encoder, crf))
        cmd.extend(["-c:a", "aac", "-b:a", audio_bitrate, str(out)])
        try:
            _run(cmd)
            return encoder
        except RuntimeError:
            out.unlink(missing_ok=True)
            if attempt_index + 1 >= len(attempts):
                raise
            print(
                f"Hardware encoder {encoder} failed for {inp.name}; "
                "falling back to libx264 for this and remaining cameras.",
                file=sys.stderr,
            )
    raise RuntimeError("No video encoder attempt was made.")


def _video_encoder_args(video_encoder: str, quality: int) -> list[str]:
    if video_encoder == "libx264":
        return ["-c:v", "libx264", "-preset", "fast", "-crf", str(quality)]
    if video_encoder == "h264_nvenc":
        return [
            "-c:v",
            "h264_nvenc",
            "-preset",
            "fast",
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-cq",
            str(quality),
            "-b:v",
            "0",
        ]
    if video_encoder == "h264_qsv":
        return [
            "-c:v",
            "h264_qsv",
            "-preset",
            "fast",
            "-global_quality",
            str(quality),
        ]
    if video_encoder == "h264_amf":
        return [
            "-c:v",
            "h264_amf",
            "-quality",
            "speed",
            "-rc",
            "qvbr",
            "-qvbr_quality_level",
            str(quality),
        ]
    raise ValueError(f"Unsupported video encoder: {video_encoder}")


def _encoder_is_usable(video_encoder: str) -> bool:
    with tempfile.TemporaryDirectory(prefix="multicam-encoder-test-") as td:
        output = Path(td) / "test.mp4"
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1280x720:r=24000/1001:d=0.1",
            "-frames:v",
            "1",
            *_video_encoder_args(video_encoder, 23),
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(output),
        ]
        try:
            _run(cmd)
        except RuntimeError:
            return False
        return output.is_file() and output.stat().st_size > 0


def _resolve_video_encoder(requested: str) -> str:
    if requested != "auto":
        return requested
    for candidate in HARDWARE_ENCODERS:
        if _encoder_is_usable(candidate):
            print(f"Video encoder: {candidate} (hardware)")
            return candidate
    print("No usable hardware H.264 encoder found; using libx264.", file=sys.stderr)
    return "libx264"


def _multicam_output_basename(inp: Path, *, prepped_names: bool, suffix: str) -> str:
    """Basename only (e.g. Foo-prepped.mp4)."""
    if prepped_names:
        if "-synced" in inp.stem:
            return inp.stem.replace("-synced", "-prepped") + ".mp4"
        return inp.stem + "-prepped.mp4"
    return inp.stem + suffix + inp.suffix


def _trim_av_streamcopy(inp: Path, out: Path, *, trim_sec: float) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-stats",
        "-ss",
        f"{trim_sec:.6f}",
        "-i",
        str(inp),
        "-c",
        "copy",
        str(out),
    ]
    _run(cmd)


def compute_trims(
    videos: list[Path],
    *,
    analyze_seconds: float,
    sample_hz: int,
    align_to: AlignTo,
) -> tuple[list[int], int, list[float], list[dict], str]:
    """
    Anchor = videos[0]. For i>0: lag_i = delay of file i's mono mix vs anchor
    (positive => file i is delayed vs Video 1).

    earliest: ref = argmin(lags), trim_i = lag_i - min(lags)  (ref trims 0)
    latest:   ref = argmax(lags), trim_i = max(lags) - lag_i  (ref trims 0)

    Returns (trims, ref_idx, peaks, meta_rows, align_to).
    """
    if len(videos) < 2:
        raise ValueError("Need at least two video files.")

    monos: list[np.ndarray] = []
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        for i, vp in enumerate(videos):
            w = tdir / f"mono_{i}.wav"
            _extract_video_audio_mono_wav(vp.resolve(), w, sample_hz=sample_hz)
            arr, sr = _read_wav(w)
            if sr != sample_hz:
                raise RuntimeError(f"Unexpected sample rate {sr} for {vp.name}")
            monos.append(_to_mono(arr) if arr.ndim > 1 else arr[:, 0])

    anchor = monos[0]
    lags: list[int] = [0]
    peaks: list[float] = [1.0]
    meta_rows: list[dict] = [
        {
            "index": 0,
            "lag_vs_anchor_samples": 0,
            "lag_vs_anchor_ms": 0.0,
            "correlation_peak": 1.0,
        }
    ]

    for i in range(1, len(videos)):
        lag, peak = _estimate_lag_samples(anchor, monos[i], sample_hz, analyze_seconds)
        lags.append(lag)
        peaks.append(peak)
        meta_rows.append(
            {
                "index": i,
                "lag_vs_anchor_samples": lag,
                "lag_vs_anchor_ms": lag * 1000.0 / sample_hz,
                "correlation_peak": peak,
            }
        )

    lag_arr = np.asarray(lags, dtype=np.int64)
    if align_to == "earliest":
        ref_idx = int(np.argmin(lag_arr))
        lag_ref = int(lag_arr[ref_idx])
        trims = [max(0, int(li) - lag_ref) for li in lags]
        role = (
            "Waveform reaches the common line-up first vs Video 1 (min lag vs anchor); "
            "others lose extra head before that line."
        )
    else:
        ref_idx = int(np.argmax(lag_arr))
        lag_ref = int(lag_arr[ref_idx])
        trims = [max(0, lag_ref - int(li)) for li in lags]
        role = (
            "Waveform is slowest vs Video 1 (max lag vs anchor); "
            "others lose extra head so all match this clip's line-up."
        )

    for i, row in enumerate(meta_rows):
        row["trim_samples"] = trims[i]
        row["trim_ms"] = trims[i] * 1000.0 / sample_hz
        row["reference"] = i == ref_idx
    meta_rows[ref_idx]["reference_role"] = role

    return trims, ref_idx, peaks, meta_rows, align_to


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "videos",
        nargs="+",
        type=Path,
        help="Two or more MP4s (same program waveform). Video 1 = lag anchor only.",
    )
    p.add_argument(
        "--align-to",
        choices=("earliest", "latest"),
        default="earliest",
        help="Common t=0: earliest=min lag vs Video 1 (default); latest=max lag vs Video 1.",
    )
    p.add_argument(
        "--prepped-names",
        action="store_true",
        help="Write outputs as <stem with -synced replaced by -prepped>.mp4 (video-sync "
        "final naming). Ignores --suffix when set.",
    )
    p.add_argument(
        "--suffix",
        type=str,
        default="-multicamaligned",
        help="Output basename = input stem + suffix + .mp4 (default: -multicamaligned). "
        "Ignored when --prepped-names is set.",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Write outputs here (default: same directory as each source).",
    )
    p.add_argument(
        "--analyze-seconds",
        type=float,
        default=300.0,
        help="Max duration from the start used for lag detection (default: 300).",
    )
    p.add_argument(
        "--sample-rate",
        type=int,
        default=48000,
        help="Internal mono rate for correlation (default: 48000).",
    )
    p.add_argument(
        "--stream-copy",
        action="store_true",
        help="Fast trim via -ss before input + stream copy (keyframe-aligned, not ms-exact).",
    )
    p.add_argument(
        "--crf",
        type=int,
        default=20,
        help="H.264 CRF when re-encoding (default: 20; good YouTube upload default).",
    )
    p.add_argument(
        "--video-encoder",
        choices=("auto", "libx264", *HARDWARE_ENCODERS),
        default="auto",
        help=(
            "H.264 encoder for frame-accurate trims (default: auto). Auto tries "
            "NVENC, QSV, then AMF, with runtime fallback to libx264."
        ),
    )
    p.add_argument(
        "--downscale-1080p",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="When re-encoding (default mode), downscale video to max width 1920 "
        "(preserve aspect; never upscale). Default: on. Ignored with --stream-copy "
        "and when output is stream-copied with no head trim.",
    )
    p.add_argument(
        "--audio-bitrate",
        type=str,
        default="192k",
        help="AAC bitrate when re-encoding (default: 192k).",
    )
    p.add_argument(
        "--loudnorm",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Two-pass -14 LUFS on prepped audio (default: on with --prepped-names). "
        "Re-encode path applies pass 2 during trim; stream-copy / no-trim copy "
        "paths loudnorm in place after write.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print trims only; do not write videos.",
    )
    p.add_argument(
        "--json-report",
        type=Path,
        default=None,
        help="Write trim metadata as JSON.",
    )
    args = p.parse_args()
    loudnorm_enabled = (
        bool(args.prepped_names) if args.loudnorm is None else bool(args.loudnorm)
    )

    videos = [v.resolve() for v in args.videos]
    for v in videos:
        if not v.is_file():
            print(f"Missing file: {v}", file=sys.stderr)
            return 2

    align_to: AlignTo = args.align_to  # type: ignore[assignment]

    try:
        trims, ref_idx, peaks, meta, _ = compute_trims(
            videos,
            analyze_seconds=args.analyze_seconds,
            sample_hz=args.sample_rate,
            align_to=align_to,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    sr = args.sample_rate
    mode_note = (
        "earliest: reference = min lag vs Video 1 (no trim on that file); "
        "each other file loses (lag_i - min_lag) of head so waveforms match."
        if align_to == "earliest"
        else "latest: reference = max lag vs Video 1 (no trim on that file); "
        "each other file loses (max_lag - lag_i) of head."
    )

    print("Multicam trim (waveform-only; per-file A/V unchanged except shared head cut).")
    print(f"  Anchor for measurement: {videos[0].name} (lag 0 vs itself)")
    print(f"  --align-to {align_to}: {mode_note}")
    print(f"  Reference clip (no head trim): {videos[ref_idx].name} (index {ref_idx})")
    for i, v in enumerate(videos):
        lead = " (reference)" if i == ref_idx else ""
        print(
            f"  [{i}] {v.name}: lag vs anchor {meta[i]['lag_vs_anchor_ms']:.2f} ms, "
            f"head trim {meta[i]['trim_ms']:.2f} ms{lead}, corr={peaks[i]:.4f}"
        )

    report = {
        "anchor_index": 0,
        "anchor_file": str(videos[0]),
        "align_to": align_to,
        "prepped_names": bool(args.prepped_names),
        "loudnorm": loudnorm_enabled,
        "loudnorm_target_i_lufs": -14.0 if loudnorm_enabled else None,
        "reference_index": ref_idx,
        "reference_file": str(videos[ref_idx]),
        "reference_role": meta[ref_idx].get("reference_role", ""),
        "sample_rate_hz": sr,
        "files": meta,
    }

    for i, v in enumerate(videos):
        bn = _multicam_output_basename(
            v, prepped_names=args.prepped_names, suffix=args.suffix
        )
        od = args.out_dir if args.out_dir is not None else v.parent
        meta[i]["output_path"] = str((od / bn).resolve())

    if args.dry_run:
        if args.json_report:
            args.json_report.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"Wrote {args.json_report}")
        return 0

    active_video_encoder = (
        "copy"
        if args.stream_copy
        else _resolve_video_encoder(args.video_encoder)
    )
    report["video_encoder_requested"] = args.video_encoder
    report["video_encoder_initial"] = active_video_encoder

    for i, v in enumerate(videos):
        trim_sec = trims[i] / float(sr)
        out_basename = _multicam_output_basename(
            v, prepped_names=args.prepped_names, suffix=args.suffix
        )
        out_dir = args.out_dir if args.out_dir is not None else v.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = (out_dir / out_basename).resolve()
        # Suffix must stay recognized by ffmpeg muxer (e.g. .mp4), not ".mp4.partial".
        tmp_path = out_path.with_name(f"{out_path.stem}.partial{out_path.suffix}")
        loudnorm_measured = None
        loudnorm_applied_during_encode = False

        if loudnorm_enabled:
            from harness_loudnorm import (
                PREPPED_TARGET_I_LUFS,
                measure_loudnorm,
                measure_loudnorm_trimmed,
                normalize_prepped_media_inplace,
            )

        if trim_sec <= 0.0:
            if out_path == v:
                print(f"  [{i}] skip (no trim, output would overwrite input)", file=sys.stderr)
                continue
            try:
                _run(
                    [
                        "ffmpeg",
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "warning",
                        "-stats",
                        "-i",
                        str(v),
                        "-c",
                        "copy",
                        str(tmp_path),
                    ]
                )
                _replace_file_atomic(tmp_path, out_path)
                if loudnorm_enabled:
                    loudnorm_measured = normalize_prepped_media_inplace(out_path)
            finally:
                tmp_path.unlink(missing_ok=True)
        elif args.stream_copy:
            try:
                _trim_av_streamcopy(v, tmp_path, trim_sec=trim_sec)
                _replace_file_atomic(tmp_path, out_path)
                if loudnorm_enabled:
                    loudnorm_measured = normalize_prepped_media_inplace(
                        out_path,
                    )
            finally:
                tmp_path.unlink(missing_ok=True)
        else:
            try:
                if loudnorm_enabled:
                    loudnorm_measured = (
                        measure_loudnorm_trimmed(v, trim_sec)
                        if trim_sec > 0.0
                        else measure_loudnorm(v)
                    )
                active_video_encoder = _trim_av_reencode(
                    v,
                    tmp_path,
                    trim_sec=trim_sec,
                    crf=args.crf,
                    audio_bitrate=args.audio_bitrate,
                    downscale_1080p=bool(args.downscale_1080p),
                    video_encoder=active_video_encoder,
                    loudnorm_measured=loudnorm_measured,
                )
                loudnorm_applied_during_encode = loudnorm_enabled
                meta[i]["video_encoder"] = active_video_encoder
                _replace_file_atomic(tmp_path, out_path)
            finally:
                tmp_path.unlink(missing_ok=True)
        if loudnorm_enabled and loudnorm_measured is not None:
            meta[i]["loudnorm_input_i_lufs"] = loudnorm_measured.input_i
            meta[i]["loudnorm_applied_during_encode"] = loudnorm_applied_during_encode
            print(
                f"  Loudnorm {out_path.name}: input_i={loudnorm_measured.input_i:.1f} LUFS "
                f"-> target {PREPPED_TARGET_I_LUFS:.1f} LUFS "
                f"({'during encode' if loudnorm_applied_during_encode else 'post copy'})",
                file=sys.stderr,
            )
        print(f"Wrote {out_path}")

    report["video_encoder_final"] = active_video_encoder
    if args.json_report:
        args.json_report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"JSON report: {args.json_report}")

    if args.stream_copy:
        print(
            "Note: --stream-copy trims are keyframe-approximate; "
            "omit it for sample-accurate trims (re-encode).",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
