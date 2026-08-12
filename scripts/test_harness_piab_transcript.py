"""Tests for PIAB human transcript generation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness_piab_transcript import (
    FULL_INTERVIEW_TRANSCRIPT_TXT,
    build_human_transcript_text,
    write_human_transcript_to_output,
)


SAMPLE_SRT = """\
00:00:01,000 --> 00:00:03,000 [Speaker 0]
Hello there.

00:00:03,500 --> 00:00:06,000 [Speaker 1]
Hi, nice to meet you.
"""


class PiabTranscriptTests(unittest.TestCase):
    def _state(self, tmp: Path) -> dict:
        input_dir = tmp / "Input"
        input_dir.mkdir()
        detail = input_dir / "Host Clean Audio-prepped Transcript.json"
        detail.write_text("{}", encoding="utf-8")
        text = input_dir / "Host Clean Audio-prepped Text.txt"
        text.write_text(SAMPLE_SRT, encoding="utf-8")
        return {
            "name": "Alex",
            "main_transcript_json": str(detail),
            "swap_speaker_ids": False,
        }

    def test_build_human_transcript_uses_host_and_guest_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            text = build_human_transcript_text(self._state(Path(tmp)))
        self.assertIn("Host:", text)
        self.assertIn("Alex:", text)
        self.assertIn("Hello there.", text)

    def test_swap_speaker_ids_flips_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = self._state(Path(tmp))
            state["swap_speaker_ids"] = True
            text = build_human_transcript_text(state)
        self.assertIn("Alex:", text)
        self.assertTrue(text.index("Alex:") < text.index("Hello there."))
        self.assertIn("Host:", text)

    def test_write_human_transcript_to_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "Output"
            state = self._state(root)
            path = write_human_transcript_to_output(state, output)
            self.assertEqual(path.name, FULL_INTERVIEW_TRANSCRIPT_TXT)
            self.assertTrue(path.is_file())
            body = path.read_text(encoding="utf-8")
            self.assertIn("Host:", body)


if __name__ == "__main__":
    unittest.main()
