#!/usr/bin/env python3
"""Apply Host/Guest/Wide labels: copy MultiCorder files into Raw with standard names."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness_overwrite_guard import HarnessOverwriteError, OVERWRITE_EXIT_CODE
from piab_lib import (
    APPLY_LABELS_RESUME_AT,
    LabelApplyCancelled,
    estimate_prep_through_one_min,
    load_piab_state,
    mark_step,
    move_labeled_media,
    print_json,
    save_piab_state,
    utc_now_iso,
)

_LABEL_ALIASES = {
    "do_not_use": "do_not_use",
    "donotuse": "do_not_use",
    "skip": "do_not_use",
    "unused": "do_not_use",
}


def _parse_labels(raw: str) -> dict[str, str]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Labels JSON must be an object mapping path -> role")
    return normalize_label_roles(
        {str(k): str(v).strip().lower().replace(" ", "_").replace("-", "_") for k, v in data.items()}
    )


def normalize_label_roles(labels: dict[str, str]) -> dict[str, str]:
    return {k: _LABEL_ALIASES.get(v, v) for k, v in labels.items()}


def _persist_label_paths(
    state: dict,
    *,
    video_labels: dict[str, str],
    audio_labels: dict[str, str],
) -> None:
    state["labels"] = {
        "videos": {Path(k).name: v for k, v in video_labels.items()},
        "audios": {Path(k).name: v for k, v in audio_labels.items()},
    }
    state["label_paths"] = {
        "videos": {str(Path(k).resolve()): v for k, v in video_labels.items()},
        "audios": {str(Path(k).resolve()): v for k, v in audio_labels.items()},
    }


def _record_label_apply_cancel(
    state: dict,
    exc: LabelApplyCancelled,
) -> dict:
    stamp = utc_now_iso()
    completed = list(exc.completed)
    remaining = list(exc.remaining)
    original_paths: dict[str, str] = {}
    copied: dict[str, str] = {}
    for item in completed:
        dest_name = str(item.get("dest_name") or Path(str(item.get("dest") or "")).name)
        original_paths[dest_name] = str(item.get("src") or "")
        role = str(item.get("role") or "")
        kind = str(item.get("kind") or "video")
        if role:
            key = role if kind == "video" else f"{role}_audio"
            copied[key] = str(item.get("dest") or "")
    payload = {
        "status": "cancelled",
        "cancelled_at": stamp,
        "completed": completed,
        "remaining": remaining,
        "partial_deleted": exc.partial,
    }
    state["label_apply"] = payload
    state["original_paths"] = original_paths
    state["copied_raw"] = copied
    state["moved_raw"] = copied
    state["resume_at"] = APPLY_LABELS_RESUME_AT
    mark_step(
        state,
        APPLY_LABELS_RESUME_AT,
        title="Copy labeled files into Raw",
        status="aborted",
        aborted_at=stamp,
        completed=completed,
        remaining=remaining,
        partial_deleted=exc.partial,
    )
    state["last_abort"] = {
        "aborted_at": stamp,
        "message": "Label apply cancelled by closing the window.",
        "interrupted_step": APPLY_LABELS_RESUME_AT,
        "resume_at": APPLY_LABELS_RESUME_AT,
        "completed_copies": completed,
        "remaining_copies": remaining,
        "partial_deleted": exc.partial,
    }
    return state


def apply_labeled_media_session(
    working_folder: Path,
    *,
    video_labels: dict[str, str],
    audio_labels: dict[str, str],
    allow_overwrite: bool = False,
    on_copy: Callable[[Path, Path, int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict:
    """Copy labeled media into Raw and update session state."""
    folder = working_folder.resolve()
    state = load_piab_state(folder)
    video_labels = normalize_label_roles(video_labels)
    audio_labels = normalize_label_roles(audio_labels)
    _persist_label_paths(
        state,
        video_labels=video_labels,
        audio_labels=audio_labels,
    )
    mark_step(
        state,
        APPLY_LABELS_RESUME_AT,
        title="Copy labeled files into Raw",
        status="in_progress",
    )
    save_piab_state(folder, state)

    try:
        state = move_labeled_media(
            state,
            video_labels=video_labels,
            audio_labels=audio_labels,
            allow_overwrite=allow_overwrite,
            on_copy=on_copy,
            should_cancel=should_cancel,
        )
    except LabelApplyCancelled as exc:
        state = _record_label_apply_cancel(state, exc)
        save_piab_state(folder, state)
        raise
    from piab_fast_preview_lib import clear_preview_sandbox

    clear_preview_sandbox(state, folder)
    dur = float(state.get("source_duration_sec") or 0)
    eta = estimate_prep_through_one_min(dur)
    state["estimate_prep"] = eta
    try:
        from piab_fast_preview_lib import (
            estimate_fast_preview_prep,
            fast_preview_eligible,
            max_raw_video_duration_sec,
        )

        raw = Path(state["paths"]["raw"])
        max_video = max_raw_video_duration_sec(raw)
        state["max_video_duration_sec"] = max_video
        state["fast_preview_eligible"] = fast_preview_eligible(raw)
        state["estimate_prep_fast"] = estimate_fast_preview_prep()
    except FileNotFoundError:
        state["fast_preview_eligible"] = False
    mark_step(
        state,
        APPLY_LABELS_RESUME_AT,
        title="Copy labeled files into Raw",
        status="completed",
        moved=state.get("moved_raw"),
    )
    mark_step(
        state,
        "03_label_videos",
        title="Label videos",
        status="completed",
        moved=state.get("moved_raw"),
    )
    mark_step(
        state,
        "04_label_audio",
        title="Label audio",
        status="completed",
        moved=state.get("moved_raw"),
    )
    mark_step(
        state,
        "05_estimate_prep",
        title="Estimate prep through 1-min test",
        status="awaiting_user",
        **eta,
    )
    state["resume_at"] = "05_estimate_prep"
    save_piab_state(folder, state)
    return {
        "working_folder": str(folder),
        "moved_raw": state.get("moved_raw"),
        "estimate_prep": state.get("estimate_prep"),
        "message": (
            f"Files copied into Raw (sources left in place). Prep through 1-min test will take "
            f"{eta['summary']} (source ~{eta['breakdown']['source_duration_human']}). "
            "Ask the user to confirm before starting prep."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply PIAB video/audio labels and copy files into Raw.")
    parser.add_argument("working_folder", type=Path)
    parser.add_argument(
        "--video-labels-json",
        required=True,
        help='JSON object: {"E:\\\\...\\\\file.mp4": "host"|"guest"|"wide"|"do_not_use", ...}',
    )
    parser.add_argument(
        "--audio-labels-json",
        required=True,
        help='JSON object: {"E:\\\\...\\\\file.wav": "host"|"guest"|"do_not_use", ...}',
    )
    parser.add_argument("--allow-overwrite", action="store_true")
    args = parser.parse_args()

    try:
        result = apply_labeled_media_session(
            args.working_folder,
            video_labels=_parse_labels(args.video_labels_json),
            audio_labels=_parse_labels(args.audio_labels_json),
            allow_overwrite=args.allow_overwrite,
        )
    except HarnessOverwriteError:
        return OVERWRITE_EXIT_CODE
    except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
