"""B4 recording-controls copy."""

from __future__ import annotations

import unittest

from app.controller.recording import recording_controls_html, recording_instructions


class RecordingControlsHtmlTests(unittest.TestCase):
    def test_layout_and_colors(self) -> None:
        html = recording_controls_html()
        self.assertEqual(html, recording_instructions())
        self.assertIn("text-align:center", html)
        self.assertIn("Program is running.", html)
        self.assertIn("Start Phrase is", html)
        self.assertIn("I solemnly swear I&#x27;m up to no good", html)
        self.assertIn("#4ade80", html)
        self.assertIn("Pause Phrase is", html)
        self.assertIn("Computer, Pause Program", html)
        self.assertIn("Resume Phrase is", html)
        self.assertIn("Computer, Resume Program", html)
        self.assertIn("#60a5fa", html)
        self.assertIn("End Phrase is", html)
        self.assertIn("Be excellent to each other and party on dudes", html)
        self.assertIn("When you are done, press Continue.", html)
        self.assertIn("#f87171", html)
        self.assertIn("THIS WILL STOP RECORDING!", html)
        self.assertNotIn("count down", html.lower())
        self.assertNotIn("Hut of brown", html)
