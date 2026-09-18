"""Tests for CallableWorker start/stop helpers."""

from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication, QWidget

from app.gui.widgets.screen_base import ScreenWidget
from app.gui.widgets.worker import start_callable_worker, stop_callable_worker


class WorkerHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_start_skips_when_already_running(self) -> None:
        owner = QWidget()
        owner._worker = None
        started = start_callable_worker(owner, lambda: 1, on_ok=lambda _r: None)
        self.assertIsNotNone(started)
        skipped = start_callable_worker(owner, lambda: 2, on_ok=lambda _r: None)
        self.assertIsNone(skipped)
        stop_callable_worker(owner._worker, wait_ms=5_000)

    def test_session_folder_reads_context(self) -> None:
        from pathlib import Path

        from app.gui.session_context import SessionContext

        screen = ScreenWidget(controller=None)
        self.assertIsNone(screen.session_folder())
        ctx = SessionContext()
        ctx.session_folder = Path("E:/tmp/session")
        screen.bind_context(lambda: ctx)
        self.assertEqual(screen.session_folder(), ctx.session_folder)


if __name__ == "__main__":
    unittest.main()
