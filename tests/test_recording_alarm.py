"""Tests for B4 recording-stopped alarm helpers."""

from __future__ import annotations

import unittest

from app.gui.recording_alarm import (
    RECORDING_ALARM_URGENT_MS,
    RECORDING_STOPPED_TEXT,
    alarm_flash_stylesheet,
    alarm_label_style,
    raise_app_window,
)


class RecordingAlarmTests(unittest.TestCase):
    def test_stopped_copy_is_large_and_explicit(self) -> None:
        self.assertEqual(RECORDING_STOPPED_TEXT, "Recording has stopped")
        self.assertEqual(RECORDING_ALARM_URGENT_MS, 2000)
        self.assertIn("64px", alarm_label_style())
        self.assertIn("font-weight: 900", alarm_label_style())

    def test_flash_alternates_bright_and_dark_red(self) -> None:
        on = alarm_flash_stylesheet(on=True)
        off = alarm_flash_stylesheet(on=False)
        self.assertIn("#dc2626", on)
        self.assertIn("#111111", off)
        self.assertNotEqual(on, off)

    def test_raise_app_window_is_not_the_alarm_topmost_helper(self) -> None:
        self.assertTrue(callable(raise_app_window))
        self.assertNotEqual(raise_app_window.__name__, "bring_window_to_front")


if __name__ == "__main__":
    unittest.main()
