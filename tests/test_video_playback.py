"""Tests for review-player file release."""

from __future__ import annotations

import unittest

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from app.gui.widgets.screen_base import ScreenWidget
from app.gui.widgets.video_playback import close_media_player


class CloseMediaPlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_close_clears_source(self) -> None:
        player = QMediaPlayer()
        close_media_player(player)
        self.assertEqual(player.source(), QUrl())
        self.assertEqual(
            player.playbackState(),
            QMediaPlayer.PlaybackState.StoppedState,
        )

    def test_screen_base_on_leave_is_safe_default(self) -> None:
        screen = ScreenWidget(controller=None)
        screen.on_leave()


if __name__ == "__main__":
    unittest.main()
