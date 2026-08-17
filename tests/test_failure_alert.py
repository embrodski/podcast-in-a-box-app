"""Tests for failure-alert dedupe keys (no Qt dialogs)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.gui.failure_alert import (
    _mark_popup_shown,
    _popup_already_shown,
    failure_alert_key,
)


class FailureAlertKeyTests(unittest.TestCase):
    def test_uses_failed_at_when_present(self) -> None:
        folder = Path(r"E:\PodcastRoom\Test")
        first = failure_alert_key(
            folder,
            summary="Process exited with code 1",
            failed_at="2026-08-15T20:00:00+00:00",
        )
        second = failure_alert_key(
            folder,
            summary="Unknown segment: main",
            failed_at="2026-08-15T20:00:00+00:00",
        )
        self.assertEqual(first, second)

    def test_changes_on_new_failure(self) -> None:
        folder = Path(r"E:\PodcastRoom\Test")
        first = failure_alert_key(
            folder, summary="boom", failed_at="2026-08-15T20:00:00+00:00"
        )
        second = failure_alert_key(
            folder, summary="boom", failed_at="2026-08-15T21:00:00+00:00"
        )
        self.assertNotEqual(first, second)

    def test_popup_shown_persists_on_failure_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            marker = folder / "Temp" / "harness-FAILURE.json"
            marker.parent.mkdir()
            marker.write_text(
                json.dumps({"failed_at": "2026-08-15T20:00:00+00:00"}),
                encoding="utf-8",
            )
            self.assertFalse(_popup_already_shown(folder))
            _mark_popup_shown(folder)
            self.assertTrue(_popup_already_shown(folder))
            payload = json.loads(marker.read_text(encoding="utf-8"))
            self.assertTrue(payload.get("error_popup_shown_at"))


if __name__ == "__main__":
    unittest.main()
