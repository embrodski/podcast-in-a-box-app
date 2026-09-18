"""Controller MultiCorder probe: API errors are not treated as idle."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.controller import PiabController
from app.controller.jobs import JobRunner
from app.controller.lock import AppLock
from app.controller.job_queue import JobQueueStore
from app.controller.paths import ensure_scripts_path


class ControllerMulticorderProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        ensure_scripts_path()
        from piab_vmix_api import MulticorderProbe

        self.Probe = MulticorderProbe
        self._tmp = tempfile.TemporaryDirectory()
        folder = Path(self._tmp.name)
        self.controller = PiabController(
            work_root=folder,
            lock=AppLock(folder / "lock.json"),
            jobs=JobRunner(),
            job_queue=JobQueueStore(folder / "queue.json"),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_multicorder_is_active_raises_when_unreachable(self) -> None:
        probe = self.Probe("unreachable", "Could not read vMix MultiCorder state: down")
        with patch("piab_vmix_api.probe_multicorder", return_value=probe):
            with self.assertRaisesRegex(RuntimeError, "down"):
                self.controller.multicorder_is_active()

    def test_multicorder_is_active_true_only_when_recording(self) -> None:
        with patch(
            "piab_vmix_api.probe_multicorder",
            return_value=self.Probe("recording"),
        ):
            self.assertTrue(self.controller.multicorder_is_active())
        with patch(
            "piab_vmix_api.probe_multicorder",
            return_value=self.Probe("idle"),
        ):
            self.assertFalse(self.controller.multicorder_is_active())

    def test_recording_lost_message(self) -> None:
        with patch(
            "piab_vmix_api.probe_multicorder",
            return_value=self.Probe("recording"),
        ):
            self.assertIsNone(self.controller.recording_lost_message())
        with patch(
            "piab_vmix_api.probe_multicorder",
            return_value=self.Probe("idle"),
        ):
            self.assertEqual(
                self.controller.recording_lost_message(),
                "vMix is not recording.",
            )


if __name__ == "__main__":
    unittest.main()
