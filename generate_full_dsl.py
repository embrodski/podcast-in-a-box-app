#!/usr/bin/env python3
"""
Generate a full sequential DSL file from a simplified transcript JSON.

Example:
  python generate_full_dsl.py Wide_Video_Interview_Audio_Copy_eng_simplified.json \
      --segment 1 \
      --output podcast_sequences/interview_full.dsl

By default this generator:
- Outputs one `$segmentN/id` line per transcript row
- Adds `!camera ...` commands based on `speaker_id` (0 -> speaker_0, 1 -> speaker_1)
- **Open on Ben:** for the first `--open-ben-sec` seconds (default 3), every row overlapping
  `[0, open)` is forced to `speaker_0` so there is no cut off Ben during that window
  (row-aligned).
- **Close on Ben:** for the last `--tail-ben-sec` seconds (default 4) of the timeline
  (including `--final-shot-tail-sec` past the last transcript word), every overlapping row
  is forced to `speaker_0` (row-aligned; the first row that intersects that tail window may
  start earlier, so Ben may begin slightly before the exact cut time if there is no row
  boundary at T - tail).
- Applies the core rule: **dense cuts → force wide**
  If there would be more than one camera cut in any rolling window (default 3 seconds), it
  replaces that region with a single `!camera wide` span (sentence-aligned, at least
  `--min-wide-sec`), extending the wide span if another cut would happen within the window.
  Wide spans are trimmed so they never cover the open-Ben or tail-Ben windows.

Use `--no-cameras` to reproduce the legacy behavior (no `!camera` lines, no wide rule).
"""

import argparse
import json
import os
import re
import struct
import subprocess
import sys
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Tuple

_SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from podcast_phrase_gates import apply_namespace_phrase_defaults


CAM_BY_SPEAKER_ID = {
    0: "speaker_0",
    1: "speaker_1",
}

_INTERJECTION_CANONICAL = {
    # Keep this conservative: only short backchannels that should not flip the shot by themselves.
    "mm-hmm",
    "mhm",
    "uh-huh",
    "uh huh",
    "huh",
    "yeah",
    "yep",
    "right",
    "ok",
    "okay",
}

# If a transcript row is <= this duration and matches an interjection above, treat it as a
# "brief interjection" for camera-switch purposes.
_BRIEF_INTERJECTION_MAX_SEC = 0.85

_NORMALIZE_TEXT_RE = re.compile(r"[^a-z0-9\s\-]+")


@dataclass(frozen=True)
class WordToken:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Row:
    idx: int
    start: float
    end: float
    text: str
    speaker_id: int
    speaker_name: str
    words: Tuple[WordToken, ...] = ()


@dataclass(frozen=True)
class FlatWord:
    row_i: int
    word_i: int
    token: str
    start: float
    end: float


@dataclass(frozen=True)
class StartPhraseCut:
    rows: List[Row]
    first_slice_start: Optional[float]
    content_start_abs: float
    matched_phrase: str
    next_word_text: str
    host_speaker_id: int
    opening_sec: float = 0.0
    opening_from_quietest: bool = False


@dataclass(frozen=True)
class EndPhraseCut:
    rows: List[Row]
    last_slice_end: Optional[float]
    content_end_abs: float
    matched_phrase: str
    last_word_text: str
    end_from_quietest: bool = False


@dataclass
class Piece:
    """One emitted clip derived from a transcript row (possibly sliced)."""

    row: Row
    slice_start: Optional[float] = None
    slice_end: Optional[float] = None
    force_cam: Optional[str] = None
    seam_after_pause: bool = False
    resume_abs: Optional[float] = None


@dataclass(frozen=True)
class PausePair:
    pause_start_i: int
    pause_end_i: int  # exclusive flat index
    unpause_start_i: int
    unpause_end_i: int  # exclusive
    pause_phrase: str
    unpause_phrase: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a sequential DSL file from a simplified transcript."
    )
    parser.add_argument("transcript_json", help="Path to simplified transcript JSON")
    parser.add_argument(
        "--segment",
        required=True,
        help="Segment number to use in generated DSL entries, e.g. 1",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to the output DSL file",
    )
    parser.add_argument(
        "--max-start",
        type=float,
        default=None,
        help="Include only transcript rows with start_time < seconds",
    )
    parser.add_argument(
        "--no-cameras",
        action="store_true",
        help="Do not emit !camera lines (legacy output). Disables dense-cuts-wide too.",
    )
    parser.add_argument(
        "--wide-camera",
        default="wide",
        help='Camera name for forced wide spans (default: "wide")',
    )
    parser.add_argument(
        "--cut-window-sec",
        type=float,
        default=3.0,
        help="Rolling window size in seconds for dense-cuts-wide (default: 3.0)",
    )
    parser.add_argument(
        "--min-wide-sec",
        type=float,
        default=3.0,
        help="Minimum wide span duration in seconds (default: 3.0)",
    )
    parser.add_argument(
        "--camera-switch-offset-ms",
        type=float,
        default=-250.0,
        help="Shift ALL camera switch boundaries by this many milliseconds. "
             "Negative = switch earlier, positive = switch later. Implemented by splitting "
             "the adjacent transcript row and assigning the moved slice to the other camera "
             "(sentence-aligned overall order is preserved). Default: -250. Use 0 or "
             "--no-camera-switch-offset to disable.",
    )
    parser.add_argument(
        "--no-camera-switch-offset",
        action="store_true",
        help="Do not shift camera switch boundaries (overrides --camera-switch-offset-ms).",
    )
    parser.add_argument(
        "--final-shot-tail-sec",
        type=float,
        default=2.0,
        help="Extend the final shot this many seconds past the last word if possible "
             "(default: 2.0). If media ends sooner, the renderer will naturally stop at EOF.",
    )
    parser.add_argument(
        "--open-ben-sec",
        type=float,
        default=3.0,
        help="Force speaker_0 for every transcript row overlapping [0, N) seconds on the "
             "timeline (default N=3.0). Set to 0 to disable.",
    )
    parser.add_argument(
        "--tail-ben-sec",
        type=float,
        default=4.0,
        help="Force speaker_0 for rows overlapping the last this many seconds of the "
             "timeline (uses --final-shot-tail-sec for end time). Default: 4.0. Set to 0 "
             "to disable.",
    )
    parser.add_argument(
        "--speaker-2-to-wide",
        action="store_true",
        help="Map speaker_id 2 to the wide camera (--wide-camera, default wide). "
             "Use for off-camera / third-speaker diarization labels.",
    )
    parser.add_argument(
        "--speaker-3-to-wide",
        action="store_true",
        help="Map speaker_id 3 to the wide camera (--wide-camera, default wide). "
             "Off by default for normal two-speaker interviews.",
    )
    parser.add_argument(
        "--start-trigger-phrase",
        default=None,
        help=(
            "Required start trigger (case/punctuation-insensitive; I am / I'm "
            "equivalent). With --start-phrase-countdown, the optional countdown "
            "tail may follow the trigger; each tail token may be skipped in order. "
            "The first cut begins at the quietest point in the "
            "--start-preroll-sec window before the first word after "
            "the trigger (or after the countdown when spoken), falling back "
            "to that static offset if the search fails. Default from "
            "podcast-phrase-gates.json; if absent, start trimming is skipped."
        ),
    )
    parser.add_argument(
        "--start-phrase",
        default=None,
        help="Deprecated alias for --start-trigger-phrase.",
    )
    parser.add_argument(
        "--start-preroll-sec",
        type=float,
        default=1.0,
        help=(
            "Search this many seconds before the first post-start-phrase word "
            "for the quietest start; fall back to this offset if the search "
            "fails (default: 1.0)."
        ),
    )
    parser.add_argument(
        "--start-phrase-countdown",
        nargs="*",
        default=None,
        help=(
            "Ordered optional countdown numbers after the trigger (ten through "
            "two as words). Optional leading ``in`` (see --no-start-countdown-in); "
            "each number may be skipped. Default from podcast-phrase-gates.json."
        ),
    )
    parser.add_argument(
        "--start-phrase-countdown-suffix",
        nargs="*",
        default=None,
        help=(
            "Optional trailing countdown words removed when present after the "
            "core countdown (default: one zero)."
        ),
    )
    parser.add_argument(
        "--no-start-countdown-in",
        action="store_true",
        help="Do not treat a leading ``in`` before the countdown as optional.",
    )
    parser.add_argument(
        "--end-phrase",
        action="append",
        default=None,
        help=(
            "Drop this phrase and everything after it (repeatable; the latest "
            "match among all end phrases wins). The last cut ends at the "
            "quietest point in the --end-postroll-sec window after the last "
            "word before the phrase (clamped so the phrase never appears), "
            "falling back to that static offset if the search fails. Defaults come from "
            "podcast-phrase-gates.json; if none match, end trimming is skipped."
        ),
    )
    parser.add_argument(
        "--end-postroll-sec",
        type=float,
        default=1.0,
        help=(
            "Search this many seconds after the last pre-end-phrase word for "
            "the quietest end (clamped so the end phrase never appears); fall "
            "back to this offset if the search fails (default: 1.0)."
        ),
    )
    parser.add_argument(
        "--pause-phrase",
        action="append",
        default=None,
        help=(
            "Pause cue phrase (repeatable; any match starts a pause). "
            "Matched Pause→Unpause pairs remove the cues and "
            "everything between them (unless --abort-phrase is present anywhere)."
        ),
    )
    parser.add_argument(
        "--unpause-phrase",
        action="append",
        default=None,
        help=(
            "Unpause cue phrase (repeatable; any match ends a pause). "
            "Example: --unpause-phrase 'Computer Resume Program' "
            "--unpause-phrase 'Computer Unfreeze Program'."
        ),
    )
    parser.add_argument(
        "--abort-phrase",
        default=None,
        help=(
            "If this phrase appears anywhere in the full transcript, all Pause/Unpause "
            "pairs are ignored for the episode."
        ),
    )
    parser.add_argument(
        "--pause-preroll-sec",
        type=float,
        default=0.25,
        help=(
            "Fallback keep after the last word before Pause when no audio search "
            "is available. Always clamped so the cut cannot enter the cue "
            "(default: 0.25)."
        ),
    )
    parser.add_argument(
        "--pause-postroll-sec",
        type=float,
        default=0.7,
        help=(
            "Fallback resume before the first word after Unpause when no audio "
            "search is available. Always clamped so resume cannot start inside "
            "the cue (default: 0.7)."
        ),
    )
    parser.add_argument(
        "--pause-resume-hold-sec",
        type=float,
        default=1.5,
        help=(
            "If a camera cut would fall within this many seconds after a pause "
            "resume, hold one camera for that window: wide if the pause shot was "
            "Host/Guest, or the speaker at resume if the pause shot was wide "
            "(default: 1.5)."
        ),
    )
    parser.add_argument(
        "--pause-audio-file",
        default=None,
        help=(
            "WAV (or ffmpeg-readable audio) used to place Start/End/Pause/"
            "Unpause cuts at the quietest point in each gate window. Defaults "
            "to the segment prepped WAV when it can be found."
        ),
    )
    return parser.parse_args()


def _load_word_tokens(raw_words: object) -> Tuple[WordToken, ...]:
    if not isinstance(raw_words, list):
        return ()
    words: List[WordToken] = []
    for w in raw_words:
        if not isinstance(w, dict):
            continue
        text = str(w.get("text", "") or "").strip()
        if not text:
            continue
        try:
            start = float(w.get("start", w.get("start_time")))
            end = float(w.get("end", w.get("end_time")))
        except (TypeError, ValueError):
            continue
        words.append(WordToken(text=text, start=start, end=end))
    return tuple(words)


def _load_rows(transcript: Dict[str, Dict]) -> List[Row]:
    keys = sorted(transcript.keys(), key=int)
    rows: List[Row] = []
    for k in keys:
        v = transcript[k]
        rows.append(
            Row(
                idx=int(k),
                start=float(v.get("start", 0.0)),
                end=float(v.get("end", 0.0)),
                text=str(v.get("text", "")),
                speaker_id=int(v.get("speaker_id", 0)),
                speaker_name=str(v.get("speaker_name", "") or ""),
                words=_load_word_tokens(v.get("words")),
            )
        )
    return rows


def _normalize_match_token(text: str) -> str:
    t = text.strip().lower()
    # Treat dashes as word separators so "override - Eject" matches spoken words.
    t = t.replace("—", " ").replace("–", " ").replace("-", " ")
    t = _NORMALIZE_TEXT_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _tokenize_phrase(phrase: str) -> List[str]:
    tokens = [t for t in _normalize_match_token(phrase).split() if t]
    if not tokens:
        raise ValueError(f"Phrase is empty after normalization: {phrase!r}")
    return tokens


def _cli_phrase_list(value: object) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return [str(p).strip() for p in list(value) if str(p).strip()]


def _flatten_match_words(rows: List[Row]) -> List[FlatWord]:
    flat: List[FlatWord] = []
    for row_i, row in enumerate(rows):
        for word_i, word in enumerate(row.words):
            token = _normalize_match_token(word.text)
            if not token:
                continue
            flat.append(
                FlatWord(
                    row_i=row_i,
                    word_i=word_i,
                    token=token,
                    start=float(word.start),
                    end=float(word.end),
                )
            )
    return flat


def _find_phrase_start_index(flat: List[FlatWord], phrase_tokens: List[str]) -> int:
    n = len(phrase_tokens)
    if n == 0:
        raise ValueError("Phrase token list is empty.")
    for i in range(0, len(flat) - n + 1):
        if all(flat[i + j].token == phrase_tokens[j] for j in range(n)):
            return i
    raise ValueError(
        "Phrase not found in word-timed transcript: "
        + " ".join(phrase_tokens)
    )


def _prefix_tokens_match_end(
    flat: List[FlatWord],
    start: int,
    prefix_tokens: List[str],
) -> int | None:
    """Return flat index after ``prefix_tokens``, or None if no match."""
    fi = start
    pi = 0
    while pi < len(prefix_tokens):
        if fi >= len(flat):
            return None
        pt = prefix_tokens[pi]
        ft = flat[fi].token
        if ft == pt:
            fi += 1
            pi += 1
            continue
        if (
            pt == "im"
            and ft == "i"
            and fi + 1 < len(flat)
            and flat[fi + 1].token == "am"
        ):
            fi += 2
            pi += 1
            continue
        if (
            pt == "i"
            and pi + 1 < len(prefix_tokens)
            and prefix_tokens[pi + 1] == "am"
            and ft == "im"
        ):
            fi += 1
            pi += 2
            continue
        return None
    return fi


def _prefix_tokens_match(flat: List[FlatWord], start: int, prefix_tokens: List[str]) -> bool:
    return _prefix_tokens_match_end(flat, start, prefix_tokens) is not None


def _build_countdown_optional_tail(
    countdown_tokens: List[str],
    *,
    allow_in: bool,
) -> List[str]:
    """Optional spoken tail after the trigger: ``in`` + countdown numbers."""
    if not countdown_tokens:
        return []
    if allow_in:
        return ["in", *countdown_tokens]
    return list(countdown_tokens)


def _consume_optional_spoken_tail(
    flat: List[FlatWord],
    pos: int,
    optional_tokens: List[str],
    *,
    suffix_set: set[str],
) -> int:
    """Advance ``pos`` across optional ``in`` / countdown tokens (all skippable)."""
    opt_idx = 0
    while opt_idx < len(optional_tokens):
        if pos >= len(flat):
            break
        token = flat[pos].token
        if token == optional_tokens[opt_idx]:
            pos += 1
            opt_idx += 1
        elif token in optional_tokens[opt_idx + 1 :]:
            opt_idx += 1
        elif token in suffix_set:
            break
        else:
            break
    return pos


def _find_countdown_start_span(
    flat: List[FlatWord],
    *,
    prefix_tokens: List[str],
    optional_tail_tokens: List[str],
    suffix_tokens: List[str],
) -> tuple[int, int] | None:
    """
    Return (match_start, match_end_exclusive) for a start phrase with optional
    ``in`` / countdown gaps and optional trailing suffix tokens (e.g. one, zero).
    """
    if not prefix_tokens:
        return None
    suffix_set = set(suffix_tokens)
    for i in range(0, len(flat)):
        pos = _prefix_tokens_match_end(flat, i, prefix_tokens)
        if pos is None:
            continue
        pos = _consume_optional_spoken_tail(
            flat,
            pos,
            optional_tail_tokens,
            suffix_set=suffix_set,
        )
        for suffix in suffix_tokens:
            if pos < len(flat) and flat[pos].token == suffix:
                pos += 1
            else:
                break
        if pos >= len(flat):
            continue
        return (i, pos)
    return None


def _start_trigger_exists(
    rows: List[Row],
    trigger_phrase: str,
    *,
    countdown_tokens: List[str] | None = None,
    countdown_suffix_tokens: List[str] | None = None,
    allow_in: bool = True,
) -> bool:
    flat = _flatten_match_words(rows)
    if not flat:
        return False
    trigger_tokens = _tokenize_phrase(trigger_phrase)
    if not trigger_tokens:
        return False
    if countdown_tokens:
        optional_tail = _build_countdown_optional_tail(
            list(countdown_tokens),
            allow_in=allow_in,
        )
        return (
            _find_countdown_start_span(
                flat,
                prefix_tokens=trigger_tokens,
                optional_tail_tokens=optional_tail,
                suffix_tokens=countdown_suffix_tokens or [],
            )
            is not None
        )
    return _phrase_exists(rows, trigger_phrase)


def _start_phrase_exists(
    rows: List[Row],
    phrase: str,
    *,
    countdown_tokens: List[str] | None = None,
    countdown_suffix_tokens: List[str] | None = None,
    allow_in: bool = True,
) -> bool:
    """Backward-compatible alias for tests and legacy callers."""
    return _start_trigger_exists(
        rows,
        phrase,
        countdown_tokens=countdown_tokens,
        countdown_suffix_tokens=countdown_suffix_tokens,
        allow_in=allow_in,
    )


def _apply_start_trigger_with_countdown(
    rows: List[Row],
    trigger_phrase: str,
    *,
    countdown_tokens: List[str],
    countdown_suffix_tokens: List[str],
    allow_in: bool = True,
    preroll_sec: float,
    quietest_fn: Optional[Callable[[float, float], Optional[float]]] = None,
    audio_path: Optional[Path] = None,
) -> StartPhraseCut:
    if preroll_sec < 0:
        raise ValueError("--start-preroll-sec must be >= 0")
    flat = _flatten_match_words(rows)
    if not flat:
        raise ValueError(
            "--start-trigger-phrase requires word-level timestamps on simplified transcript rows."
        )
    trigger_tokens = _tokenize_phrase(trigger_phrase)
    optional_tail = _build_countdown_optional_tail(countdown_tokens, allow_in=allow_in)
    span = _find_countdown_start_span(
        flat,
        prefix_tokens=trigger_tokens,
        optional_tail_tokens=optional_tail,
        suffix_tokens=countdown_suffix_tokens,
    )
    if span is None:
        raise ValueError(
            "Start trigger with countdown not found in word-timed transcript: "
            + " ".join(trigger_tokens + optional_tail)
        )
    match_start, match_end = span
    next_word = flat[match_end]
    host_speaker_id = int(rows[flat[match_start].row_i].speaker_id)
    kept = rows[next_word.row_i :]
    if not kept:
        raise ValueError("Start phrase left no transcript rows to keep.")
    first = kept[0]
    rel = float(next_word.start) - float(first.start)
    first_slice_start = rel if rel > 1e-6 else None
    matched = " ".join(flat[k].token for k in range(match_start, match_end))
    opening_sec, from_quietest = _start_opening_sec(
        next_word_start=float(next_word.start),
        preroll_sec=float(preroll_sec),
        quietest_fn=_resolve_quietest_fn(quietest_fn, audio_path),
    )
    return StartPhraseCut(
        rows=kept,
        first_slice_start=first_slice_start,
        content_start_abs=float(next_word.start),
        matched_phrase=matched,
        next_word_text=next_word.token,
        host_speaker_id=host_speaker_id,
        opening_sec=opening_sec,
        opening_from_quietest=from_quietest,
    )


def _apply_start_phrase_countdown(
    rows: List[Row],
    phrase: str,
    *,
    countdown_tokens: List[str],
    countdown_suffix_tokens: List[str],
    preroll_sec: float,
    allow_in: bool = True,
    quietest_fn: Optional[Callable[[float, float], Optional[float]]] = None,
    audio_path: Optional[Path] = None,
) -> StartPhraseCut:
    """Backward-compatible alias: ``phrase`` is the trigger only."""
    return _apply_start_trigger_with_countdown(
        rows,
        phrase,
        countdown_tokens=countdown_tokens,
        countdown_suffix_tokens=countdown_suffix_tokens,
        allow_in=allow_in,
        preroll_sec=preroll_sec,
        quietest_fn=quietest_fn,
        audio_path=audio_path,
    )


def _apply_start_phrase(
    rows: List[Row],
    phrase: str,
    *,
    preroll_sec: float,
    quietest_fn: Optional[Callable[[float, float], Optional[float]]] = None,
    audio_path: Optional[Path] = None,
) -> StartPhraseCut:
    if preroll_sec < 0:
        raise ValueError("--start-preroll-sec must be >= 0")
    flat = _flatten_match_words(rows)
    if not flat:
        raise ValueError(
            "--start-phrase requires word-level timestamps on simplified transcript rows."
        )
    phrase_tokens = _tokenize_phrase(phrase)
    match_i = _find_phrase_start_index(flat, phrase_tokens)
    after_i = match_i + len(phrase_tokens)
    if after_i >= len(flat):
        raise ValueError(
            f"Start phrase {' '.join(phrase_tokens)!r} was found, but no timed word follows it."
        )
    next_word = flat[after_i]
    host_speaker_id = int(rows[flat[match_i].row_i].speaker_id)
    kept = rows[next_word.row_i :]
    if not kept:
        raise ValueError("Start phrase left no transcript rows to keep.")
    first = kept[0]
    rel = float(next_word.start) - float(first.start)
    first_slice_start = rel if rel > 1e-6 else None
    opening_sec, from_quietest = _start_opening_sec(
        next_word_start=float(next_word.start),
        preroll_sec=float(preroll_sec),
        quietest_fn=_resolve_quietest_fn(quietest_fn, audio_path),
    )
    return StartPhraseCut(
        rows=kept,
        first_slice_start=first_slice_start,
        content_start_abs=float(next_word.start),
        matched_phrase=" ".join(phrase_tokens),
        next_word_text=next_word.token,
        host_speaker_id=host_speaker_id,
        opening_sec=opening_sec,
        opening_from_quietest=from_quietest,
    )


def _cam_by_speaker_with_host(
    host_speaker_id: int,
    base: Mapping[int, str],
) -> Dict[int, str]:
    """
    Map the start-phrase speaker to Host camera (speaker_0).

    Other close-mic speaker IDs that previously mapped to speaker_0/speaker_1
    become Guest (speaker_1). Wide / special mappings are preserved.
    """
    out: Dict[int, str] = dict(base)
    out[int(host_speaker_id)] = "speaker_0"
    for sid, cam in list(out.items()):
        if int(sid) == int(host_speaker_id):
            continue
        if cam in ("speaker_0", "speaker_1"):
            out[int(sid)] = "speaker_1"
    return out


def _apply_end_phrase(
    rows: List[Row],
    phrase: str,
    *,
    postroll_sec: float,
    match_index: int | None = None,
    quietest_fn: Optional[Callable[[float, float], Optional[float]]] = None,
    audio_path: Optional[Path] = None,
) -> EndPhraseCut:
    if postroll_sec < 0:
        raise ValueError("--end-postroll-sec must be >= 0")
    flat = _flatten_match_words(rows)
    if not flat:
        raise ValueError(
            "--end-phrase requires word-level timestamps on simplified transcript rows."
        )
    phrase_tokens = _tokenize_phrase(phrase)
    if match_index is None:
        match_i = _find_phrase_start_index(flat, phrase_tokens)
    else:
        match_i = match_index
    if match_i <= 0:
        raise ValueError(
            f"End phrase {' '.join(phrase_tokens)!r} was found, but no timed word precedes it."
        )
    last_word = flat[match_i - 1]
    end_phrase_start_abs = float(flat[match_i].start)
    kept = rows[: last_word.row_i + 1]
    if not kept:
        raise ValueError("End phrase left no transcript rows to keep.")
    last = kept[-1]
    content_end_abs, from_quietest = _end_content_abs(
        last_word_end=float(last_word.end),
        phrase_start_abs=end_phrase_start_abs,
        postroll_sec=float(postroll_sec),
        quietest_fn=_resolve_quietest_fn(quietest_fn, audio_path),
    )
    rel_end = content_end_abs - float(last.start)
    rel_end = max(rel_end, float(last_word.end) - float(last.start))
    last_slice_end = rel_end
    return EndPhraseCut(
        rows=kept,
        last_slice_end=last_slice_end,
        content_end_abs=content_end_abs,
        matched_phrase=" ".join(phrase_tokens),
        last_word_text=last_word.token,
        end_from_quietest=from_quietest,
    )


def _find_latest_end_phrase_match(
    rows: List[Row],
    phrases: List[str],
) -> tuple[str, int] | None:
    """Return (phrase, flat_word_index) for the latest end-phrase match, if any."""
    flat = _flatten_match_words(rows)
    if not flat:
        return None
    best: tuple[str, int] | None = None
    for phrase in phrases:
        if not phrase or not str(phrase).strip():
            continue
        tokens = _tokenize_phrase(str(phrase))
        for match_i in _find_all_phrase_starts(flat, tokens):
            if match_i <= 0:
                continue
            if best is None or match_i > best[1]:
                best = (str(phrase), match_i)
    return best


def _any_end_phrase_exists(rows: List[Row], phrases: List[str]) -> bool:
    return _find_latest_end_phrase_match(rows, phrases) is not None


def _phrase_exists(rows: List[Row], phrase: str) -> bool:
    flat = _flatten_match_words(rows)
    if not flat:
        return False
    try:
        _find_phrase_start_index(flat, _tokenize_phrase(phrase))
        return True
    except ValueError:
        return False


def _find_all_phrase_starts(flat: List[FlatWord], phrase_tokens: List[str]) -> List[int]:
    n = len(phrase_tokens)
    if n == 0:
        return []
    hits: List[int] = []
    i = 0
    while i <= len(flat) - n:
        if all(flat[i + j].token == phrase_tokens[j] for j in range(n)):
            hits.append(i)
            i += n
        else:
            i += 1
    return hits


def _earliest_phrase_hit(
    flat: List[FlatWord],
    phrases: List[str],
    *,
    search_from: int,
) -> Optional[Tuple[int, int, str]]:
    best: Optional[Tuple[int, int, str]] = None
    for phrase in phrases:
        if not str(phrase).strip():
            continue
        tokens = _tokenize_phrase(phrase)
        for i in _find_all_phrase_starts(flat, tokens):
            if i < search_from:
                continue
            cand = (i, i + len(tokens), " ".join(tokens))
            if best is None or cand[0] < best[0]:
                best = cand
    return best


def _match_pause_unpause_pairs(
    flat: List[FlatWord],
    pause_phrases: List[str],
    unpause_phrases: List[str],
) -> List[PausePair]:
    pairs: List[PausePair] = []
    search_from = 0
    while search_from < len(flat):
        pause_hit = _earliest_phrase_hit(flat, pause_phrases, search_from=search_from)
        if pause_hit is None:
            break
        pause_i, pause_end, pause_shown = pause_hit
        unpause_hit = _earliest_phrase_hit(flat, unpause_phrases, search_from=pause_end)
        if unpause_hit is None:
            # Unmatched pause: leave it in the video; keep scanning after it.
            search_from = pause_i + 1
            continue
        u_i, u_end, u_phrase = unpause_hit
        pairs.append(
            PausePair(
                pause_start_i=pause_i,
                pause_end_i=pause_end,
                unpause_start_i=u_i,
                unpause_end_i=u_end,
                pause_phrase=pause_shown,
                unpause_phrase=u_phrase,
            )
        )
        search_from = u_end
    return pairs


def _clamp(value: float, lo: float, hi: float) -> float:
    return min(max(value, lo), hi)


def _read_wav_span_mono(
    audio_path: Path,
    start_sec: float,
    duration_sec: float,
) -> Optional[Tuple[List[float], int]]:
    """Return (mono samples, sample_rate) for a short span, or None."""
    if duration_sec <= 1e-4 or not audio_path.is_file():
        return None
    try:
        with wave.open(str(audio_path), "rb") as wf:
            sr = int(wf.getframerate())
            nch = int(wf.getnchannels())
            sw = int(wf.getsampwidth())
            nframes = int(wf.getnframes())
            if sr <= 0 or nch <= 0 or sw not in (2, 4):
                raise wave.Error("unsupported wav format")
            start_i = max(0, int(round(float(start_sec) * sr)))
            take = max(1, int(round(float(duration_sec) * sr)))
            if start_i >= nframes:
                return None
            take = min(take, nframes - start_i)
            wf.setpos(start_i)
            raw = wf.readframes(take)
    except (wave.Error, EOFError, OSError):
        return _ffmpeg_decode_span_mono(audio_path, start_sec, duration_sec)

    if sw == 2:
        count = len(raw) // 2
        ints = struct.unpack("<" + "h" * count, raw[: count * 2])
        scale = 32768.0
    else:
        count = len(raw) // 4
        ints = struct.unpack("<" + "i" * count, raw[: count * 4])
        scale = 2147483648.0
    if nch == 1:
        samples = [v / scale for v in ints]
    else:
        frames = count // nch
        samples = []
        for i in range(frames):
            acc = 0.0
            base = i * nch
            for ch in range(nch):
                acc += ints[base + ch] / scale
            samples.append(acc / float(nch))
    return samples, sr


def _ffmpeg_decode_span_mono(
    audio_path: Path,
    start_sec: float,
    duration_sec: float,
    *,
    sample_rate: int = 48000,
) -> Optional[Tuple[List[float], int]]:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{float(start_sec):.6f}",
        "-t",
        f"{float(duration_sec):.6f}",
        "-i",
        str(audio_path),
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "f32le",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, check=False)
    except OSError:
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    raw = proc.stdout
    count = len(raw) // 4
    if count < 8:
        return None
    samples = list(struct.unpack("<" + "f" * count, raw[: count * 4]))
    return samples, sample_rate


def quietest_time_in_span(
    audio_path: Path,
    start_sec: float,
    end_sec: float,
    *,
    rms_window_sec: float = 0.02,
    hop_sec: float = 0.004,
) -> Optional[float]:
    """
    Return the time of minimum short-window RMS in ``[start_sec, end_sec]``.

    The last RMS window is kept entirely before ``end_sec`` so the first word of
    a cue is not part of the measurement.
    """
    lo = float(start_sec)
    hi = float(end_sec)
    if hi <= lo + 1e-4:
        return None
    duration = hi - lo
    loaded = _read_wav_span_mono(audio_path, lo, duration)
    if loaded is None:
        return None
    samples, sr = loaded
    if sr <= 0 or len(samples) < 8:
        return None
    win = max(4, int(round(rms_window_sec * sr)))
    hop = max(1, int(round(hop_sec * sr)))
    if len(samples) <= win:
        return lo + 0.5 * duration

    best_i = 0
    best_e = float("inf")
    last_start = len(samples) - win
    for i in range(0, last_start + 1, hop):
        chunk = samples[i : i + win]
        energy = sum(s * s for s in chunk) / float(win)
        if energy < best_e:
            best_e = energy
            best_i = i
    # Center of the quietest window, still inside the gap.
    found = lo + (best_i + 0.5 * win) / float(sr)
    return _clamp(found, lo, hi)


def _boundary_in_gap(
    gap_lo: float,
    gap_hi: float,
    *,
    fallback_offset_from_lo: Optional[float] = None,
    fallback_offset_from_hi: Optional[float] = None,
    quietest_fn: Optional[Callable[[float, float], Optional[float]]] = None,
) -> float:
    """Pick a cut inside ``[gap_lo, gap_hi]`` (quietest point, else clamped roll)."""
    lo = float(gap_lo)
    hi = float(gap_hi)
    if hi <= lo + 1e-6:
        return lo
    if quietest_fn is not None:
        found = quietest_fn(lo, hi)
        if found is not None:
            return _clamp(float(found), lo, hi)
    if fallback_offset_from_lo is not None:
        return min(lo + float(fallback_offset_from_lo), hi)
    if fallback_offset_from_hi is not None:
        return max(hi - float(fallback_offset_from_hi), lo)
    return 0.5 * (lo + hi)


def _resolve_quietest_fn(
    quietest_fn: Optional[Callable[[float, float], Optional[float]]],
    audio_path: Optional[Path],
) -> Optional[Callable[[float, float], Optional[float]]]:
    if quietest_fn is not None:
        return quietest_fn
    if audio_path is None:
        return None
    return lambda lo, hi, _path=audio_path: quietest_time_in_span(_path, lo, hi)


def _start_opening_sec(
    *,
    next_word_start: float,
    preroll_sec: float,
    quietest_fn: Optional[Callable[[float, float], Optional[float]]] = None,
) -> Tuple[float, bool]:
    """
    Seconds to pull before the first content word.

    Search ``[next_word_start - preroll, next_word_start]`` for the quietest
    point. If that search fails, return the static preroll.
    """
    hi = float(next_word_start)
    roll = float(preroll_sec)
    if roll <= 1e-9:
        return 0.0, False
    lo = hi - roll
    if quietest_fn is not None and hi > lo + 1e-6:
        found = quietest_fn(lo, hi)
        if found is not None:
            cut = _clamp(float(found), lo, hi)
            return max(0.0, hi - cut), True
    return roll, False


def _end_content_abs(
    *,
    last_word_end: float,
    phrase_start_abs: float,
    postroll_sec: float,
    quietest_fn: Optional[Callable[[float, float], Optional[float]]] = None,
) -> Tuple[float, bool]:
    """
    Absolute end time after the last pre-end-phrase word.

    Search the postroll window (clamped so the end phrase never appears). If
    that search fails, use the static postroll, still clamped to the phrase.
    """
    lo = float(last_word_end)
    hi = min(float(phrase_start_abs), lo + float(postroll_sec))
    if hi <= lo + 1e-6:
        return lo, False
    if quietest_fn is not None:
        found = quietest_fn(lo, hi)
        if found is not None:
            return _clamp(float(found), lo, hi), True
    return min(lo + float(postroll_sec), float(phrase_start_abs)), False


def _rel_slice_start(row: Row, abs_time: float) -> Optional[float]:
    rel = float(abs_time) - float(row.start)
    if rel <= 1e-6:
        return None
    return rel


def _rel_slice_end(row: Row, abs_time: float) -> float:
    return max(0.0, float(abs_time) - float(row.start))


def _row_overlaps_keep(row: Row, keep_lo: float, keep_hi: float) -> bool:
    return float(row.end) > keep_lo + 1e-6 and float(row.start) < keep_hi - 1e-6


def _piece_slices_for_keep_span(
    row: Row,
    keep_lo: float,
    keep_hi: float,
    *,
    is_first_in_interval: bool,
    is_last_in_interval: bool,
) -> Tuple[Optional[float], Optional[float]]:
    """
    Map a keep interval onto one transcript row as DSL slice offsets.

    First/last pieces in the interval may extend before ``row.start`` (negative
    ``slice_start``) or after ``row.end`` (``slice_end`` past the row duration).
    """
    clip_lo = float(keep_lo) if is_first_in_interval else max(float(row.start), float(keep_lo))
    clip_hi = (
        float(keep_hi) if is_last_in_interval else min(float(row.end), float(keep_hi))
    )

    rel_start = clip_lo - float(row.start)
    slice_start = None if abs(rel_start) <= 1e-6 else rel_start

    if is_last_in_interval and float(keep_hi) > float(row.end) + 1e-6:
        slice_end = float(keep_hi) - float(row.start)
    elif abs(clip_hi - float(row.end)) <= 1e-6:
        slice_end = None
    elif clip_hi < float(row.end) - 1e-6:
        slice_end = float(clip_hi) - float(row.start)
    else:
        slice_end = None

    return slice_start, slice_end


def _build_pieces_from_rows(
    rows: List[Row],
    *,
    first_slice_start: Optional[float],
    last_slice_end: Optional[float],
) -> List[Piece]:
    pieces: List[Piece] = []
    last_i = len(rows) - 1
    for i, row in enumerate(rows):
        pieces.append(
            Piece(
                row=row,
                slice_start=first_slice_start if i == 0 else None,
                slice_end=last_slice_end if i == last_i else None,
            )
        )
    return pieces


def _apply_pause_unpause_to_pieces(
    rows: List[Row],
    *,
    pause_phrases: List[str],
    unpause_phrases: List[str],
    preroll_sec: float,
    postroll_sec: float,
    first_slice_start: Optional[float],
    last_slice_end: Optional[float],
    audio_path: Optional[Path] = None,
    quietest_fn: Optional[Callable[[float, float], Optional[float]]] = None,
) -> Tuple[List[Piece], List[str]]:
    """
    Remove matched Pause→Unpause spans from rows and return emit pieces.

    Each seam is placed at the quietest point in the gap before the Pause cue
    and after the Unpause cue (ElevenLabs word times can miss a bit of the
    word). Without audio, falls back to ``preroll_sec`` / ``postroll_sec``,
    always clamped so the cut cannot enter the cue. Padding is emitted as
    explicit slice lead-in/tail on the boundary pieces (negative ``slice_start``
    / extended ``slice_end``), not just as keep-interval cut points.
    """
    if preroll_sec < 0 or postroll_sec < 0:
        raise ValueError("Pause preroll/postroll must be >= 0")
    if not unpause_phrases:
        raise ValueError("--pause-phrase requires at least one --unpause-phrase")

    flat = _flatten_match_words(rows)
    if not flat:
        raise ValueError(
            "--pause-phrase requires word-level timestamps on simplified transcript rows."
        )
    pairs = _match_pause_unpause_pairs(flat, pause_phrases, unpause_phrases)
    if not pairs:
        return (
            _build_pieces_from_rows(
                rows,
                first_slice_start=first_slice_start,
                last_slice_end=last_slice_end,
            ),
            [],
        )

    # Keep intervals in absolute time: [keep_lo, keep_hi).
    keep_intervals: List[Tuple[float, float]] = []
    notes: List[str] = []
    cursor_abs = float(rows[0].start)
    if first_slice_start is not None:
        cursor_abs = float(rows[0].start) + float(first_slice_start)

    seam_force_at_abs: List[float] = []
    finder = _resolve_quietest_fn(quietest_fn, audio_path)

    for pair in pairs:
        if pair.pause_start_i <= 0:
            # Nowhere to attach preroll; skip this pair (treat like unmatched).
            notes.append(
                f"Skipped pause {pair.pause_phrase!r}: nothing before it to keep."
            )
            continue
        last_before = flat[pair.pause_start_i - 1]
        pause_first = flat[pair.pause_start_i]
        if pair.unpause_end_i >= len(flat):
            notes.append(
                f"Skipped pause {pair.pause_phrase!r}: no word after unpause "
                f"{pair.unpause_phrase!r}."
            )
            continue
        first_after = flat[pair.unpause_end_i]
        unpause_last = flat[pair.unpause_end_i - 1]
        cut_end_abs = _boundary_in_gap(
            float(last_before.end),
            float(pause_first.start),
            fallback_offset_from_lo=float(preroll_sec),
            quietest_fn=finder,
        )
        resume_abs = _boundary_in_gap(
            float(unpause_last.end),
            float(first_after.start),
            fallback_offset_from_hi=float(postroll_sec),
            quietest_fn=finder,
        )
        if resume_abs < cut_end_abs:
            # Degenerate / overlapping rolls — hard join at midpoint.
            mid = 0.5 * (float(last_before.end) + float(first_after.start))
            cut_end_abs = mid
            resume_abs = mid

        keep_intervals.append((cursor_abs, cut_end_abs))
        seam_force_at_abs.append(resume_abs)
        notes.append(
            f"Pause {pair.pause_phrase!r} → Unpause {pair.unpause_phrase!r}: "
            f"drop {cut_end_abs:.3f}s..{resume_abs:.3f}s"
        )
        cursor_abs = resume_abs

    # Trailing keep through end of content.
    end_abs = float(rows[-1].end)
    if last_slice_end is not None:
        end_abs = float(rows[-1].start) + float(last_slice_end)
    keep_intervals.append((cursor_abs, end_abs))

    pieces: List[Piece] = []
    for keep_lo, keep_hi in keep_intervals:
        if keep_hi <= keep_lo + 1e-6:
            continue
        is_resume_interval = any(abs(keep_lo - resume_abs) <= 1e-3 for resume_abs in seam_force_at_abs)
        overlapping = [row for row in rows if _row_overlaps_keep(row, keep_lo, keep_hi)]
        for i, row in enumerate(overlapping):
            is_first = i == 0
            is_last = i == len(overlapping) - 1
            slice_start, slice_end = _piece_slices_for_keep_span(
                row,
                keep_lo,
                keep_hi,
                is_first_in_interval=is_first,
                is_last_in_interval=is_last,
            )
            pieces.append(
                Piece(
                    row=row,
                    slice_start=slice_start,
                    slice_end=slice_end,
                    force_cam=None,
                    seam_after_pause=is_resume_interval and is_first,
                    resume_abs=keep_lo if (is_resume_interval and is_first) else None,
                )
            )

    if not pieces:
        raise ValueError("Pause/Unpause removal left no transcript pieces to keep.")
    return pieces, notes


def _piece_abs_start(piece: Piece) -> float:
    if piece.slice_start is not None:
        return float(piece.row.start) + float(piece.slice_start)
    return float(piece.row.start)


def _piece_abs_end(piece: Piece) -> float:
    if piece.slice_end is not None:
        return float(piece.row.start) + float(piece.slice_end)
    return float(piece.row.end)


def _event_abs_start(piece: Piece, ev: dict) -> float:
    sl = ev.get("slice_start")
    if sl is not None:
        return float(piece.row.start) + float(sl)
    return _piece_abs_start(piece)


def _event_abs_end(piece: Piece, ev: dict) -> float:
    sl = ev.get("slice_end")
    if sl is not None:
        return float(piece.row.start) + float(sl)
    return _piece_abs_end(piece)


def _apply_pause_resume_camera_hold(
    pieces: List[Piece],
    events: List[dict],
    *,
    cam_by_speaker: Mapping[int, str],
    wide_camera: str,
    hold_sec: float,
    notes: List[str],
) -> List[dict]:
    """
    After a pause resume, flip off the pause camera. If another camera cut would
    land within ``hold_sec``, hold that resume camera for the window:

    - pause was Host/Guest → wide
    - pause was wide → speaker camera of the first resume line
    """
    if not events or hold_sec < 0:
        return events
    wide = str(wide_camera)
    out: List[dict] = []
    i = 0
    while i < len(events):
        ev = dict(events[i])
        piece = pieces[int(ev["piece_i"])]
        if not piece.seam_after_pause or i == 0:
            out.append(ev)
            i += 1
            continue

        pause_cam = str(events[i - 1]["cam"])
        speaker_cam = str(cam_by_speaker.get(piece.row.speaker_id, "speaker_0"))
        hold_cam = speaker_cam if pause_cam == wide else wide
        ev["cam"] = hold_cam
        ev["pause_hold"] = True
        piece.force_cam = hold_cam
        resume_t = (
            float(piece.resume_abs)
            if piece.resume_abs is not None
            else _event_abs_start(piece, ev)
        )
        hold_until = resume_t + float(hold_sec)
        out.append(ev)

        cut_within = False
        j = i + 1
        while j < len(events):
            nxt_piece = pieces[int(events[j]["piece_i"])]
            nstart = _event_abs_start(nxt_piece, events[j])
            if nstart >= hold_until - 1e-6:
                break
            if str(events[j]["cam"]) != hold_cam:
                cut_within = True
                break
            j += 1

        if not cut_within:
            notes.append(
                f"Pause seam after row {piece.row.idx}: {pause_cam} → {hold_cam}"
            )
            i += 1
            continue

        j = i + 1
        while j < len(events):
            nxt = dict(events[j])
            nxt_piece = pieces[int(nxt["piece_i"])]
            nstart = _event_abs_start(nxt_piece, nxt)
            nend = _event_abs_end(nxt_piece, nxt)
            if nstart >= hold_until - 1e-6:
                break
            if nend > hold_until + 1e-6:
                rel = hold_until - float(nxt_piece.row.start)
                first = dict(nxt)
                first["cam"] = hold_cam
                first["pause_hold"] = True
                first["slice_end"] = rel
                if first.get("slice_start") is None and abs(nstart - float(nxt_piece.row.start)) > 1e-6:
                    first["slice_start"] = nstart - float(nxt_piece.row.start)
                second = dict(nxt)
                second["slice_start"] = rel
                second["after_pause_hold"] = True
                out.append(first)
                out.append(second)
                j += 1
                break
            nxt["cam"] = hold_cam
            nxt["pause_hold"] = True
            nxt_piece.force_cam = hold_cam
            out.append(nxt)
            j += 1
        notes.append(
            f"Pause resume hold {hold_cam} for {hold_sec:.1f}s after row "
            f"{piece.row.idx} (pause was {pause_cam})"
        )
        i = j
    return out


def _normalize_interjection_text(text: str) -> str:
    t = text.strip().lower()
    t = t.replace("—", "-").replace("–", "-")
    t = _NORMALIZE_TEXT_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _is_brief_interjection_row(r: Row) -> bool:
    dur = max(0.0, float(r.end) - float(r.start))
    if dur > _BRIEF_INTERJECTION_MAX_SEC:
        return False

    t = _normalize_interjection_text(r.text)
    if not t:
        return False

    # Two backchannels like "yeah yeah" should count as *not* brief (user wants that to trigger).
    parts = t.split()
    if len(parts) >= 2 and all(p == parts[0] for p in parts):
        return False

    # Very short (1–2 tokens) and exactly one of our canonical interjections.
    if len(parts) > 2:
        return False
    return t in _INTERJECTION_CANONICAL


def _intended_camera(
    rows: List[Row],
    cam_by_speaker: Optional[Mapping[int, str]] = None,
) -> List[str]:
    """
    Camera selection per transcript row.

    Rule tweak: a *single* very brief interjection (e.g. "Mm-hmm.", "Yeah.") should not
    flip the camera on its own. Two consecutive interjections, or any longer utterance,
    can still flip as normal. Interjection suppression applies only to speaker_id 0/1.
    """
    mapping: Mapping[int, str] = cam_by_speaker or CAM_BY_SPEAKER_ID
    base = [mapping.get(r.speaker_id, "speaker_0") for r in rows]
    if not rows:
        return []

    effective: List[str] = []
    last_cam: Optional[str] = None
    pending_other_cam: Optional[str] = None  # remembers a suppressed one-off interjection cam

    for r, cam in zip(rows, base):
        if last_cam is None:
            effective.append(cam)
            last_cam = cam
            pending_other_cam = None
            continue

        if r.speaker_id not in (0, 1):
            effective.append(cam)
            last_cam = cam
            pending_other_cam = None
            continue

        if cam != last_cam and _is_brief_interjection_row(r):
            # One-off interjection: suppress the first one; allow the second in a row to cut.
            if pending_other_cam == cam:
                effective.append(cam)
                last_cam = cam
                pending_other_cam = None
            else:
                effective.append(last_cam)
                pending_other_cam = cam
            continue

        effective.append(cam)
        last_cam = cam
        pending_other_cam = None

    return effective


def _timeline_end(rows: List[Row], final_shot_tail_sec: float) -> float:
    if not rows:
        return 0.0
    return float(rows[-1].end) + max(0.0, float(final_shot_tail_sec))


def _row_overlaps_interval(r: Row, lo: float, hi: float) -> bool:
    """True if [r.start, r.end) intersects [lo, hi) on the timeline."""
    return float(r.start) < float(hi) and float(r.end) > float(lo)


def _apply_open_ben_lock(
    rows: List[Row],
    cams: List[str],
    open_sec: float,
    *,
    lock_start: float = 0.0,
) -> None:
    """Force speaker_0 on every row overlapping [lock_start, lock_start + open_sec)."""
    if open_sec <= 0.0 or not rows:
        return
    lo = float(lock_start)
    hi = lo + float(open_sec)
    for i, r in enumerate(rows):
        if _row_overlaps_interval(r, lo, hi):
            cams[i] = "speaker_0"


def _first_row_overlapping_tail(rows: List[Row], t_lo: float, t_hi: float) -> int:
    """First row index whose [start, end) intersects (t_lo, t_hi) on the timeline."""
    for i, r in enumerate(rows):
        if r.end > t_lo and r.start < t_hi:
            return i
    return len(rows)


def _apply_tail_ben_lock(
    rows: List[Row], cams: List[str], tail_sec: float, final_shot_tail_sec: float
) -> None:
    """Force speaker_0 from the first row that overlaps the last tail_sec of the timeline."""
    if tail_sec <= 0.0 or not rows:
        return
    t_hi = _timeline_end(rows, final_shot_tail_sec)
    t_lo = max(0.0, t_hi - float(tail_sec))
    j = _first_row_overlapping_tail(rows, t_lo, t_hi)
    for i in range(j, len(rows)):
        cams[i] = "speaker_0"


def _merge_row_spans(spans: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    if not spans:
        return []
    spans = sorted(spans, key=lambda x: (x[0], x[1]))
    out: List[Tuple[int, int]] = [spans[0]]
    for s, e in spans[1:]:
        ps, pe = out[-1]
        if s <= pe:
            out[-1] = (ps, max(pe, e))
        else:
            out.append((s, e))
    return out


def _trim_wide_spans_for_ben_locks(
    rows: List[Row],
    spans: List[Tuple[int, int]],
    *,
    open_sec: float,
    tail_sec: float,
    final_shot_tail_sec: float,
    open_lock_start: float = 0.0,
) -> List[Tuple[int, int]]:
    """
    Remove wide coverage from the open-Ben window [open_lock_start, open_lock_start + open_sec)
    and from the tail-Ben window [T - tail_sec, T] (row-aligned).
    """
    if not spans:
        return []
    T_end = _timeline_end(rows, final_shot_tail_sec)
    t_cut = (
        max(0.0, T_end - float(tail_sec)) if tail_sec > 0 else float("-inf")
    )
    tail_row0 = (
        _first_row_overlapping_tail(rows, t_cut, T_end) if tail_sec > 0 else len(rows)
    )
    open_lo = float(open_lock_start)
    open_hi = open_lo + float(open_sec)
    out: List[Tuple[int, int]] = []
    for s, e in spans:
        ss, ee = s, e
        if open_sec > 0:
            while ss < ee and _row_overlaps_interval(rows[ss], open_lo, open_hi):
                ss += 1
        if ss >= ee:
            continue
        if tail_sec > 0 and tail_row0 < len(rows):
            # Wide may not cover any row that overlaps [t_cut, T_end] (tail Ben window).
            ee = min(ee, tail_row0)
        if ss >= ee:
            continue
        out.append((ss, ee))
    return _merge_row_spans(out)


def _camera_cut_boundaries(rows: List[Row], cams: List[str]) -> List[Tuple[float, int]]:
    cuts: List[Tuple[float, int]] = []
    for i in range(1, len(rows)):
        if cams[i] != cams[i - 1]:
            cuts.append((rows[i].start, i))
    return cuts


def _find_wide_spans(
    rows: List[Row],
    cams: List[str],
    window_sec: float,
    min_wide_sec: float,
) -> List[Tuple[int, int]]:
    cuts = _camera_cut_boundaries(rows, cams)
    if len(cuts) < 2:
        return []

    spans: List[Tuple[int, int]] = []
    k = 0
    while k < len(cuts) - 1:
        t0, i0 = cuts[k]
        t1, _ = cuts[k + 1]
        if (t1 - t0) >= window_sec:
            k += 1
            continue

        # Dense cutting detected: start wide at the first cut boundary.
        start_idx = i0
        start_time = rows[start_idx].start

        # End wide at the first sentence boundary that makes the span >= min_wide_sec.
        end_idx = start_idx + 1
        while end_idx < len(rows) and (rows[end_idx].start - start_time) < min_wide_sec:
            end_idx += 1
        if end_idx > len(rows):
            end_idx = len(rows)

        # Extension exception: if another cut would happen within window_sec of the end boundary,
        # extend to that cut boundary; repeat until no such cut exists.
        while True:
            end_time = rows[end_idx].start if end_idx < len(rows) else rows[-1].end
            next_cut: Optional[Tuple[float, int]] = None
            for tc, ic in cuts:
                # strictly after the current boundary to avoid infinite loops
                if ic > end_idx:
                    next_cut = (tc, ic)
                    break
            if next_cut is None:
                break
            tc, ic = next_cut
            if (tc - end_time) < window_sec:
                end_idx = ic
                continue
            break

        # Merge overlaps/adjacent.
        if spans and start_idx <= spans[-1][1]:
            ps, pe = spans[-1]
            spans[-1] = (ps, max(pe, end_idx))
        else:
            spans.append((start_idx, end_idx))

        # Advance past cuts inside this span.
        while k < len(cuts) and cuts[k][1] < end_idx:
            k += 1

    return [(s, e) for (s, e) in spans if 0 <= s < e <= len(rows)]


def _spans_to_override_map(spans: List[Tuple[int, int]]) -> Dict[int, int]:
    m: Dict[int, int] = {}
    for s, e in spans:
        for i in range(s, e):
            m[i] = e
    return m


def _row_comment(row: Row, *, include_fallback_speaker: bool) -> str:
    text = row.text.strip().replace("\n", " ")
    if row.speaker_name:
        return f"{row.speaker_name}: {text}"
    if include_fallback_speaker:
        if row.speaker_id == 0:
            fallback = "Speaker 0"
        elif row.speaker_id == 1:
            fallback = "Speaker 1"
        else:
            fallback = f"Speaker {row.speaker_id}"
        return f"{fallback}: {text}"
    return text


def _row_segment_line(
    row: Row,
    segment_num: str,
    *,
    include_fallback_speaker: bool,
    is_last: bool,
    final_shot_tail_sec: float,
    slice_start: Optional[float] = None,
    slice_end: Optional[float] = None,
) -> str:
    segment_ref = f"$segment{segment_num}/{row.idx}"
    if is_last and final_shot_tail_sec > 0:
        # Extend the final row's extracted clip.
        base_end = (row.end - row.start) + final_shot_tail_sec
        slice_end = base_end if slice_end is None else max(float(slice_end), float(base_end))

    if slice_start is not None or slice_end is not None:
        start_s = "" if slice_start is None else f"{float(slice_start):.3f}"
        end_s = "" if slice_end is None else f"{float(slice_end):.3f}"
        segment_ref = f"{segment_ref} slice({start_s}:{end_s})"
    comment = _row_comment(row, include_fallback_speaker=include_fallback_speaker)
    return f"{segment_ref} // {comment}"


def _apply_camera_switch_offset(
    rows: List[Row],
    events: List[dict],
    *,
    offset_sec: float,
) -> List[dict]:
    """
    Shift camera switch boundaries earlier by pulling the next camera earlier with slice(negative_start:).

    events: list of {"cam": str, "row_i": int, "slice_start": Optional[float], "slice_end": Optional[float]}
    """
    if not events or abs(float(offset_sec)) < 1e-9:
        return events
    if offset_sec > 0:
        raise ValueError(
            "--camera-switch-offset-ms currently supports only negative values "
            "(switch earlier). Positive offsets would require splitting transcript rows, "
            "which interacts badly with gap-preservation and clip padding."
        )

    # For each camera change A -> B, pull B earlier by setting a negative slice_start.
    # This moves the visual cut earlier relative to the row boundary (row start time),
    # which matches user expectations even when there is a pause/gap between rows.
    shift = float(offset_sec)  # negative
    out = [dict(e) for e in events]
    for i in range(1, len(out)):
        prev = out[i - 1]
        cur = out[i]
        if prev["cam"] == cur["cam"]:
            continue
        # Apply to the *first* row under the new camera.
        existing = cur.get("slice_start")
        existing = float(existing) if existing is not None else 0.0
        # Take the earlier (more negative) of existing vs shift.
        cur["slice_start"] = min(existing, shift)
        out[i] = cur

    return out


def main() -> int:
    try:
        return _main_impl()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def _resolve_pause_audio_file(
    *,
    explicit: Optional[Path],
    segment_id: str,
    transcript_path: Path,
) -> Optional[Path]:
    if explicit is not None and explicit.is_file():
        return explicit.resolve()
    candidates: List[Path] = []
    env = str(os.environ.get("PODCAST_DSL_SEGMENTS_FILE") or "").strip()
    if env:
        candidates.append(Path(env))
    parent = transcript_path.resolve().parent
    candidates.append(parent / "segments.json")
    candidates.append(parent.parent / "Temp" / "segments.json")
    seen: set[str] = set()
    for seg_path in candidates:
        key = str(seg_path.resolve()) if seg_path.exists() else str(seg_path)
        if key in seen:
            continue
        seen.add(key)
        if not seg_path.is_file():
            continue
        try:
            data = json.loads(seg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        entry = data.get(str(segment_id))
        if not isinstance(entry, dict):
            continue
        raw = entry.get("audio_file")
        if not raw:
            continue
        audio = Path(str(raw))
        if audio.is_file():
            return audio.resolve()
    return None


def _main_impl() -> int:
    args = apply_namespace_phrase_defaults(parse_args())

    transcript_path = Path(args.transcript_json)
    output_path = Path(args.output)

    with transcript_path.open("r", encoding="utf-8") as f:
        transcript = json.load(f)

    segment_num = str(args.segment)
    full_rows = _load_rows(transcript)
    rows = list(full_rows)
    if args.max_start is not None:
        rows = [r for r in rows if r.start < float(args.max_start)]

    start_cut: Optional[StartPhraseCut] = None
    end_cut: Optional[EndPhraseCut] = None
    first_slice_start: Optional[float] = None
    last_slice_end: Optional[float] = None
    content_start_abs = 0.0
    pause_notes: List[str] = []
    phrase_notes: List[str] = []
    abort_triggered = False

    if args.abort_phrase and _phrase_exists(full_rows, str(args.abort_phrase)):
        abort_triggered = True

    gate_audio = _resolve_pause_audio_file(
        explicit=Path(args.pause_audio_file) if args.pause_audio_file else None,
        segment_id=segment_num,
        transcript_path=transcript_path,
    )
    gate_quietest = _resolve_quietest_fn(None, gate_audio)

    trigger_phrase = args.start_trigger_phrase or args.start_phrase
    if trigger_phrase:
        countdown_tokens = list(args.start_phrase_countdown or [])
        countdown_suffix = list(args.start_phrase_countdown_suffix or [])
        allow_in = not bool(getattr(args, "no_start_countdown_in", False))
        if _start_trigger_exists(
            full_rows,
            str(trigger_phrase),
            countdown_tokens=countdown_tokens or None,
            countdown_suffix_tokens=countdown_suffix,
            allow_in=allow_in,
        ):
            if countdown_tokens:
                start_cut = _apply_start_trigger_with_countdown(
                    rows,
                    str(trigger_phrase),
                    countdown_tokens=countdown_tokens,
                    countdown_suffix_tokens=countdown_suffix,
                    allow_in=allow_in,
                    preroll_sec=float(args.start_preroll_sec),
                    quietest_fn=gate_quietest,
                )
            else:
                start_cut = _apply_start_phrase(
                    rows,
                    str(trigger_phrase),
                    preroll_sec=float(args.start_preroll_sec),
                    quietest_fn=gate_quietest,
                )
            rows = start_cut.rows
            first_slice_start = start_cut.first_slice_start
            content_start_abs = start_cut.content_start_abs
        else:
            phrase_notes.append(
                f"Start trigger not found ({trigger_phrase!r}); using full transcript start."
            )

    if args.end_phrase:
        end_phrases = [str(p) for p in args.end_phrase if str(p).strip()]
        latest = _find_latest_end_phrase_match(rows, end_phrases)
        if latest is not None:
            matched_phrase, match_i = latest
            end_cut = _apply_end_phrase(
                rows,
                matched_phrase,
                postroll_sec=float(args.end_postroll_sec),
                match_index=match_i,
                quietest_fn=gate_quietest,
            )
            rows = end_cut.rows
            last_slice_end = end_cut.last_slice_end
            if (
                start_cut is not None
                and end_cut.content_end_abs <= start_cut.content_start_abs
            ):
                raise ValueError(
                    "End phrase content ends at or before the start-phrase content start. "
                    f"start={start_cut.content_start_abs:.3f}s end={end_cut.content_end_abs:.3f}s"
                )
        else:
            shown = " | ".join(end_phrases)
            phrase_notes.append(
                f"End phrase not found ({shown!r}); using full transcript end."
            )

    cam_by_speaker: Dict[int, str] = dict(CAM_BY_SPEAKER_ID)
    if args.speaker_2_to_wide:
        cam_by_speaker[2] = str(args.wide_camera)
    if args.speaker_3_to_wide:
        cam_by_speaker[3] = str(args.wide_camera)
    if start_cut is not None:
        cam_by_speaker = _cam_by_speaker_with_host(
            start_cut.host_speaker_id, cam_by_speaker
        )

    pause_phrases = _cli_phrase_list(args.pause_phrase)
    unpause_phrases = _cli_phrase_list(args.unpause_phrase)
    if pause_phrases and not abort_triggered:
        pieces, pause_notes = _apply_pause_unpause_to_pieces(
            rows,
            pause_phrases=pause_phrases,
            unpause_phrases=unpause_phrases,
            preroll_sec=float(args.pause_preroll_sec),
            postroll_sec=float(args.pause_postroll_sec),
            first_slice_start=first_slice_start,
            last_slice_end=last_slice_end,
            audio_path=gate_audio,
        )
    else:
        pieces = _build_pieces_from_rows(
            rows,
            first_slice_start=first_slice_start,
            last_slice_end=last_slice_end,
        )
        if abort_triggered and pause_phrases:
            pause_notes.append(
                f"Abort phrase present; ignoring Pause/Unpause "
                f"({args.abort_phrase!r})."
            )

    lines: List[str] = []

    if args.no_cameras:
        if start_cut is not None and float(start_cut.opening_sec) > 1e-6:
            preroll_ms = int(round(float(start_cut.opening_sec) * 1000.0))
            lines.append(f"!opening {preroll_ms}")
        last_i = len(pieces) - 1
        for idx, piece in enumerate(pieces):
            if piece.seam_after_pause:
                lines.append("!pause-flag")
            lines.append(
                _row_segment_line(
                    piece.row,
                    segment_num,
                    include_fallback_speaker=False,
                    is_last=idx == last_i and piece.slice_end is None and last_slice_end is None,
                    final_shot_tail_sec=float(args.final_shot_tail_sec),
                    slice_start=piece.slice_start,
                    slice_end=piece.slice_end,
                )
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        print(f"Wrote {len(lines)} DSL lines to {output_path}")
        return 0

    piece_rows = [p.row for p in pieces]
    cams = _intended_camera(piece_rows, cam_by_speaker)
    _apply_open_ben_lock(
        piece_rows,
        cams,
        float(args.open_ben_sec),
        lock_start=content_start_abs,
    )
    _apply_tail_ben_lock(
        piece_rows, cams, float(args.tail_ben_sec), float(args.final_shot_tail_sec)
    )
    spans = _find_wide_spans(
        piece_rows,
        cams,
        window_sec=float(args.cut_window_sec),
        min_wide_sec=float(args.min_wide_sec),
    )
    spans = _trim_wide_spans_for_ben_locks(
        piece_rows,
        spans,
        open_sec=float(args.open_ben_sec),
        tail_sec=float(args.tail_ben_sec),
        final_shot_tail_sec=float(args.final_shot_tail_sec),
        open_lock_start=content_start_abs,
    )
    override_map = _spans_to_override_map(spans)

    last_piece_i = len(pieces) - 1
    events: List[dict] = []
    i = 0
    while i < len(pieces):
        if i in override_map:
            end_i = override_map[i]
            for j in range(i, end_i):
                events.append(
                    {
                        "cam": str(args.wide_camera),
                        "piece_i": j,
                        "slice_start": pieces[j].slice_start,
                        "slice_end": pieces[j].slice_end,
                    }
                )
            i = end_i
            continue

        events.append(
            {
                "cam": str(cams[i]),
                "piece_i": i,
                "slice_start": pieces[i].slice_start,
                "slice_end": pieces[i].slice_end,
            }
        )
        i += 1

    # Pause-seam camera overrides win over dense-wide / intended cams.
    events = _apply_pause_resume_camera_hold(
        pieces,
        events,
        cam_by_speaker=cam_by_speaker,
        wide_camera=str(args.wide_camera),
        hold_sec=float(args.pause_resume_hold_sec),
        notes=pause_notes,
    )

    camera_switch_offset_ms = (
        0.0 if args.no_camera_switch_offset else float(args.camera_switch_offset_ms)
    )
    offset_rows = piece_rows
    offset_events = [
        {
            "cam": ev["cam"],
            "row_i": int(ev["piece_i"]),
            "slice_start": ev.get("slice_start"),
            "slice_end": ev.get("slice_end"),
        }
        for ev in events
    ]
    offset_events = _apply_camera_switch_offset(
        offset_rows,
        offset_events,
        offset_sec=camera_switch_offset_ms / 1000.0,
    )
    for ev, off in zip(events, offset_events):
        piece = pieces[int(ev["piece_i"])]
        if piece.seam_after_pause or ev.get("pause_hold") or ev.get("after_pause_hold"):
            continue
        ev["slice_start"] = off.get("slice_start")
        ev["slice_end"] = off.get("slice_end")

    lines.append("// Generated DSL")
    spk_bits = [
        f"Speaker {sid} -> {cam}"
        for sid, cam in sorted(cam_by_speaker.items(), key=lambda kv: kv[0])
    ]
    spk_hdr = ", ".join(spk_bits) if spk_bits else "Speaker 0 -> speaker_0, Speaker 1 -> speaker_1"
    lines.append(f"// segment{segment_num} | {spk_hdr}")
    if start_cut is not None:
        start_how = (
            "quietest in preroll"
            if start_cut.opening_from_quietest
            else "fallback preroll"
        )
        lines.append(
            f"// Start phrase: {start_cut.matched_phrase!r} -> begin "
            f"{float(start_cut.opening_sec):.2f}s before {start_cut.next_word_text!r} "
            f"({start_how}; abs {start_cut.content_start_abs:.3f}s); "
            f"Host = transcript speaker_id {start_cut.host_speaker_id} -> speaker_0"
        )
    if end_cut is not None:
        end_how = (
            "quietest in postroll"
            if end_cut.end_from_quietest
            else "fallback postroll"
        )
        lines.append(
            f"// End phrase: {end_cut.matched_phrase!r} -> end at "
            f"abs {end_cut.content_end_abs:.3f}s after {end_cut.last_word_text!r} "
            f"({end_how})"
        )
    for note in pause_notes:
        lines.append(f"// {note}")
    for note in phrase_notes:
        lines.append(f"// {note}")
    lines.append(
        f"// Open: first {float(args.open_ben_sec):.1f}s on speaker_0 (no cuts off Ben before then); "
        f"tail: last {float(args.tail_ben_sec):.1f}s on speaker_0 (timeline includes "
        f"{float(args.final_shot_tail_sec):.1f}s final-shot tail)"
    )
    lines.append(
        f"// Wide rule: if >1 camera cut in {float(args.cut_window_sec):.1f}s, force !camera {args.wide_camera} "
        f"for >= {float(args.min_wide_sec):.1f}s (sentence-aligned), extend if another cut within {float(args.cut_window_sec):.1f}s"
    )
    if camera_switch_offset_ms != 0.0:
        lines.append(
            f"// camera-switch-offset-ms={camera_switch_offset_ms:.1f}; "
            "disabling cut padding to avoid overlap artifacts"
        )
        lines.append("!cut 0 0")
    if start_cut is not None and float(start_cut.opening_sec) > 1e-6:
        preroll_ms = int(round(float(start_cut.opening_sec) * 1000.0))
        lines.append(f"!opening {preroll_ms}")
    lines.append("")

    current_cam: Optional[str] = None
    for ev_i, ev in enumerate(events):
        cam = ev["cam"]
        if current_cam != cam:
            lines.append(f"!camera {cam}")
            current_cam = cam

        piece_i = int(ev["piece_i"])
        piece = pieces[piece_i]
        is_last_event = ev_i == (len(events) - 1)
        if piece.seam_after_pause:
            lines.append("!pause-flag")
        lines.append(
            _row_segment_line(
                piece.row,
                segment_num,
                include_fallback_speaker=True,
                is_last=(
                    is_last_event
                    and piece_i == last_piece_i
                    and piece.slice_end is None
                    and last_slice_end is None
                ),
                final_shot_tail_sec=float(args.final_shot_tail_sec),
                slice_start=ev.get("slice_start"),
                slice_end=ev.get("slice_end"),
            )
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {len(events)} DSL clip lines to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
