#!/usr/bin/env python3
"""Record user choice after PIAB sync offset A/B 1-minute tests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness_av_sync_lib import (
    SYNC_CHOICE_FORCED_OFFSET,
    SYNC_CHOICE_START_ALIGNED,
    apply_sync_offset_choice,
    load_failed_sync_confidence_flag,
)
from piab_lib import load_piab_state, mark_piab_sync_choice_completed, print_json, save_piab_state


def main() -> int:
    parser = argparse.ArgumentParser(description="Record PIAB sync offset A/B user choice.")
    parser.add_argument("working_folder", type=Path)
    parser.add_argument(
        "--choice",
        required=True,
        choices=(SYNC_CHOICE_START_ALIGNED, SYNC_CHOICE_FORCED_OFFSET),
        help="start_aligned = 1 Min Test no offset; forced_offset = forced audio offset.",
    )
    args = parser.parse_args()

    try:
        state = load_piab_state(args.working_folder)
        temp = Path(state["paths"]["temp"])
        if not load_failed_sync_confidence_flag(temp):
            print(
                "WARNING: failed-sync-confidence flag not set; recording choice anyway.",
                file=sys.stderr,
            )
        apply_sync_offset_choice(state, args.choice)
        mark_piab_sync_choice_completed(state)
        save_piab_state(args.working_folder, state)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print_json(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
