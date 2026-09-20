#!/usr/bin/env python3
"""Find transcript-bounded mono audio zero-crossings in a media file."""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from array import array
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1


class SeamError(RuntimeError):
    """Raised for an actionable input or analysis error."""


def _number(value: Any, *names: str) -> float:
    for name in names:
        if isinstance(value, dict) and value.get(name) is not None:
            return float(value[name])
    raise SeamError(f"Missing numeric field; expected one of: {', '.join(names)}")


def _word_text(value: Any) -> str:
    if not isinstance(value, dict):
        raise SeamError("Transcript word entries must be JSON objects.")
    text = value.get("text", value.get("word"))
    if text is None:
        raise SeamError("Transcript word is missing 'text' or 'word'.")
    return str(text)


def _rows_from_transcript(data: Any) -> Iterable[dict[str, Any]]:
    if isinstance(data, dict) and isinstance(data.get("segments"), list):
        return data["segments"]
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        rows = [value for value in data.values() if isinstance(value, dict)]
        if rows and any(isinstance(row.get("words"), list) for row in rows):
            return rows
    raise SeamError(
        "Unsupported transcript JSON. Expected top-level segments[], a list of "
        "rows, or a row dictionary; rows must contain words[]."
    )


def load_words(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SeamError(f"Could not read transcript JSON {path}: {exc}") from exc

    flattened: list[dict[str, Any]] = []
    for row in _rows_from_transcript(data):
        row_words = row.get("words")
        if not isinstance(row_words, list):
            continue
        for item in row_words:
            text = _word_text(item)
            if not text.strip():
                continue
            start = _number(item, "start_time", "start")
            end = _number(item, "end_time", "end")
            if end < start:
                raise SeamError(f"Word has end before start: {text!r}")
            flattened.append({"text": text, "start": start, "end": end})

    if not flattened:
        raise SeamError(f"No word-timed entries found in transcript {path}.")
    flattened.sort(key=lambda word: (word["start"], word["end"]))
    return flattened


def tokenize(text: str, *, case_sensitive: bool) -> list[str]:
    if not case_sensitive:
        text = text.casefold()
    return [token for token in re.findall(r"[^\W_]+(?:['’][^\W_]+)*", text) if token]


def normalized_word(text: str, *, case_sensitive: bool) -> str:
    tokens = tokenize(text, case_sensitive=case_sensitive)
    return tokens[0] if tokens else ""


def find_markers(
    words: list[dict[str, Any]],
    markers: list[str],
    occurrences: list[int],
    *,
    case_sensitive: bool,
) -> list[dict[str, Any]]:
    normalized = [
        normalized_word(word["text"], case_sensitive=case_sensitive) for word in words
    ]
    selected: list[dict[str, Any]] = []
    cursor = 0

    for marker, occurrence in zip(markers, occurrences):
        marker_tokens = tokenize(marker, case_sensitive=case_sensitive)
        if not marker_tokens:
            raise SeamError(f"Marker contains no searchable words: {marker!r}")

        matches: list[int] = []
        width = len(marker_tokens)
        for index in range(cursor, len(words) - width + 1):
            if normalized[index : index + width] == marker_tokens:
                matches.append(index)

        if len(matches) < occurrence:
            raise SeamError(
                f"Marker {marker!r}: requested occurrence {occurrence} after word "
                f"index {cursor}, but found {len(matches)}."
            )

        index = matches[occurrence - 1]
        if index == 0:
            raise SeamError(
                f"Marker {marker!r} is the transcript's first word, so it has no "
                "preceding word and cannot define a seam."
            )
        selected.append(
            {
                "marker": marker,
                "occurrence": occurrence,
                "word_index": index,
                "additional_matches_after_selection": len(matches) - occurrence,
            }
        )
        cursor = index + width

    for position, match in enumerate(selected):
        marker_tokens = tokenize(
            match["marker"], case_sensitive=case_sensitive
        )
        scan_start = match["word_index"] + len(marker_tokens)
        scan_end = (
            selected[position + 1]["word_index"]
            if position + 1 < len(selected)
            else len(words)
        )
        duplicates = 0
        for index in range(scan_start, scan_end - len(marker_tokens) + 1):
            if normalized[index : index + len(marker_tokens)] == marker_tokens:
                duplicates += 1
        match["additional_matches_before_next_marker"] = duplicates
    return selected


def probe_audio(media: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise SeamError("ffprobe is required but was not found on PATH.")
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=sample_rate,channels,channel_layout,start_time,duration",
        "-of",
        "json",
        str(media),
    ]
    try:
        completed = subprocess.run(
            command, check=True, capture_output=True, text=True
        )
        streams = json.loads(completed.stdout).get("streams", [])
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise SeamError(f"ffprobe failed for {media}: {detail.strip()}") from exc
    if not streams:
        raise SeamError(f"No audio stream found in {media}.")
    stream = streams[0]
    return {
        "sample_rate": int(stream["sample_rate"]),
        "channels": int(stream.get("channels", 0)),
        "channel_layout": stream.get("channel_layout"),
        "start_time": float(stream.get("start_time") or 0.0),
        "duration": (
            float(stream["duration"]) if stream.get("duration") is not None else None
        ),
    }


def decode_mono_wav(media: Path, destination: Path, sample_rate: int) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SeamError("ffmpeg is required but was not found on PATH.")
    command = [
        ffmpeg,
        "-v",
        "error",
        "-y",
        "-i",
        str(media),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        str(destination),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise SeamError(f"ffmpeg audio decode failed: {exc.stderr.strip()}") from exc


def _timecode(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


def find_zero_crossing(
    wav_file: wave.Wave_read,
    *,
    window_start: float,
    window_end: float,
    audio_start: float,
) -> dict[str, Any]:
    sample_rate = wav_file.getframerate()
    frame_count = wav_file.getnframes()
    midpoint = (window_start + window_end) / 2.0
    first_index = max(1, math.floor((window_start - audio_start) * sample_rate))
    final_index = min(
        frame_count - 1, math.ceil((window_end - audio_start) * sample_rate)
    )
    if final_index < first_index:
        raise SeamError(
            f"Seam window {window_start:.6f}–{window_end:.6f}s lies outside audio."
        )

    read_start = first_index - 1
    wav_file.setpos(read_start)
    values = array("h")
    values.frombytes(wav_file.readframes(final_index - read_start + 1))
    if sys.byteorder != "little":
        values.byteswap()

    candidates: list[float] = []
    for local_index in range(1, len(values)):
        absolute_index = read_start + local_index
        previous = values[local_index - 1]
        current = values[local_index]
        if current == 0:
            candidates.append(audio_start + absolute_index / sample_rate)
        elif (previous < 0 < current) or (previous > 0 > current):
            fraction = abs(previous) / (abs(previous) + abs(current))
            candidates.append(
                audio_start + (absolute_index - 1 + fraction) / sample_rate
            )

    if candidates:
        seam = min(candidates, key=lambda value: abs(value - midpoint))
        method = "zero-crossing"
    else:
        local_index = min(
            range(1, len(values)),
            key=lambda index: (
                abs(values[index]),
                abs(audio_start + (read_start + index) / sample_rate - midpoint),
            ),
        )
        seam = audio_start + (read_start + local_index) / sample_rate
        method = "minimum-absolute-sample"

    return {
        "time_seconds": seam,
        "time_milliseconds": int(round(seam * 1000)),
        "timecode": _timecode(seam),
        "method": method,
        "midpoint_seconds": midpoint,
        "offset_from_midpoint_ms": (seam - midpoint) * 1000,
        "zero_crossing_count": len(candidates),
    }


def analyze(
    media: Path,
    transcript: Path,
    markers: list[str],
    occurrences: list[int],
    section_names: list[str],
    *,
    context_words: int,
    case_sensitive: bool,
) -> dict[str, Any]:
    if not media.is_file():
        raise SeamError(f"Media file does not exist: {media}")
    if not transcript.is_file():
        raise SeamError(f"Transcript file does not exist: {transcript}")

    words = load_words(transcript)
    selected = find_markers(
        words, markers, occurrences, case_sensitive=case_sensitive
    )
    audio = probe_audio(media)

    with tempfile.TemporaryDirectory(prefix="find-seam-") as temp_dir:
        mono_wav = Path(temp_dir) / "mono.wav"
        decode_mono_wav(media, mono_wav, audio["sample_rate"])
        with wave.open(str(mono_wav), "rb") as wav_file:
            if wav_file.getnchannels() != 1 or wav_file.getsampwidth() != 2:
                raise SeamError("Internal decode did not produce 16-bit mono PCM.")
            seams: list[dict[str, Any]] = []
            for seam_number, match in enumerate(selected, start=1):
                index = match["word_index"]
                previous = words[index - 1]
                following = words[index]
                if following["start"] < previous["end"]:
                    raise SeamError(
                        f"Words overlap at marker {match['marker']!r}: "
                        f"{previous['text']!r} ends {previous['end']:.6f}s, "
                        f"{following['text']!r} starts {following['start']:.6f}s."
                    )
                crossing = find_zero_crossing(
                    wav_file,
                    window_start=previous["end"],
                    window_end=following["start"],
                    audio_start=audio["start_time"],
                )
                seams.append(
                    {
                        "number": seam_number,
                        "boundary": (
                            f"{section_names[seam_number - 1]} -> "
                            f"{section_names[seam_number]}"
                        ),
                        **crossing,
                        "window": {
                            "start_seconds": previous["end"],
                            "end_seconds": following["start"],
                        },
                        "last_word": previous,
                        "first_word": following,
                        "words_before": [
                            item["text"]
                            for item in words[max(0, index - context_words) : index]
                        ],
                        "words_after": [
                            item["text"]
                            for item in words[index : index + context_words]
                        ],
                        "marker": match,
                    }
                )

    return {
        "schema_version": SCHEMA_VERSION,
        "media": str(media.resolve()),
        "transcript": str(transcript.resolve()),
        "analysis": {
            "channel_mode": "mono",
            "source_channels": audio["channels"],
            "source_channel_layout": audio["channel_layout"],
            "sample_rate_hz": audio["sample_rate"],
            "audio_start_time_seconds": audio["start_time"],
            "selection_rule": (
                "Nearest mono zero-crossing to the midpoint between the prior "
                "word end and following marker word start; minimum absolute "
                "sample fallback when no crossing exists."
            ),
        },
        "seams": seams,
    }


def markdown_report(result: dict[str, Any]) -> str:
    lines = [
        "# Seam report",
        "",
        f"- Media: `{result['media']}`",
        f"- Transcript: `{result['transcript']}`",
        f"- Audio: {result['analysis']['sample_rate_hz']} Hz mono analysis",
        "",
        "## Seams",
        "",
    ]
    for seam in result["seams"]:
        before = " ".join(seam["words_before"])
        after = " ".join(seam["words_after"])
        lines.extend(
            [
                f"### {seam['number']}. {seam['boundary']} — `{seam['timecode']}`",
                "",
                (
                    f"- Window: {seam['window']['start_seconds']:.3f}s–"
                    f"{seam['window']['end_seconds']:.3f}s"
                ),
                f"- Method: {seam['method']}",
                (
                    "- Midpoint offset: "
                    f"{seam['offset_from_midpoint_ms']:+.3f} ms"
                ),
                f"- Before: {before}",
                f"- After: {after}",
                "",
            ]
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Find mono audio zero-crossings between transcript words at section "
            "markers."
        )
    )
    parser.add_argument("--media", required=True, type=Path)
    parser.add_argument("--transcript", required=True, type=Path)
    parser.add_argument(
        "--markers",
        required=True,
        nargs="+",
        help="Ordered first-word or phrase markers for sections after the first.",
    )
    parser.add_argument(
        "--marker-occurrences",
        nargs="+",
        type=int,
        help="1-based occurrence to select for each marker after the prior marker.",
    )
    parser.add_argument(
        "--section-names",
        nargs="+",
        help="Names for all sections; must contain one more item than --markers.",
    )
    parser.add_argument("--context-words", type=int, default=10)
    parser.add_argument("--case-sensitive", action="store_true")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-markdown", type=Path)
    parser.add_argument(
        "--allow-overwrite",
        action="store_true",
        help="Allow replacing existing JSON or Markdown report files.",
    )
    parser.add_argument("--json-stdout", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.context_words < 1:
        parser.error("--context-words must be at least 1")

    occurrences = args.marker_occurrences or [1] * len(args.markers)
    if len(occurrences) != len(args.markers) or any(value < 1 for value in occurrences):
        parser.error(
            "--marker-occurrences must contain one positive integer per marker"
        )
    section_names = args.section_names or [
        "Section 1",
        *[f"Section {index}" for index in range(2, len(args.markers) + 2)],
    ]
    if len(section_names) != len(args.markers) + 1:
        parser.error("--section-names must contain exactly len(markers) + 1 names")
    existing_outputs = [
        path
        for path in (args.output_json, args.output_markdown)
        if path is not None and path.exists()
    ]
    if existing_outputs and not args.allow_overwrite:
        parser.error(
            "refusing to overwrite existing report(s): "
            + ", ".join(str(path) for path in existing_outputs)
            + "; choose new paths or pass --allow-overwrite after approval"
        )

    try:
        result = analyze(
            args.media,
            args.transcript,
            args.markers,
            occurrences,
            section_names,
            context_words=args.context_words,
            case_sensitive=args.case_sensitive,
        )
    except SeamError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    rendered_json = json.dumps(result, indent=2, ensure_ascii=False)
    rendered_markdown = markdown_report(result)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(rendered_json + "\n", encoding="utf-8")
    if args.output_markdown:
        args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.output_markdown.write_text(rendered_markdown + "\n", encoding="utf-8")

    if args.json_stdout:
        print(rendered_json)
    else:
        for seam in result["seams"]:
            print(
                f"{seam['boundary']}: {seam['timecode']} "
                f"({seam['method']}, midpoint "
                f"{seam['offset_from_midpoint_ms']:+.3f} ms)"
            )
            duplicate_count = seam["marker"][
                "additional_matches_before_next_marker"
            ]
            if duplicate_count:
                print(
                    f"  WARNING: marker {seam['marker']['marker']!r} repeats "
                    f"{duplicate_count} time(s) before the next marker."
                )
        if args.output_json:
            print(f"JSON: {args.output_json.resolve()}")
        if args.output_markdown:
            print(f"Markdown: {args.output_markdown.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
