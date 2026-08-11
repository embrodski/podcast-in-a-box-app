#!/usr/bin/env python3
"""Extract midpoint preview frames for each session video."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from piab_lib import (
    extract_midpoint_frame,
    load_piab_state,
    mark_step,
    print_json,
    save_piab_state,
)

def main() -> int:
    parser = argparse.ArgumentParser(description="Extract PIAB video midpoint frames.")
    parser.add_argument("working_folder", type=Path)
    args = parser.parse_args()

    try:
        state = load_piab_state(args.working_folder)
        preview_dir = Path(state["paths"]["previews"])
        preview_dir.mkdir(parents=True, exist_ok=True)
        for stale_preview in preview_dir.glob("*.jpg"):
            stale_preview.unlink()
        previews = []
        video_items = [
            item for item in state.get("session_files", []) if item.get("kind") == "video"
        ]
        for camera_number, item in enumerate(video_items, start=1):
            src = Path(item["path"])
            if not src.is_file():
                # Already copied into Raw — try standard names / original map later.
                continue
            camera_label = f"Camera {camera_number}"
            out = preview_dir / f"{camera_label}.jpg"
            extract_midpoint_frame(src, out)
            previews.append(
                {
                    "camera": camera_label,
                    "source": str(src),
                    "source_name": src.name,
                    "preview": str(out),
                    "duration_sec": item.get("duration_sec"),
                }
            )
        if not previews:
            raise FileNotFoundError(
                "No session videos found on disk to preview. "
                "Have they already been moved?"
            )
        state["video_previews"] = previews
        mark_step(
            state,
            "03_label_videos",
            title="Label videos",
            status="awaiting_user",
            preview_count=len(previews),
        )
        state["resume_at"] = "03_label_videos"
        save_piab_state(args.working_folder, state)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print_json(
        {
            "video_previews": previews,
            "preview_folder": str(preview_dir),
            "working_folder": str(args.working_folder.resolve()),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
