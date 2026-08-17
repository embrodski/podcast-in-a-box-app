"""Fast Preview helpers — 300s sandbox prep before full-length processing."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from harness_episode_lib import utc_now_iso
from piab_lib import (
    GUEST_RAW_AUDIO,
    GUEST_RAW_VIDEO,
    HOST_RAW_AUDIO,
    HOST_RAW_VIDEO,
    WIDE_RAW_VIDEO,
    estimate_prep_through_one_min,
    ffprobe_duration,
    format_eta_range,
)

FAST_PREVIEW_DIR_NAME = "Preview Files"
PREVIEW_PREFIX = "Preview "
FAST_PREVIEW_CLIP_SEC = 300.0
# Observed Fast Preview wall-clock is ~3× faster than the shared prep formula.
FAST_PREVIEW_ESTIMATE_DIVISOR = 3.0
# Kept for older session JSON; Fast Preview now runs for every labeled session.
FAST_PREVIEW_THRESHOLD_SEC = 600.0
FAST_PREVIEW_SHORT_SOURCE_SEC = 300.0  # < 5 min → tail 1-min + reuse prepped files
FAST_PREVIEW_START_PHRASE_MAX_SEC = 240.0  # 4 minutes — phrase must appear by here

PREVIEW_ONE_MIN_DEFAULT = f"{PREVIEW_PREFIX}1 Min Test.mp4"
PREVIEW_ONE_MIN_NO_OFFSET = f"{PREVIEW_PREFIX}1 Min Test no offset.mp4"
PREVIEW_ONE_MIN_FORCED_OFFSET = f"{PREVIEW_PREFIX}1 Min Test forced audio offset.mp4"

LABELED_RAW_FILES: tuple[str, ...] = (
    HOST_RAW_VIDEO,
    GUEST_RAW_VIDEO,
    WIDE_RAW_VIDEO,
    HOST_RAW_AUDIO,
    GUEST_RAW_AUDIO,
)


def preview_filename(standard_name: str) -> str:
    return f"{PREVIEW_PREFIX}{standard_name}"


def preview_root(working_folder: Path) -> Path:
    return working_folder.resolve() / FAST_PREVIEW_DIR_NAME


def preview_paths(working_folder: Path) -> dict[str, str]:
    root = preview_root(working_folder)
    return {
        "preview_root": str(root),
        "raw": str(root),
        "input": str(root / "Input"),
        "output": str(root / "Output"),
        "temp": str(root / "Temp"),
    }


def ensure_preview_layout(working_folder: Path) -> Path:
    root = preview_root(working_folder)
    for sub in ("", "Input", "Output", "Temp"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def max_raw_video_duration_sec(raw_dir: Path) -> float:
    raw_dir = raw_dir.resolve()
    durations: list[float] = []
    for name in (HOST_RAW_VIDEO, GUEST_RAW_VIDEO, WIDE_RAW_VIDEO):
        path = raw_dir / name
        if not path.is_file():
            raise FileNotFoundError(f"Missing labeled raw video: {path}")
        durations.append(ffprobe_duration(path))
    return max(durations)


def fast_preview_eligible(raw_dir: Path) -> bool:
    """True when labeled Host/Guest/Wide videos exist (all sessions use Fast Preview)."""
    max_raw_video_duration_sec(raw_dir)
    return True


def is_short_source_duration(duration_sec: float | None) -> bool:
    return duration_sec is not None and float(duration_sec) < FAST_PREVIEW_SHORT_SOURCE_SEC


def short_source_duration_from_state(state: dict | None) -> float | None:
    if not state:
        return None
    fp = state.get("fast_preview") if isinstance(state.get("fast_preview"), dict) else {}
    raw = None
    if isinstance(fp, dict):
        raw = fp.get("max_video_duration_sec")
    if raw is None:
        raw = state.get("max_video_duration_sec")
    try:
        if raw is None:
            return None
        return float(raw)
    except (TypeError, ValueError):
        return None


def is_short_source_state(state: dict | None) -> bool:
    return is_short_source_duration(short_source_duration_from_state(state))


def estimate_fast_preview_prep() -> dict[str, Any]:
    raw = estimate_prep_through_one_min(FAST_PREVIEW_CLIP_SEC)
    center = float(raw["center_sec"]) / FAST_PREVIEW_ESTIMATE_DIVISOR
    result = format_eta_range(center)
    breakdown = dict(raw.get("breakdown") or {})
    for key in (
        "conversation_sync_sec",
        "video_sync_sec",
        "transcribe_sec",
        "one_min_render_sec",
    ):
        value = breakdown.get(key)
        if isinstance(value, (int, float)):
            breakdown[key] = int(round(value / FAST_PREVIEW_ESTIMATE_DIVISOR))
    result["breakdown"] = breakdown
    return result


def _ffmpeg_copy_head(*, src: Path, dest: Path, seconds: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-t",
            f"{seconds:.3f}",
            "-i",
            str(src),
            "-c",
            "copy",
            str(dest),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not dest.is_file():
        raise RuntimeError(
            f"ffmpeg preview clip failed for {src}:\n{(proc.stderr or proc.stdout).strip()}"
        )


def create_preview_clips(
    working_folder: Path,
    *,
    allow_overwrite: bool = False,
) -> dict[str, Any]:
    """Stream-copy first 300s of each labeled Raw file into Preview Files/."""
    from harness_overwrite_guard import refuse_overwrite
    from piab_lib import load_piab_state, save_piab_state

    working = working_folder.resolve()
    state = load_piab_state(working)
    restore_canonical_paths(state)
    raw = Path(state["paths"]["raw"])
    max_raw_video_duration_sec(raw)

    ensure_preview_layout(working)
    preview_raw = preview_root(working)
    clips: dict[str, str] = {}

    for standard_name in LABELED_RAW_FILES:
        src = raw / standard_name
        if not src.is_file():
            raise FileNotFoundError(f"Missing Raw source for preview clip: {src}")
        dest = preview_raw / preview_filename(standard_name)
        refuse_overwrite(dest, allow_overwrite=allow_overwrite)
        _ffmpeg_copy_head(src=src, dest=dest, seconds=FAST_PREVIEW_CLIP_SEC)
        clips[standard_name] = str(dest.resolve())

    max_video = max_raw_video_duration_sec(raw)
    info = {
        "enabled": True,
        "clip_sec": FAST_PREVIEW_CLIP_SEC,
        "threshold_sec": FAST_PREVIEW_THRESHOLD_SEC,
        "max_video_duration_sec": max_video,
        "short_source": is_short_source_duration(max_video),
        "preview_root": str(preview_raw.resolve()),
        "clips": clips,
        "clips_created_at": utc_now_iso(),
    }
    state.setdefault("fast_preview", {}).update(info)
    state["estimate_prep_fast"] = estimate_fast_preview_prep()
    state["resume_at"] = "05b_fast_preview_clips"
    save_piab_state(working, state)
    return info


def apply_preview_paths_to_state(state: dict, working_folder: Path) -> dict[str, str]:
    """Overlay preview sandbox paths onto ``state['paths']``; keep canonical copy."""
    paths = state.setdefault("paths", {})
    if "_canonical" not in paths:
        paths["_canonical"] = dict(paths)
    overlay = preview_paths(working_folder)
    paths.update(overlay)
    state["fast_preview_active"] = True
    return overlay


def restore_canonical_paths(state: dict) -> None:
    paths = state.get("paths") or {}
    canonical = paths.get("_canonical")
    if isinstance(canonical, dict):
        state["paths"] = dict(canonical)
    state.pop("fast_preview_active", None)


def clear_preview_sandbox(state: dict, working_folder: Path) -> None:
    """Remove preview artifacts and in-progress approval when re-labeling."""
    import shutil

    root = preview_root(working_folder)
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)
    state.pop("fast_preview_approval", None)
    fp = state.get("fast_preview")
    if isinstance(fp, dict):
        fp.pop("clips", None)
        fp.pop("clips_created_at", None)
    restore_canonical_paths(state)


def save_fast_preview_approval(
    state: dict,
    *,
    sync_offset_choice: str | None,
    swap_speaker_ids: bool,
    preview_render_mode: str,
    preview_one_min_path: str,
    sync_ab_required: bool,
) -> dict[str, Any]:
    bundle = {
        "approved_at": utc_now_iso(),
        "sync_offset_choice": sync_offset_choice,
        "swap_speaker_ids": swap_speaker_ids,
        "preview_render_mode": preview_render_mode,
        "preview_one_min_path": preview_one_min_path,
        "sync_ab_required": sync_ab_required,
    }
    state["fast_preview_approval"] = bundle
    return bundle


def apply_fast_preview_approval_to_state(state: dict) -> None:
    """Copy recorded preview choices onto canonical session fields before full prep."""
    approval = state.get("fast_preview_approval")
    if not isinstance(approval, dict) or not approval.get("approved_at"):
        raise ValueError("fast_preview_approval is missing or not approved.")

    if approval.get("swap_speaker_ids") is not None:
        state["swap_speaker_ids"] = bool(approval["swap_speaker_ids"])

    choice = approval.get("sync_offset_choice")
    if choice in ("start_aligned", "forced_offset"):
        state["sync_offset_choice"] = choice
        state["sync_offset_choice_pending"] = False


def snapshot_preview_sandbox_artifacts(state: dict) -> dict[str, Any]:
    """Keep preview prep paths after approval pops canonical keys."""
    artifacts = {
        "main_prepped": copy.deepcopy(state.get("main_prepped")),
        "main_prepped_forced_offset": copy.deepcopy(state.get("main_prepped_forced_offset")),
        "main_transcript_json": state.get("main_transcript_json"),
        "main_combined_audio": state.get("main_combined_audio"),
        "main_clean_audio": state.get("main_clean_audio"),
    }
    fp = state.setdefault("fast_preview", {})
    if isinstance(fp, dict):
        fp["sandbox_artifacts"] = artifacts
    return artifacts


def _strip_preview_prefix(name: str) -> str:
    if name.startswith(PREVIEW_PREFIX):
        return name[len(PREVIEW_PREFIX) :]
    return name


def _canonical_destination_for_preview_file(src: Path, working_folder: Path) -> Path:
    working = working_folder.resolve()
    preview = preview_root(working)
    src = src.resolve()
    name = _strip_preview_prefix(src.name)
    try:
        rel = src.relative_to(preview)
    except ValueError:
        return working / "Input" / name
    parts = rel.parts
    if len(parts) == 1:
        return working / "Raw" / name
    top = parts[0]
    if top == "Input":
        return working / "Input" / name
    if top == "Temp":
        return working / "Temp" / name
    if top == "Output":
        return working / "Output" / name
    return working / "Raw" / name


def _copy_preview_file(
    src: Path,
    working_folder: Path,
    *,
    allow_overwrite: bool,
) -> Path:
    from harness_overwrite_guard import refuse_overwrite

    dest = _canonical_destination_for_preview_file(src, working_folder)
    dest.parent.mkdir(parents=True, exist_ok=True)
    refuse_overwrite(dest, allow_overwrite=allow_overwrite)
    import shutil

    shutil.copy2(src, dest)
    return dest


def _rewrite_preview_paths_in_value(
    value: Any,
    working_folder: Path,
    *,
    allow_overwrite: bool,
) -> Any:
    if isinstance(value, str):
        path = Path(value)
        if path.is_file() and FAST_PREVIEW_DIR_NAME in path.parts:
            return str(_copy_preview_file(path, working_folder, allow_overwrite=allow_overwrite))
        return value
    if isinstance(value, list):
        return [
            _rewrite_preview_paths_in_value(item, working_folder, allow_overwrite=allow_overwrite)
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: _rewrite_preview_paths_in_value(
                item, working_folder, allow_overwrite=allow_overwrite
            )
            for key, item in value.items()
        }
    return value


def promote_short_source_preview_to_canonical(
    working_folder: Path,
    *,
    allow_overwrite: bool = False,
) -> dict[str, Any]:
    """Copy <5 min Fast Preview prepped media into canonical Input/Temp/Raw."""
    from piab_lib import load_piab_state, mark_step, save_piab_state

    working = working_folder.resolve()
    state = load_piab_state(working)
    restore_canonical_paths(state)
    apply_fast_preview_approval_to_state(state)

    fp = state.get("fast_preview") if isinstance(state.get("fast_preview"), dict) else {}
    artifacts = (fp or {}).get("sandbox_artifacts") or {}
    choice = state.get("sync_offset_choice")
    prep = artifacts.get("main_prepped")
    if choice == "forced_offset" and artifacts.get("main_prepped_forced_offset"):
        prep = artifacts.get("main_prepped_forced_offset")
    if not isinstance(prep, dict):
        raise ValueError("Short-source Fast Preview has no sandbox prepped media to reuse.")

    promoted_prep = _rewrite_preview_paths_in_value(
        prep, working, allow_overwrite=allow_overwrite
    )
    transcript = artifacts.get("main_transcript_json")
    combined = artifacts.get("main_combined_audio")
    clean = artifacts.get("main_clean_audio")
    if transcript:
        state["main_transcript_json"] = _rewrite_preview_paths_in_value(
            transcript, working, allow_overwrite=allow_overwrite
        )
    if combined:
        state["main_combined_audio"] = _rewrite_preview_paths_in_value(
            combined, working, allow_overwrite=allow_overwrite
        )
    if clean:
        state["main_clean_audio"] = _rewrite_preview_paths_in_value(
            clean, working, allow_overwrite=allow_overwrite
        )

    state["main_prepped"] = promoted_prep
    if choice == "forced_offset":
        state["main_prepped_forced_offset"] = promoted_prep

    note = "Reused Fast Preview prepped files (source shorter than 5 minutes)."
    mark_step(
        state,
        "06_conversation_sync",
        title="Conversation-sync",
        status="completed",
        note=note,
        output=state.get("main_combined_audio"),
    )
    mark_step(
        state,
        "07_deroom_placeholder",
        title="Clean audio selection",
        status="completed",
        note=note,
        main_clean_audio=state.get("main_clean_audio"),
    )
    mark_step(
        state,
        "08_video_sync",
        title="Video-sync (main)",
        status="completed",
        note=note,
        **(promoted_prep if isinstance(promoted_prep, dict) else {}),
    )
    mark_step(
        state,
        "09_transcribe",
        title="Transcribe prepped WAV",
        status="completed",
        note=note,
        transcript_json=state.get("main_transcript_json"),
    )
    mark_step(
        state,
        "10_one_min_test",
        title="Podcast autocut 1-min test",
        status="skipped",
        note="Skipped — Fast Preview approval recorded.",
    )
    mark_step(
        state,
        "11_one_min_approval",
        title="1-min test approval",
        status="completed",
        from_fast_preview=True,
        reused_preview_prepped=True,
    )
    state["resume_at"] = "13_full_render"
    if isinstance(fp, dict):
        fp["promoted_to_canonical"] = True
        fp["promoted_at"] = utc_now_iso()
    from harness_av_sync_lib import write_canonical_main_segments

    write_canonical_main_segments(state)
    save_piab_state(working, state)
    return {
        "main_prepped": promoted_prep,
        "main_transcript_json": state.get("main_transcript_json"),
        "reused_preview_prepped": True,
        "forced_only": choice == "forced_offset",
    }


def preview_one_min_output_names() -> tuple[str, str, str]:
    return PREVIEW_ONE_MIN_DEFAULT, PREVIEW_ONE_MIN_NO_OFFSET, PREVIEW_ONE_MIN_FORCED_OFFSET


def resolve_preview_one_min_path(state: dict, working_folder: Path) -> Path:
    approval = state.get("fast_preview_approval") or {}
    raw = approval.get("preview_one_min_path")
    if raw:
        path = Path(str(raw))
        if path.is_file():
            return path.resolve()

    preview_out = preview_root(working_folder) / "Output"
    choice = approval.get("sync_offset_choice") or state.get("sync_offset_choice")
    if choice == "forced_offset":
        forced = preview_out / PREVIEW_ONE_MIN_FORCED_OFFSET
        if forced.is_file():
            return forced.resolve()
    for name in (PREVIEW_ONE_MIN_DEFAULT, PREVIEW_ONE_MIN_NO_OFFSET):
        candidate = preview_out / name
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"No preview 1-min test found under {preview_out}")


def _load_simplified_rows(path: Path) -> list[dict[str, Any]]:
    """Load convert_transcript_json / generate_full_dsl simplified transcript rows."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("rows"), list):
        return list(data["rows"])
    if isinstance(data, dict):
        # Canonical shape from convert_transcript_json: {"0": {...}, "1": {...}, ...}
        try:
            keys = sorted(data.keys(), key=lambda k: int(str(k)))
        except (TypeError, ValueError):
            keys = list(data.keys())
        rows = [data[k] for k in keys if isinstance(data.get(k), dict)]
        if rows:
            return rows
    raise ValueError(f"Unexpected simplified transcript shape: {path}")


def _row_start_sec(row: dict[str, Any]) -> float | None:
    words = row.get("words") or []
    if words:
        try:
            return float(words[0].get("start", words[0].get("start_time")))
        except (TypeError, ValueError, IndexError):
            pass
    for key in ("start", "start_time", "start_sec"):
        if key in row:
            try:
                return float(row[key])
            except (TypeError, ValueError):
                continue
    return None


def _row_end_sec(row: dict[str, Any]) -> float | None:
    words = row.get("words") or []
    if words:
        last = words[-1]
        for key in ("end", "end_time", "start", "start_time"):
            if key in last:
                try:
                    return float(last[key])
                except (TypeError, ValueError):
                    continue
    for key in ("end", "end_time", "end_sec"):
        if key in row:
            try:
                return float(row[key])
            except (TypeError, ValueError):
                continue
    return _row_start_sec(row)


def _shift_time_fields(payload: dict[str, Any], origin: float) -> None:
    for key in ("start", "end", "start_time", "end_time", "start_sec", "end_sec"):
        if key not in payload:
            continue
        try:
            payload[key] = max(0.0, float(payload[key]) - origin)
        except (TypeError, ValueError):
            continue


def _rebase_transcript_row(row: dict[str, Any], origin: float) -> None:
    _shift_time_fields(row, origin)
    words = row.get("words") or []
    if not isinstance(words, list):
        return
    for word in words:
        if isinstance(word, dict):
            _shift_time_fields(word, origin)


def find_start_phrase_time_sec(
    simplified_json: Path,
    state: dict | None = None,
) -> float | None:
    """Return wall-clock seconds where start trigger begins, or None if absent."""
    import sys

    repo_root = Path(__file__).resolve().parent.parent
    src = str((repo_root / "src").resolve())
    if src not in sys.path:
        sys.path.insert(0, src)
    root = str(repo_root.resolve())
    if root not in sys.path:
        sys.path.insert(0, root)

    from generate_full_dsl import (
        _find_countdown_start_span,
        _flatten_match_words,
        _tokenize_phrase,
    )
    from podcast_phrase_gates import (
        load_phrase_gates,
        start_countdown_suffix_from_gates,
        start_countdown_tokens_from_gates,
        start_trigger_phrase_from_gates,
    )

    rows_raw = _load_simplified_rows(simplified_json)

    class _Word:
        def __init__(self, data: dict[str, Any]) -> None:
            self.text = str(data.get("text", "") or "")
            try:
                self.start = float(data.get("start", data.get("start_time", 0.0)))
            except (TypeError, ValueError):
                self.start = 0.0
            try:
                self.end = float(data.get("end", data.get("end_time", self.start)))
            except (TypeError, ValueError):
                self.end = self.start

    class _Row:
        def __init__(self, data: dict[str, Any]) -> None:
            self.speaker_id = data.get("speaker_id", data.get("speaker", 0))
            words = data.get("words") or []
            self.words = [_Word(w) for w in words if isinstance(w, dict)]
            self.text = data.get("text", "")
            try:
                self.start = float(data.get("start", 0.0))
            except (TypeError, ValueError):
                self.start = 0.0
            try:
                self.end = float(data.get("end", self.start))
            except (TypeError, ValueError):
                self.end = self.start

    rows = [_Row(r) for r in rows_raw if isinstance(r, dict)]
    flat = _flatten_match_words(rows)
    if not flat:
        return None

    gates = load_phrase_gates(state_overrides=state or {})
    trigger = start_trigger_phrase_from_gates(gates)
    countdown = start_countdown_tokens_from_gates(gates)
    suffix = start_countdown_suffix_from_gates(gates)
    if not trigger:
        return None

    trigger_tokens = _tokenize_phrase(trigger)
    if not trigger_tokens:
        return None

    if countdown:
        from generate_full_dsl import _build_countdown_optional_tail

        optional_tail = _build_countdown_optional_tail(list(countdown), allow_in=True)
        span = _find_countdown_start_span(
            flat,
            prefix_tokens=trigger_tokens,
            optional_tail_tokens=optional_tail,
            suffix_tokens=list(suffix or []),
        )
    else:
        span = None
        for i in range(len(flat)):
            window = [flat[j].token for j in range(i, min(i + len(trigger_tokens), len(flat)))]
            if window == trigger_tokens:
                span = (i, i + len(trigger_tokens))
                break

    if span is None:
        return None

    match_start, _match_end = span
    return float(flat[match_start].start)


def should_use_tail_preview(
    *,
    simplified_json: Path,
    prepped_duration_sec: float,
    state: dict | None = None,
) -> tuple[bool, str]:
    source_sec = short_source_duration_from_state(state)
    if is_short_source_duration(source_sec):
        return True, "source_shorter_than_5min"
    start_sec = find_start_phrase_time_sec(simplified_json, state=state)
    if start_sec is None:
        return True, "start_phrase_missing"
    if start_sec > FAST_PREVIEW_START_PHRASE_MAX_SEC:
        return True, "start_phrase_after_4min"
    remaining = prepped_duration_sec - start_sec
    if remaining < 60.0:
        return True, "insufficient_head_content"
    return False, "head_autocut"


def slice_simplified_transcript_last_seconds(
    simplified_json: Path,
    *,
    output_path: Path,
    window_sec: float = 60.0,
    media_duration_sec: float,
) -> tuple[Path, float]:
    """Keep the last ``window_sec`` of prepped media and rebase times to 0.

    The renderer always starts the first clip at timeline 0. Leaving absolute
    timestamps (e.g. 240–300s) makes that first clip run from 0 to ~4 minutes
    with no camera cuts. Rebasing plus media offsets treats the tail as a
    normal 60s source.
    """
    original = json.loads(simplified_json.read_text(encoding="utf-8"))
    rows = _load_simplified_rows(simplified_json)
    window_start = max(0.0, media_duration_sec - window_sec)
    kept: list[dict[str, Any]] = []

    for row in rows:
        row_copy = copy.deepcopy(row)
        words = row_copy.get("words") or []
        if words:
            filtered = []
            for word in words:
                try:
                    start = float(word.get("start", word.get("start_time")))
                except (TypeError, ValueError):
                    continue
                if start >= window_start - 0.05:
                    filtered.append(word)
            if not filtered:
                continue
            row_copy["words"] = filtered
            row_copy["start"] = float(
                filtered[0].get("start", filtered[0].get("start_time", window_start))
            )
            last = filtered[-1]
            row_copy["end"] = float(
                last.get("end", last.get("end_time", last.get("start", row_copy["start"])))
            )
            _rebase_transcript_row(row_copy, window_start)
            kept.append(row_copy)
            continue

        start = _row_start_sec(row_copy)
        end = _row_end_sec(row_copy)
        if end is not None and end <= window_start - 0.05:
            continue
        if start is not None and start >= media_duration_sec:
            continue
        if start is None and end is None:
            continue
        if start is not None:
            row_copy["start"] = max(start, window_start)
        if end is not None:
            row_copy["end"] = end
        _rebase_transcript_row(row_copy, window_start)
        kept.append(row_copy)

    if not kept:
        raise ValueError(
            f"No transcript rows in last {window_sec:.0f}s of prepped media "
            f"(duration={media_duration_sec:.2f}s)."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Preserve convert_transcript_json / generate_full_dsl dict-index shape when present.
    if isinstance(original, list):
        payload: Any = kept
    else:
        payload = {str(i): row for i, row in enumerate(kept)}
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return output_path, window_start


def prepped_anchor_duration_sec(prep: dict[str, Any]) -> float:
    anchor = Path(str(prep["prepped_audio_wav"]))
    if not anchor.is_file():
        videos = prep.get("prepped_videos") or []
        if not videos:
            raise FileNotFoundError("No prepped media to measure duration.")
        anchor = Path(str(videos[0]))
    return ffprobe_duration(anchor)


def _generate_interview_dsl(
    *,
    simplified_json: Path,
    interview_dsl: Path,
    segment_id: str,
    state: dict,
    use_phrase_gates: bool,
) -> None:
    from harness_autocut_common import run_cmd
    from harness_episode_lib import REPO_ROOT, podcast_phrase_cli_args

    cmd = [
        sys.executable,
        str(REPO_ROOT / "generate_full_dsl.py"),
        str(simplified_json),
        "--segment",
        segment_id,
        "--output",
        str(interview_dsl),
    ]
    if use_phrase_gates:
        cmd.extend(podcast_phrase_cli_args(state))
    run_cmd(cmd)


def render_preview_one_min_test(
    state: dict,
    working: Path,
    *,
    allow_overwrite: bool,
) -> tuple[Path, str, bool]:
    """
    Build DSL and render preview 1-min test in the preview sandbox.

    Returns ``(output_mp4, preview_render_mode, sync_ab_required)``.
    """
    import sys

    from episode_segments import MAIN_SEGMENT_KEY, segments_path, upsert_segment
    from harness_autocut_common import render_dsl, run_cmd
    from harness_av_sync_lib import (
        load_failed_sync_confidence_flag,
        run_sync_ab_one_min_tests,
    )
    from harness_episode_lib import REPO_ROOT, pick_interview_videos, podcast_swap_speaker_ids_cli_args
    from harness_overwrite_guard import refuse_overwrite
    from piab_lib import mark_piab_sync_ab_steps, mark_step, save_piab_state

    temp = Path(state["paths"]["temp"])
    output_dir = Path(state["paths"]["output"])
    temp.mkdir(parents=True, exist_ok=True)

    ben, guest, wide = pick_interview_videos(state["main_prepped"]["prepped_videos"])
    audio_wav = Path(state["main_prepped"]["prepped_audio_wav"])
    detail_json = Path(state["main_transcript_json"])
    simplified = temp / "interview_transcript_simplified.json"
    interview_dsl = temp / "interview.dsl"
    dsl_source = temp / "interview_transcript_for_dsl.json"

    mark_step(
        state,
        "10p_fast_preview_one_min",
        title="Fast Preview 1-min test",
        status="in_progress",
    )
    state["resume_at"] = "10p_fast_preview_one_min"
    save_piab_state(working, state)

    for path in (simplified, interview_dsl, dsl_source):
        refuse_overwrite(path, allow_overwrite=allow_overwrite)

    convert_cmd = [
        sys.executable,
        str(REPO_ROOT / "convert_transcript_json.py"),
        str(detail_json),
        "-o",
        str(simplified),
    ]
    convert_cmd.extend(podcast_swap_speaker_ids_cli_args(state))
    run_cmd(convert_cmd)

    media_duration = prepped_anchor_duration_sec(state["main_prepped"])
    use_tail, tail_reason = should_use_tail_preview(
        simplified_json=simplified,
        prepped_duration_sec=media_duration,
        state=state,
    )
    preview_render_mode = "tail_autocut" if use_tail else "head_autocut"

    media_origin = 0.0
    if use_tail:
        _, media_origin = slice_simplified_transcript_last_seconds(
            simplified,
            output_path=dsl_source,
            window_sec=60.0,
            media_duration_sec=media_duration,
        )
        dsl_input = dsl_source
        use_phrase_gates = False
    else:
        dsl_input = simplified
        use_phrase_gates = True

    segment_id = MAIN_SEGMENT_KEY
    upsert_segment(
        temp,
        segment_id,
        {
            "audio_file": str(audio_wav),
            "audio_offset": media_origin,
            "enable_color_match": False,
            "video_files": {
                "speaker_0": {"file": str(ben), "offset": media_origin},
                "speaker_1": {"file": str(guest), "offset": media_origin},
                "wide": {"file": str(wide), "offset": media_origin},
            },
            "transcript_file": str(dsl_input),
        },
        allow_overwrite=allow_overwrite,
    )
    state["main_segment_id"] = segment_id
    state["segments_file"] = str(segments_path(temp))

    _generate_interview_dsl(
        simplified_json=dsl_input,
        interview_dsl=interview_dsl,
        segment_id=segment_id,
        state=state,
        use_phrase_gates=use_phrase_gates,
    )
    state["interview_dsl"] = str(interview_dsl)
    state["fast_preview"] = state.get("fast_preview") or {}
    if isinstance(state["fast_preview"], dict):
        state["fast_preview"]["preview_render_mode"] = preview_render_mode
        state["fast_preview"]["tail_reason"] = tail_reason if use_tail else None

    sync_flag = load_failed_sync_confidence_flag(temp)
    if sync_flag is None and state.get("sync_confidence_failed"):
        sync_flag = {"failed": True}

    if sync_flag:
        ab_result = run_sync_ab_one_min_tests(
            state,
            allow_overwrite=allow_overwrite,
            one_min_no_offset_name=PREVIEW_ONE_MIN_NO_OFFSET,
            one_min_forced_offset_name=PREVIEW_ONE_MIN_FORCED_OFFSET,
        )
        mark_piab_sync_ab_steps(state, ab_result=ab_result)
        out_mp4 = Path(ab_result["one_min_no_offset"])
        sync_ab_required = True
        state["resume_at"] = "10a_sync_offset_approval"
    else:
        out_mp4 = output_dir / PREVIEW_ONE_MIN_DEFAULT
        render_dsl(
            interview_dsl,
            out_mp4,
            temp,
            max_seconds=60,
            allow_overwrite=allow_overwrite,
        )
        sync_ab_required = False
        state["podcast_autocut_test_mp4"] = str(out_mp4)
        mark_step(
            state,
            "11_one_min_approval",
            title="Fast Preview approval",
            status="awaiting_user",
            preview=True,
        )
        state["resume_at"] = "11_one_min_approval"

    mark_step(
        state,
        "10p_fast_preview_one_min",
        title="Fast Preview 1-min test",
        status="completed",
        output_mp4=str(out_mp4),
        preview_render_mode=preview_render_mode,
        sync_ab=sync_ab_required,
    )
    save_piab_state(working, state)
    return out_mp4, preview_render_mode, sync_ab_required

