#!/usr/bin/env python3
"""
Fix Host/Guest audio appearing swapped in the autocut (speaker-ID mapping).

Assumes Raw/ and Input/ files are labeled correctly. Toggles transcript speaker-id
mapping, re-converts the existing detail transcript, regenerates interview.dsl, and
re-renders 1 Min Test.mp4. Does not swap Raw files or re-run video-sync / transcribe.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness_overwrite_guard import HarnessOverwriteError, OVERWRITE_EXIT_CODE
from piab_lib import load_piab_state, mark_step, print_json, save_piab_state
from piab_rerun_one_min import rerun_one_min_test


def fix_audio_speaker_swap(
    working_folder: Path,
    *,
    allow_overwrite: bool = False,
) -> Path:
    """Toggle speaker-id swap and re-render the 1-minute test from existing Input."""
    working = working_folder.resolve()
    state = load_piab_state(working)

    if not state.get("main_prepped") or not state.get("main_transcript_json"):
        raise FileNotFoundError(
            "Missing prepped Input or transcript. Complete prep through 1-min test first, "
            "or use piab_swap.py --files audio only when Raw Host/Guest WAV files were "
            "mislabeled during labeling."
        )

    transcript = Path(state["main_transcript_json"])
    if not transcript.is_file():
        raise FileNotFoundError(f"Detail transcript not found: {transcript}")

    previous = bool(state.get("swap_speaker_ids"))
    state["swap_speaker_ids"] = not previous
    mark_step(
        state,
        "swap_or_relabel",
        title="Fix Host/Guest audio speaker mapping",
        status="completed",
        actions=[
            f"swap_speaker_ids={state['swap_speaker_ids']} (was {previous})",
            "reconvert transcript with --swap-speaker-ids",
            "regenerate interview.dsl",
            "re-render 1 Min Test (or A/B pair if sync confidence failed)",
        ],
        raw_files_unchanged=True,
        input_files_unchanged=True,
    )
    save_piab_state(working, state)

    out_mp4 = rerun_one_min_test(working, allow_overwrite=allow_overwrite)
    return out_mp4


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fix Host/Guest audio swapped in the edit (speaker-ID mapping only). "
            "Raw and Input stay unchanged; reconvert transcript, regenerate DSL, "
            "re-render 1 Min Test."
        )
    )
    parser.add_argument("working_folder", type=Path)
    parser.add_argument("--allow-overwrite", action="store_true")
    args = parser.parse_args()

    try:
        out_mp4 = fix_audio_speaker_swap(
            args.working_folder,
            allow_overwrite=args.allow_overwrite,
        )
    except HarnessOverwriteError:
        return OVERWRITE_EXIT_CODE
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    state = load_piab_state(args.working_folder)
    print_json(
        {
            "one_min_test": str(out_mp4),
            "swap_speaker_ids": state.get("swap_speaker_ids"),
            "message": (
                f"Speaker mapping updated and 1 Min Test re-rendered: {out_mp4}. "
                "Raw and Input prepped files were not changed."
            ),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
