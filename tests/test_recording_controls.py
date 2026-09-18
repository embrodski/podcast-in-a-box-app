"""B4 recording-controls copy."""

from __future__ import annotations

import html
import unittest

from app.controller.paths import ensure_scripts_path
from app.controller.recording import recording_controls_html, recording_instructions


class RecordingControlsHtmlTests(unittest.TestCase):
    def test_layout_and_colors(self) -> None:
        ensure_scripts_path()
        from podcast_phrase_gates import (
            end_phrases_from_gates,
            load_phrase_gates,
            pause_phrases_from_gates,
            start_trigger_phrase_from_gates,
            unpause_phrases_from_gates,
        )

        html_text = recording_controls_html()
        gates = load_phrase_gates(create_file_if_missing=False)
        start = start_trigger_phrase_from_gates(gates)
        pause_phrases = pause_phrases_from_gates(gates)
        unpause_phrases = unpause_phrases_from_gates(gates)
        end_phrases = end_phrases_from_gates(gates)

        self.assertEqual(html_text, recording_instructions())
        self.assertIn("text-align:center", html_text)
        self.assertIn("Program is running.", html_text)
        self.assertIn("Start Phrase is", html_text)
        self.assertIn(html.escape(start), html_text)
        self.assertIn("#4ade80", html_text)
        self.assertIn("Pause Phrase is", html_text)
        self.assertIn(html.escape(pause_phrases[0]), html_text)
        self.assertIn("Resume Phrase is", html_text)
        self.assertIn(html.escape(unpause_phrases[0]), html_text)
        self.assertIn("#60a5fa", html_text)
        self.assertIn("End Phrase is", html_text)
        self.assertIn(html.escape(end_phrases[0]), html_text)
        self.assertIn("When you are done, press Stop.", html_text)
        self.assertIn("#f87171", html_text)
        self.assertIn("THIS WILL STOP RECORDING!", html_text)
        self.assertNotIn("count down", html_text.lower())
        self.assertNotIn("press Continue", html_text)
        self.assertNotIn("Hut of brown", html_text)
        self.assertNotIn("Flag", html_text)
        self.assertNotIn("Timestamp", html_text)
        for extra in pause_phrases[1:]:
            self.assertNotIn(html.escape(extra), html_text)
        for extra in unpause_phrases[1:]:
            self.assertNotIn(html.escape(extra), html_text)
        for extra in end_phrases[1:]:
            self.assertNotIn(html.escape(extra), html_text)


if __name__ == "__main__":
    unittest.main()
