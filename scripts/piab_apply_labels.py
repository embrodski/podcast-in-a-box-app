#!/usr/bin/env python3
"""Apply Host/Guest/Wide labels: copy MultiCorder files into Raw with standard names."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness_overwrite_guard import HarnessOverwriteError, OVERWRITE_EXIT_CODE
from piab_lib import (
    estimate_prep_through_one_min,
    load_piab_state,
    mark_step,
    move_labeled_media,
    print_json,
    save_piab_state,
)


def _parse_labels(raw: str) -> dict[str, str]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Labels JSON must be an object mapping path -> role")
    return {str(k): str(v).strip().lower().replace(" ", "_").replace("-", "_") for k, v in data.items()}


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
        state = load_piab_state(args.working_folder)
        video_labels = _parse_labels(args.video_labels_json)
        audio_labels = _parse_labels(args.audio_labels_json)
        # Normalize aliases
        aliases = {
            "do_not_use": "do_not_use",
            "donotuse": "do_not_use",
            "skip": "do_not_use",
            "unused": "do_not_use",
        }
        video_labels = {k: aliases.get(v, v) for k, v in video_labels.items()}
        audio_labels = {k: aliases.get(v, v) for k, v in audio_labels.items()}

        state = move_labeled_media(
            state,
            video_labels=video_labels,
            audio_labels=audio_labels,
            allow_overwrite=args.allow_overwrite,
        )
        dur = float(state.get("source_duration_sec") or 0)
        eta = estimate_prep_through_one_min(dur)
        state["estimate_prep"] = eta
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
        save_piab_state(args.working_folder, state)
    except HarnessOverwriteError:
        return OVERWRITE_EXIT_CODE
    except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print_json(
        {
            "working_folder": str(Path(args.working_folder).resolve()),
            "moved_raw": state.get("moved_raw"),
            "estimate_prep": state.get("estimate_prep"),
            "message": (
                f"Files copied into Raw (sources left in place). Prep through 1-min test will take "
                f"{eta['summary']} (source ~{eta['breakdown']['source_duration_human']}). "
                "Ask the user to confirm before starting prep."
            ),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
