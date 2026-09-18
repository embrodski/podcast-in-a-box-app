"""Closing a window stops that window's timers and idle media, without destroying it."""

from __future__ import annotations

import unittest

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

from app.gui.views.labeling_screens import LabelMicrophonesScreen
from app.gui.views.processing_screen import ProcessingScreen
from app.gui.views.recording_screens import RecordingScreen
from app.gui.views.full_render_screen import FullRenderScreen


class WindowIdleResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_processing_screen_stops_poll(self) -> None:
        screen = ProcessingScreen(controller=None)
        screen._poll.start()
        self.assertTrue(screen._poll.isActive())
        screen.release_idle_resources()
        self.assertFalse(screen._poll.isActive())

    def test_full_render_screen_stops_poll(self) -> None:
        screen = FullRenderScreen(controller=None)
        screen._poll.start()
        self.assertTrue(screen._poll.isActive())
        screen.release_idle_resources()
        self.assertFalse(screen._poll.isActive())

    def test_recording_screen_stops_heartbeat_not_multicorder(self) -> None:
        screen = RecordingScreen(controller=None)
        screen._heartbeat.start()
        self.assertTrue(screen._heartbeat.isActive())
        screen.release_idle_resources()
        self.assertFalse(screen._heartbeat.isActive())

    def test_mic_label_player_drops_source(self) -> None:
        screen = LabelMicrophonesScreen(controller=None)
        screen._player.setSource(QUrl.fromLocalFile("C:/unused-preview.wav"))
        self.assertNotEqual(screen._player.source(), QUrl())
        screen.release_idle_resources()
        self.assertEqual(screen._player.source(), QUrl())


if __name__ == "__main__":
    unittest.main()
