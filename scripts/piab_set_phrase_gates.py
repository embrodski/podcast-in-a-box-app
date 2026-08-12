#!/usr/bin/env python3
"""View or update shared podcast phrase gates (podcast-phrase-gates.json)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from podcast_phrase_gates import (
    load_phrase_gates,
    phrase_gates_path,
    save_phrase_gates,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="View/update podcast phrase gates JSON.")
    parser.add_argument("--show", action="store_true", help="Print merged gates (default).")
    parser.add_argument("--from-json", type=Path, help="Replace gates from this JSON object file.")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE", help="Patch one field.")
    args = parser.parse_args()

    try:
        if args.from_json:
            data = json.loads(args.from_json.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("--from-json must contain a JSON object.")
            path = save_phrase_gates(data)
            print(path)
            return 0

        updates: dict = {}
        for item in args.set or []:
            if "=" not in item:
                raise ValueError(f"Invalid --set (expected KEY=VALUE): {item!r}")
            key, value = item.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key in (
                "unpause_phrases",
                "end_phrases",
                "start_countdown_tokens",
                "start_countdown_suffix_tokens",
                "start_phrase_countdown_tokens",
                "start_phrase_countdown_suffix",
            ):
                updates[key] = [part.strip() for part in value.split("|") if part.strip()]
            elif key.endswith("_sec"):
                updates[key] = float(value)
            else:
                updates[key] = value
        if updates:
            path = save_phrase_gates(updates)
            print(path)

        gates = load_phrase_gates()
        print(json.dumps({"path": str(phrase_gates_path()), **gates}, indent=2))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
