"""Tests for vMix MultiCorder probe."""

from __future__ import annotations

import unittest

from piab_vmix_api import probe_multicorder, recording_lost_message


class PiabVmixApiProbeTests(unittest.TestCase):
    def test_probe_recording_and_idle(self) -> None:
        recording = probe_multicorder(
            fetch_xml=lambda **_kwargs: "<vmix><multiCorder>True</multiCorder></vmix>"
        )
        idle = probe_multicorder(
            fetch_xml=lambda **_kwargs: "<vmix><multiCorder>False</multiCorder></vmix>"
        )
        self.assertEqual(recording.status, "recording")
        self.assertTrue(recording.is_recording)
        self.assertEqual(idle.status, "idle")
        self.assertFalse(idle.is_recording)

    def test_probe_unreachable_on_api_error(self) -> None:
        def boom(**_kwargs):
            raise TimeoutError("timed out")

        probe = probe_multicorder(fetch_xml=boom)
        self.assertEqual(probe.status, "unreachable")
        self.assertIn("timed out", probe.message)
        self.assertFalse(probe.is_recording)

    def test_probe_unreachable_on_bad_xml(self) -> None:
        probe = probe_multicorder(fetch_xml=lambda **_kwargs: "not-xml")
        self.assertEqual(probe.status, "unreachable")
        self.assertIn("Could not read vMix MultiCorder state", probe.message)

    def test_recording_lost_message(self) -> None:
        recording = probe_multicorder(
            fetch_xml=lambda **_kwargs: "<vmix><multiCorder>True</multiCorder></vmix>"
        )
        idle = probe_multicorder(
            fetch_xml=lambda **_kwargs: "<vmix><multiCorder>False</multiCorder></vmix>"
        )
        down = probe_multicorder(
            fetch_xml=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("down"))
        )
        self.assertIsNone(recording_lost_message(recording))
        self.assertEqual(recording_lost_message(idle), "vMix is not recording.")
        self.assertIn("down", recording_lost_message(down) or "")


if __name__ == "__main__":
    unittest.main()
