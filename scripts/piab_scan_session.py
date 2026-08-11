#!/usr/bin/env python3
"""Scan E:\\PodcastRoom (or --root) for a MultiCorder session cluster."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from piab_lib import (
    DEFAULT_SCAN_ROOT,
    collect_session_scan,
    print_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Scan for MultiCorder session clusters. "
            "By default selects the newest cluster (index 0)."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_SCAN_ROOT,
        help=f"Folder to scan (default: {DEFAULT_SCAN_ROOT})",
    )
    parser.add_argument("--mtime-tol-sec", type=float, default=60.0)
    parser.add_argument("--duration-tol-sec", type=float, default=2.0)
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        help="Limit candidates to this local modified date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--cluster-index",
        type=int,
        default=0,
        help="Which session cluster to use when several exist (0=newest).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 when PIAB minimum file counts are not met.",
    )
    args = parser.parse_args()

    try:
        payload = collect_session_scan(
            args.root,
            mtime_tol_sec=args.mtime_tol_sec,
            duration_tol_sec=args.duration_tol_sec,
            date_filter=args.date,
            cluster_index=args.cluster_index,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print_json(payload)
    if args.strict and not payload["requirements"]["ok"]:
        for line in payload["requirements"]["missing"]:
            print(f"ERROR: {line}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
