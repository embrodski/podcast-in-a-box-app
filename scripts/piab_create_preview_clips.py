#!/usr/bin/env python3
"""Create 300-second preview clips from labeled Raw files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness_overwrite_guard import HarnessOverwriteError, OVERWRITE_EXIT_CODE
from piab_fast_preview_lib import create_preview_clips
from piab_lib import load_piab_state, mark_step, print_json, save_piab_state


def main() -> int:
    parser = argparse.ArgumentParser(description="Create PIAB Fast Preview source clips.")
    parser.add_argument("working_folder", type=Path)
    parser.add_argument("--allow-overwrite", action="store_true")
    args = parser.parse_args()

    try:
        working = args.working_folder.resolve()
        info = create_preview_clips(working, allow_overwrite=args.allow_overwrite)
        state = load_piab_state(working)
        mark_step(
            state,
            "05b_fast_preview_clips",
            title="Fast Preview source clips",
            status="completed",
            **info,
        )
        save_piab_state(working, state)
    except HarnessOverwriteError:
        return OVERWRITE_EXIT_CODE
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print_json(info)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
