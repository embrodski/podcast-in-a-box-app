"""Tests for disk-full error detection and user messaging."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from piab_disk_errors import (
    drive_letter_from_text,
    estimate_session_disk_gb,
    format_disk_full_user_message,
    is_disk_full_error,
    summarize_disk_full_if_applicable,
)


class DiskFullErrorTests(unittest.TestCase):
    def test_detects_ffmpeg_no_space_message(self) -> None:
        text = "Error muxing a packet\nerror code: -28 (No space left on device)"
        self.assertTrue(is_disk_full_error(text))

    def test_drive_from_output_path(self) -> None:
        text = r"ffmpeg ... E:\PodcastRoom\2026-08-05_1304\Input\Guest Video-prepped.partial.mp4"
        self.assertEqual(drive_letter_from_text(text), "E")

    def test_estimate_session_disk_gb(self) -> None:
        self.assertAlmostEqual(estimate_session_disk_gb(3000.0), 60.0)

    def test_format_message_includes_drive_and_estimate(self) -> None:
        text = r"E:\PodcastRoom\session\Input\Guest Video-prepped.partial.mp4\nNo space left on device"
        message = format_disk_full_user_message(
            text,
            source_duration_sec=3000.0,
        )
        self.assertIn("drive E:", message)
        self.assertIn("60 GB", message)
        self.assertIn("50-minute", message)
        self.assertIn("disk space", message.lower())

    def test_summarize_from_exception(self) -> None:
        exc = RuntimeError("Command failed: No space left on device")
        summary = summarize_disk_full_if_applicable(
            exc,
            working_folder=Path(r"E:\PodcastRoom\session"),
            source_duration_sec=3600.0,
        )
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertIn("drive E:", summary)
        self.assertIn("72 GB", summary)

    def test_duration_from_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "podcast-in-a-box.json").write_text(
                json.dumps({"source_duration_sec": 1800.0}),
                encoding="utf-8",
            )
            text = f"{folder}\\Input\\clip.mp4\nNo space left on device"
            message = format_disk_full_user_message(text, working_folder=folder)
            self.assertIn("30-minute", message)
            self.assertIn("36 GB", message)


if __name__ == "__main__":
    unittest.main()
