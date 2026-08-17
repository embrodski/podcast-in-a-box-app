#!/usr/bin/env python3
"""Record Fast Preview approval and prepare for full-length prep."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from piab_fast_preview_lib import (
    resolve_preview_one_min_path,
    restore_canonical_paths,
    save_fast_preview_approval,
    snapshot_preview_sandbox_artifacts,
)
from piab_lib import load_piab_state, mark_step, print_json, save_piab_state


def approve_fast_preview(working_folder: Path) -> dict:
    working = working_folder.resolve()
    state = load_piab_state(working)

    preview_mode = (state.get("fast_preview") or {}).get("preview_render_mode", "head_autocut")
    sync_ab = bool((state.get("fast_preview") or {}).get("sync_ab_required"))
    preview_path = resolve_preview_one_min_path(state, working)

    save_fast_preview_approval(
        state,
        sync_offset_choice=state.get("sync_offset_choice"),
        swap_speaker_ids=bool(state.get("swap_speaker_ids")),
        preview_render_mode=str(preview_mode),
        preview_one_min_path=str(preview_path.resolve()),
        sync_ab_required=sync_ab,
    )
    snapshot_preview_sandbox_artifacts(state)

    restore_canonical_paths(state)

    for key in (
        "main_prepped",
        "main_prepped_forced_offset",
        "main_transcript_json",
        "main_combined_audio",
        "main_clean_audio",
        "interview_dsl",
        "podcast_autocut_test_mp4",
        "podcast_autocut_test_mp4_no_offset",
        "podcast_autocut_test_mp4_forced_offset",
    ):
        state.pop(key, None)

    mark_step(
        state,
        "11_one_min_approval",
        title="Fast Preview approval",
        status="completed",
        preview=True,
        preview_one_min_path=str(preview_path),
    )
    mark_step(
        state,
        "12_estimate_full",
        title="Estimate full interview render",
        status="completed",
        auto_continue=True,
        note="Skipped Estimate B — auto-continuing after Fast Preview approval.",
    )
    state["resume_at"] = "13_queued_full"
    save_piab_state(working, state)
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description="Approve PIAB Fast Preview and continue to full prep.")
    parser.add_argument("working_folder", type=Path)
    args = parser.parse_args()
    try:
        state = approve_fast_preview(args.working_folder)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print_json(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
