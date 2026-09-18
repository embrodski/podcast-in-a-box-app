#!/usr/bin/env python3
"""
Align an external WAV (e.g. cleaned conversation mix) to a video's embedded
audio via cross-correlation, then mux video + aligned replacement audio.

Video is the reference timeline (lip-sync / picture clock). Positive lag means
the external file is delayed vs the video's audio; we skip the start of the
external file. Negative lag prepends silence before the external audio.

When correlation peak strength is below ``--min-correlation-strength`` (default
0.35), or when ``--assume-start-aligned`` is set, the external WAV is muxed at
sample 0 with no lag shift (MultiCorder-style shared record start).

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

from win_hidden_console import install_hidden_console

install_hidden_console()

import numpy as np
from scipy import signal
from scipy.io import wavfile

DEFAULT_MIN_CORRELATION_STRENGTH = 0.35


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(
            f"Command failed ({r.returncode}): {' '.join(cmd)}\n{r.stderr or r.stdout}"
        )


def _ffprobe_duration(path: Path) -> float:
    r = subprocess.run(
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
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}:\n{r.stderr.strip()}")
    s = (r.stdout or "").strip()
    if not s:
        raise RuntimeError(f"ffprobe returned no duration for {path}")
    return float(s)


def _ffmpeg_decode_to_wav(src: Path, out_wav: Path) -> None:
    """Decode any ffmpeg-readable audio to PCM WAV for scipy.io.wavfile."""
    _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src),
            str(out_wav),
        ]
    )


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


def _write_wav(path: Path, y: np.ndarray, rate: int) -> None:
    y = np.clip(y, -1.0, 1.0)
    pcm = np.round(y * 32767.0).astype(np.int16)
    wavfile.write(str(path), rate, pcm)


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


def _estimate_lag_at_window(
    mono_a: np.ndarray,
    mono_b: np.ndarray,
    sr: int,
    start: int,
    length: int,
) -> int:
    end = start + length
    sa = mono_a[start:end]
    sb = mono_b[start:end]
    if len(sa) < length // 2 or len(sb) < length // 2:
        return 0
    lag, _ = _estimate_lag_samples(sa, sb, sr, analyze_seconds=length / float(sr))
    return lag


def _drift_ms(
    mono_a: np.ndarray,
    mono_b: np.ndarray,
    sr: int,
    *,
    analyze_seconds: float,
    lag_start: int,
) -> tuple[float, int]:
    if len(mono_a) <= int(2.5 * analyze_seconds * sr):
        return 0.0, lag_start
    w = int(min(analyze_seconds * sr, len(mono_a) * 0.25))
    start2 = max(0, len(mono_a) - w)
    lag2 = _estimate_lag_at_window(mono_a, mono_b, sr, start2, w)
    drift = abs(lag2 - lag_start) / float(sr) * 1000.0
    return drift, lag2


def _shift_external_to_lag(
    ext_rs: np.ndarray,
    lag: int,
    n_ref: int,
) -> np.ndarray:
    if lag >= 0:
        ext_adj = ext_rs[lag:]
    else:
        pad = np.zeros((-lag, ext_rs.shape[1]), dtype=np.float32)
        ext_adj = np.vstack([pad, ext_rs])

    if len(ext_adj) < n_ref:
        pad_end = np.zeros((n_ref - len(ext_adj), ext_adj.shape[1]), dtype=np.float32)
        ext_adj = np.vstack([ext_adj, pad_end])
    else:
        ext_adj = ext_adj[:n_ref]
    return ext_adj


def _align_external_to_reference(
    ref_mono: np.ndarray,
    ref_sr: int,
    external: np.ndarray,
    ext_sr: int,
    *,
    analyze_seconds: float,
    min_correlation_strength: float = DEFAULT_MIN_CORRELATION_STRENGTH,
    assume_start_aligned: bool = False,
) -> tuple[np.ndarray, dict]:
    """
    Resample external to ref_sr, estimate lag on mono, return external (full
    channels) resampled to ref_sr with leading trim/pad so it aligns to ref_mono
    from sample 0. Length matches ref_mono (crop or pad end with zeros).
    """
    report: dict = {}
    ext = external
    if ext.ndim == 1:
        ext = ext[:, np.newaxis]
    if ext.shape[1] >= 1:
        chans = []
        for c in range(ext.shape[1]):
            chans.append(_resample_poly(ext[:, c].astype(np.float64), ext_sr, ref_sr))
        ext_rs = np.stack(chans, axis=1).astype(np.float32)
    else:
        raise ValueError("external audio has no channels")

    mono_b = _to_mono(ext_rs)
    detected_lag, strength = _estimate_lag_samples(
        ref_mono, mono_b, ref_sr, analyze_seconds
    )
    report["correlation_lag_samples"] = detected_lag
    report["correlation_lag_ms"] = detected_lag / float(ref_sr) * 1000.0
    report["correlation_peak_strength"] = strength
    report["reference_sample_rate_hz"] = ref_sr
    report["external_original_rate_hz"] = ext_sr

    use_start_aligned = assume_start_aligned
    if not use_start_aligned and strength < min_correlation_strength:
        use_start_aligned = True
        report["start_aligned_fallback"] = True
        report["start_aligned_reason"] = (
            f"correlation peak {strength:.4f} below threshold "
            f"{min_correlation_strength:.4f}"
        )
    elif assume_start_aligned:
        report["start_aligned_fallback"] = True
        report["start_aligned_reason"] = "--assume-start-aligned"

    applied_lag = 0 if use_start_aligned else detected_lag
    report["lag_samples_external_delayed_vs_video_audio"] = applied_lag
    report["lag_ms"] = applied_lag / float(ref_sr) * 1000.0
    report["start_aligned"] = use_start_aligned

    drift, lag_end = _drift_ms(
        ref_mono,
        mono_b,
        ref_sr,
        analyze_seconds=analyze_seconds,
        lag_start=detected_lag,
    )
    report["drift_estimate_ms"] = drift
    report["lag_samples_end_window"] = lag_end
    if drift > 25.0 and not use_start_aligned:
        report["drift_warning"] = (
            "Start vs end window offset differs by more than ~25 ms. "
            "Clock drift or heavy edits may leave residual lip-sync error; "
            "try a shorter clip or specialized time-warp tooling."
        )

    n_ref = len(ref_mono)
    ext_adj = _shift_external_to_lag(ext_rs, applied_lag, n_ref)

    report["output_audio_samples"] = int(ext_adj.shape[0])
    report["reference_audio_samples"] = n_ref
    return ext_adj, report


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("video", type=Path, help="Input video (any format ffmpeg reads).")
    p.add_argument(
        "external_audio",
        type=Path,
        help="Replacement audio (WAV preferred; use ffmpeg-readable formats by converting first).",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Output MP4 (re-encoded AAC audio, video stream copy).",
    )
    p.add_argument(
        "--analyze-seconds",
        type=float,
        default=300.0,
        help="Max duration from the start used for lag detection (default: 300).",
    )
    p.add_argument(
        "--ref-sample-rate",
        type=int,
        default=48000,
        help="Sample rate for extracted video audio used in correlation (default: 48000).",
    )
    p.add_argument(
        "--audio-bitrate",
        type=str,
        default="192k",
        help="AAC bitrate for output (default: 192k).",
    )
    p.add_argument(
        "--json-report",
        type=Path,
        default=None,
        help="Write alignment metadata as JSON.",
    )
    p.add_argument(
        "--min-correlation-strength",
        type=float,
        default=DEFAULT_MIN_CORRELATION_STRENGTH,
        help=(
            "When correlation peak strength is below this value, mux external "
            "audio at sample 0 with no lag shift (default: 0.35)."
        ),
    )
    p.add_argument(
        "--assume-start-aligned",
        action="store_true",
        help=(
            "Skip lag correction; mux external audio starting at sample 0 "
            "(same as low-correlation fallback)."
        ),
    )
    args = p.parse_args()

    if not args.video.is_file():
        print(f"Video not found: {args.video}", file=sys.stderr)
        return 2
    if not args.external_audio.is_file():
        print(f"Audio not found: {args.external_audio}", file=sys.stderr)
        return 2

    try:
        duration = _ffprobe_duration(args.video.resolve())
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    ref_sr = int(args.ref_sample_rate)
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        ref_wav = tdir / "ref_video_audio.wav"
        ext_wav = tdir / "external.wav"
        aligned_wav = tdir / "aligned_replace.wav"

        try:
            _extract_video_audio_mono_wav(
                args.video.resolve(), ref_wav, sample_hz=ref_sr
            )
        except Exception as e:
            print(f"Error extracting video audio: {e}", file=sys.stderr)
            return 1

        ref_stereo, _ = _read_wav(ref_wav)
        if ref_stereo.shape[1] == 1:
            ref_mono = ref_stereo[:, 0]
        else:
            ref_mono = _to_mono(ref_stereo)

        ext_path = args.external_audio.resolve()
        if ext_path.suffix.lower() != ".wav":
            try:
                _ffmpeg_decode_to_wav(ext_path, ext_wav)
            except Exception as e:
                print(f"Error decoding external audio: {e}", file=sys.stderr)
                return 1
            ext_load = ext_wav
        else:
            ext_load = ext_path

        try:
            ext, ext_sr = _read_wav(ext_load)
        except Exception as e:
            print(f"Error reading external audio: {e}", file=sys.stderr)
            return 1

        try:
            aligned, report = _align_external_to_reference(
                ref_mono,
                ref_sr,
                ext,
                ext_sr,
                analyze_seconds=args.analyze_seconds,
                min_correlation_strength=float(args.min_correlation_strength),
                assume_start_aligned=bool(args.assume_start_aligned),
            )
        except Exception as e:
            print(f"Alignment failed: {e}", file=sys.stderr)
            return 1

        _write_wav(aligned_wav, aligned, ref_sr)

        args.output.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(args.video.resolve()),
            "-i",
            str(aligned_wav),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            args.audio_bitrate,
            "-shortest",
            str(args.output.resolve()),
        ]
        try:
            _run(cmd)
        except Exception as e:
            print(f"Mux failed: {e}", file=sys.stderr)
            return 1

    report["video_duration_sec"] = duration
    report["video_path"] = str(args.video.resolve())
    report["external_audio_path"] = str(args.external_audio.resolve())
    report["output_path"] = str(args.output.resolve())
    report["mux_sample_rate_hz"] = ref_sr

    print(f"Wrote {args.output}")
    print(
        f"  Lag (external delayed vs video audio): {report['lag_ms']:.2f} ms "
        f"({report['lag_samples_external_delayed_vs_video_audio']} samples @ {ref_sr} Hz)"
    )
    print(f"  Correlation peak strength: {report['correlation_peak_strength']:.4f}")
    if report.get("correlation_lag_ms") is not None and report.get("start_aligned"):
        print(
            f"  Detected lag (not applied): {report['correlation_lag_ms']:.2f} ms"
        )
    if report.get("start_aligned_fallback"):
        print(f"  Start-aligned mux: {report.get('start_aligned_reason', 'yes')}")
    if report.get("drift_estimate_ms", 0) > 0:
        print(f"  Drift estimate (start vs end): {report['drift_estimate_ms']:.2f} ms")
    if report.get("drift_warning"):
        print(f"  Caveat: {report['drift_warning']}")

    if args.json_report:
        args.json_report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"  JSON report: {args.json_report}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
