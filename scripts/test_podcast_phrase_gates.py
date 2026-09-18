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
    pause_phrases_from_gates,
    podcast_phrase_cli_args,
    prepped_audio_wav_from_state,
    save_phrase_gates,
    start_countdown_tokens_from_gates,
    start_trigger_phrase_from_gates,
)


class PhraseGatesTests(unittest.TestCase):
    def test_embedded_defaults_separate_trigger_and_countdown(self) -> None:
        self.assertIn("start_trigger_phrase", EMBEDDED_DEFAULTS)
        self.assertIn("start_countdown_tokens", EMBEDDED_DEFAULTS)
        self.assertIn("end_phrases", EMBEDDED_DEFAULTS)
        self.assertIn("pause_phrases", EMBEDDED_DEFAULTS)
        self.assertGreaterEqual(len(EMBEDDED_DEFAULTS["end_phrases"]), 1)
        self.assertGreaterEqual(len(EMBEDDED_DEFAULTS["pause_phrases"]), 2)
        self.assertNotIn("Hut of brown, now sit down", EMBEDDED_DEFAULTS["end_phrases"])
        self.assertIn("Computer Freeze Program.", EMBEDDED_DEFAULTS["pause_phrases"][0])
        self.assertIn("Computer Pause Program", EMBEDDED_DEFAULTS["pause_phrases"][1])
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
            self.assertEqual(args.count("--pause-phrase"), 2)
            self.assertIn("--start-phrase-countdown", args)
            self.assertIn("--start-phrase-countdown-suffix", args)
            self.assertIn("--pause-phrase", args)

    def test_end_phrases_from_gates(self) -> None:
        gates = load_phrase_gates(create_file_if_missing=False)
        phrases = end_phrases_from_gates(gates)
        self.assertGreaterEqual(len(phrases), 2)
        self.assertEqual(phrases[0], "Mischief Managed")
        self.assertIn("Be excellent to each other and party on dudes", phrases)
        self.assertNotIn("Hut of brown, now sit down", phrases)

    def test_cli_args_include_pause_audio_when_wav_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "Host Clean Audio-prepped.wav"
            wav.write_bytes(b"RIFF")
            args = podcast_phrase_cli_args(
                {"main_prepped": {"prepped_audio_wav": str(wav)}}
            )
            self.assertIn("--pause-audio-file", args)
            self.assertEqual(args[args.index("--pause-audio-file") + 1], str(wav.resolve()))
            self.assertEqual(prepped_audio_wav_from_state({}), None)

    def test_pause_phrases_from_gates(self) -> None:
        gates = load_phrase_gates(create_file_if_missing=False)
        phrases = pause_phrases_from_gates(gates)
        self.assertEqual(phrases[0], "Computer Freeze Program.")
        self.assertIn("Computer Pause Program", phrases)

    def test_legacy_pause_phrase_key_still_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "podcast-phrase-gates.json").write_text(
                json.dumps({"pause_phrase": "Legacy Pause Phrase"}),
                encoding="utf-8",
            )
            gates = load_phrase_gates(repo_root=root, create_file_if_missing=False)
            phrases = pause_phrases_from_gates(gates)
            self.assertEqual(phrases[0], "Legacy Pause Phrase")
            self.assertIn("Computer Freeze Program.", phrases)


if __name__ == "__main__":
    unittest.main()
