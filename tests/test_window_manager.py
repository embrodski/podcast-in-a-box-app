"""Placement helpers for multi-window cascade."""

from __future__ import annotations

import unittest

from app.gui.window_manager import cascaded_position


class CascadePositionTests(unittest.TestCase):
    def test_first_session_offsets_from_home(self) -> None:
        x, y = cascaded_position(
            anchor_x=100,
            anchor_y=80,
            step=40,
            index=1,
            width=720,
            height=520,
            avail_x=0,
            avail_y=0,
            avail_width=1920,
            avail_height=1080,
        )
        self.assertEqual((x, y), (140, 120))

    def test_second_session_steps_further(self) -> None:
        x, y = cascaded_position(
            anchor_x=100,
            anchor_y=80,
            step=40,
            index=2,
            width=720,
            height=520,
            avail_x=0,
            avail_y=0,
            avail_width=1920,
            avail_height=1080,
        )
        self.assertEqual((x, y), (180, 160))

    def test_stays_on_screen_near_bottom_right(self) -> None:
        x, y = cascaded_position(
            anchor_x=1400,
            anchor_y=700,
            step=40,
            index=8,
            width=720,
            height=520,
            avail_x=0,
            avail_y=0,
            avail_width=1920,
            avail_height=1080,
        )
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + 720, 1920)
        self.assertLessEqual(y + 520, 1080)
