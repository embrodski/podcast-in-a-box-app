#!/usr/bin/env python3
"""Create a Podcast In A Box working folder and state after the user confirms a scan."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness_overwrite_guard import HarnessOverwriteError, OVERWRITE_EXIT_CODE, refuse_overwrite
from harness_delivery_prompt import delivery_from_cli, merge_delivery_into_state
from piab_lib import (
    DEFAULT_SCAN_ROOT,
    DEFAULT_WORK_ROOT,
    MediaInfo,
    collect_session_scan,
    ensure_subfolders,
    mark_step,
    new_piab_state,
    print_json,
    resolve_init_layout,
    save_piab_state,
)


def _session_files_from_scan(data: dict) -> list[MediaInfo]:
    return [
        MediaInfo(
            path=f["path"],
            name=f["name"],
            kind=f["kind"],
            mtime=Path(f["path"]).stat().st_mtime,
            mtime_iso=f["mtime_iso"],
            duration_sec=float(f["duration_sec"]),
        )
        for f in data["files"]
    ]


def _print_requirement_errors(requirements: dict, *, scan_dir: Path) -> None:
    for line in requirements.get("missing", []):
        print(f"ERROR: {line}", file=sys.stderr)
    for line in requirements.get("warnings", []):
        print(f"WARNING: {line}", file=sys.stderr)
    unrecognized = requirements.get("unrecognized_media") or []
    if unrecognized:
        print(
            f"WARNING: Unrecognized media in {scan_dir} (not MultiCorder pattern):",
            file=sys.stderr,
        )
        for name in unrecognized:
            print(f"  - {name}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Init Podcast In A Box working folder.")
    parser.add_argument(
        "--name",
        help="Working folder name under --root (default mode).",
    )
    parser.add_argument(
        "--working-folder",
        type=Path,
        help="Special mode: folder that already contains the MultiCorder sources.",
    )
    parser.add_argument(
        "--mode",
        choices=("default", "special"),
        help="default: create/use a subfolder under --root. special: use --working-folder.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_WORK_ROOT,
        help="Folder that will contain the new session subfolder (default mode).",
    )
    parser.add_argument(
        "--scan-root",
        type=Path,
        default=DEFAULT_SCAN_ROOT,
        help="Folder to scan for MultiCorder dumps in default mode.",
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        help="Limit re-scan candidates to this local modified date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--from-scan-json",
        type=Path,
        help="Optional JSON from piab_scan_session.py (skips re-scan).",
    )
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Allow init even when minimum video/audio counts are not met.",
    )
    parser.add_argument("--allow-overwrite", action="store_true")
    parser.add_argument(
        "--delivery-email",
        help="Recipient email for finished-video delivery (requires --confirm-delivery-email).",
    )
    parser.add_argument(
        "--confirm-delivery-email",
        action="store_true",
        help="Confirm --delivery-email for non-interactive delivery opt-in.",
    )
    args = parser.parse_args()

    if args.working_folder is not None and args.name is not None:
        print("ERROR: Use --working-folder or --name, not both.", file=sys.stderr)
        return 1
    if args.mode == "special" and args.working_folder is None:
        print("ERROR: --mode special requires --working-folder.", file=sys.stderr)
        return 1
    if args.working_folder is None and not args.name and not args.from_scan_json:
        print(
            "ERROR: Provide --name (default mode) or --working-folder (special mode).",
            file=sys.stderr,
        )
        return 1

    try:
        working, scan_dir, session_name, session_mode = resolve_init_layout(
            mode=args.mode,
            root=args.root,
            name=args.name,
            working_folder=args.working_folder,
            scan_root=args.scan_root,
        )

        if working.exists() and any(working.iterdir()):
            refuse_overwrite(
                working / "podcast-in-a-box.json",
                allow_overwrite=args.allow_overwrite,
                label="existing working folder state",
            )

        if args.from_scan_json:
            scan_data = json.loads(args.from_scan_json.read_text(encoding="utf-8"))
            session_files = _session_files_from_scan(scan_data)
            scan_root = Path(scan_data["scan_root"])
        else:
            scan_data = collect_session_scan(
                scan_dir,
                date_filter=args.date,
            )
            requirements = scan_data["requirements"]
            if session_mode == "special" and not requirements["ok"]:
                _print_requirement_errors(requirements, scan_dir=scan_dir)
                print(
                    f"\nSpecial folder {working} is missing required MultiCorder files.\n"
                    "Expected top-level camera MP4s and Output WAVs in that folder.",
                    file=sys.stderr,
                )
                return 1
            if not requirements["ok"] and not args.allow_incomplete:
                _print_requirement_errors(requirements, scan_dir=scan_dir)
                print(
                    "\nRefusing init: session does not meet PIAB minimum file counts.\n"
                    "Re-run with --allow-incomplete to proceed anyway.",
                    file=sys.stderr,
                )
                return 1
            if not requirements["ok"]:
                _print_requirement_errors(requirements, scan_dir=scan_dir)
            session_files = _session_files_from_scan(scan_data)
            scan_root = scan_dir

        working.mkdir(parents=True, exist_ok=True)
        ensure_subfolders(working)
        state = new_piab_state(
            working,
            name=session_name,
            scan_root=scan_root,
            session_files=session_files,
            session_mode=session_mode,
        )
        mark_step(
            state,
            "01_scan_confirm",
            title="Scan and confirm session files",
            status="completed",
            file_count=len(session_files),
            session_mode=session_mode,
            scan_root=str(scan_root),
        )
        mark_step(
            state,
            "02_create_folder",
            title="Create working folder",
            status="completed",
            working_folder=str(working),
        )
        if args.delivery_email:
            merge_delivery_into_state(
                state,
                delivery_from_cli(
                    email=args.delivery_email,
                    confirm=args.confirm_delivery_email,
                ),
            )
        state["resume_at"] = "03_label_videos"
        save_piab_state(working, state)
    except HarnessOverwriteError:
        return OVERWRITE_EXIT_CODE
    except (FileNotFoundError, RuntimeError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print_json(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
