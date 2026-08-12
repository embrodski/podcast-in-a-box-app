"""Tests for shared podcast phrase gates."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from podcast_phrase_gates import (
    EMBEDDED_DEFAULTS,
    end_phrases_from_gates,
    load_phrase_gates,
    podcast_phrase_cli_args,
    save_phrase_gates,
    start_countdown_tokens_from_gates,
    start_trigger_phrase_from_gates,
)


class PhraseGatesTests(unittest.TestCase):
    def test_embedded_defaults_separate_trigger_and_countdown(self) -> None:
        self.assertIn("start_trigger_phrase", EMBEDDED_DEFAULTS)
        self.assertIn("start_countdown_tokens", EMBEDDED_DEFAULTS)
        self.assertIn("end_phrases", EMBEDDED_DEFAULTS)
        self.assertIn("pause_phrase", EMBEDDED_DEFAULTS)
        self.assertGreaterEqual(len(EMBEDDED_DEFAULTS["end_phrases"]), 1)
        self.assertNotIn("in", EMBEDDED_DEFAULTS["start_trigger_phrase"].lower().split()[-3:])

    def test_legacy_combined_start_phrase_splits_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "podcast-phrase-gates.json").write_text(
                json.dumps(
                    {
                        "start_phrase": (
                            "I solemnly swear I'm up to no good, in five four three two"
                        ),
                        "start_phrase_countdown_tokens": [
                            "five",
                            "four",
                            "three",
                            "two",
                        ],
                    }
                ),
                encoding="utf-8",
            )
            gates = load_phrase_gates(repo_root=root, create_file_if_missing=False)
            self.assertEqual(
                start_trigger_phrase_from_gates(gates),
                "I solemnly swear I'm up to no good",
            )
            self.assertEqual(
                start_countdown_tokens_from_gates(gates),
                ["five", "four", "three", "two"],
            )

    def test_state_overrides_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "podcast-phrase-gates.json").write_text(
                json.dumps({"start_trigger_phrase": "From file."}),
                encoding="utf-8",
            )
            gates = load_phrase_gates(
                repo_root=root,
                state_overrides={"start_trigger_phrase": "From state."},
                create_file_if_missing=False,
            )
            self.assertEqual(start_trigger_phrase_from_gates(gates), "From state.")

    def test_cli_args_use_trigger_and_countdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_phrase_gates({}, repo_root=root)
            args = podcast_phrase_cli_args({})
            self.assertIn("--start-trigger-phrase", args)
            self.assertEqual(args.count("--end-phrase"), 2)
            self.assertIn("--start-phrase-countdown", args)
            self.assertIn("--start-phrase-countdown-suffix", args)
            self.assertIn("--pause-phrase", args)

    def test_end_phrases_from_gates(self) -> None:
        gates = load_phrase_gates(create_file_if_missing=False)
        phrases = end_phrases_from_gates(gates)
        self.assertGreaterEqual(len(phrases), 2)
        self.assertIn("Be excellent to each other and party on dudes", phrases)
        self.assertIn("Hut of brown, now sit down", phrases)


if __name__ == "__main__":
    unittest.main()
