#!/usr/bin/env python3
"""
Swap Host/Guest raw files in Raw, and/or toggle transcript speaker-id swap.

For "Host and Guest audio are swapped in the edit" (Raw labeled correctly), use
``piab_fix_audio_speaker_swap.py`` instead — it only remaps speaker IDs and
re-renders the 1-min test. Use ``--files audio`` only when Raw WAV files were
physically mislabeled during mic labeling.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from piab_lib import load_piab_state, mark_step, print_json, save_piab_state, swap_host_guest_files


def main() -> int:
    parser = argparse.ArgumentParser(description="PIAB Host/Guest swap helpers.")
    parser.add_argument("working_folder", type=Path)
    parser.add_argument(
        "--files",
        choices=("video", "audio", "both", "none"),
        default="none",
        help=(
            "Swap Host/Guest Raw Video and/or Audio filenames in Raw. "
            "Use only when those Raw source files were mislabeled during labeling. "
            "If the edit sounds swapped but Raw is correct, use piab_fix_audio_speaker_swap.py."
        ),
    )
    parser.add_argument(
        "--speaker-ids",
        choices=("on", "off", "toggle", "leave"),
        default="leave",
        help="Set convert_transcript_json --swap-speaker-ids for autocut.",
    )
    args = parser.parse_args()

    try:
        state = load_piab_state(args.working_folder)
        raw = Path(state["paths"]["raw"])
        actions: list[str] = []
        if args.files != "none":
            actions.extend(swap_host_guest_files(raw, kind=args.files))
            # Clear stale prepped paths so prep must be re-run.
            for key in (
                "main_combined_audio",
                "main_clean_audio",
                "main_prepped",
                "main_transcript_json",
                "main_segment_id",
                "interview_dsl",
                "podcast_autocut_test_mp4",
            ):
                state.pop(key, None)
            actions.append("cleared downstream prep/render state (re-run prep)")

        if args.speaker_ids != "leave":
            current = bool(state.get("swap_speaker_ids"))
            if args.speaker_ids == "on":
                state["swap_speaker_ids"] = True
            elif args.speaker_ids == "off":
                state["swap_speaker_ids"] = False
            else:
                state["swap_speaker_ids"] = not current
            actions.append(f"swap_speaker_ids={state['swap_speaker_ids']}")

        mark_step(
            state,
            "swap_or_relabel",
            title="Swap / re-label",
            status="completed",
            actions=actions,
        )
        save_piab_state(args.working_folder, state)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print_json(
        {
            "actions": actions,
            "swap_speaker_ids": state.get("swap_speaker_ids"),
            "resume_at": state.get("resume_at"),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
