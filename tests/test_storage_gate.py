"""Tests for storage_gate thresholds."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.controller.paths import MIN_DISK_BYTES_FAIL, MIN_DISK_BYTES_WARN
from app.controller.storage_gate import (
    HALF_GIB,
    PREP_MULTIPLIER,
    assess_prep_storage,
    assess_recording_storage,
    assess_render_storage,
    clean_working_files_button_text,
    largest_prep_file_bytes,
    largest_raw_video_bytes,
)


def _usage(free: int):
    return lambda _path: (0, 0, free)


class StorageGateTests(unittest.TestCase):
    def test_recording_ok_warn_critical(self) -> None:
        ok = assess_recording_storage(
            Path("E:/PodcastRoom"),
            disk_usage=_usage(MIN_DISK_BYTES_WARN + 1),
        )
        self.assertEqual(ok.level, "ok")

        warn = assess_recording_storage(
            Path("E:/PodcastRoom"),
            disk_usage=_usage(MIN_DISK_BYTES_WARN - 1),
        )
        self.assertEqual(warn.level, "warn")

        critical = assess_recording_storage(
            Path("E:/PodcastRoom"),
            disk_usage=_usage(MIN_DISK_BYTES_FAIL - 1),
        )
        self.assertEqual(critical.level, "critical")

    def test_prep_uses_largest_raw_times_four(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            raw = folder / "Raw"
            raw.mkdir()
            small = raw / "Host Raw Video.mp4"
            large = raw / "Guest Raw Video.mp4"
            small.write_bytes(b"a" * 1000)
            large.write_bytes(b"b" * 4000)

            self.assertEqual(largest_raw_video_bytes(folder), 4000)
            needed = 4000 * PREP_MULTIPLIER

            insufficient = assess_prep_storage(
                folder,
                root=folder,
                disk_usage=_usage(needed - 1),
            )
            self.assertEqual(insufficient.level, "insufficient")
            self.assertEqual(insufficient.required_bytes, needed)

            ok = assess_prep_storage(
                folder,
                root=folder,
                disk_usage=_usage(needed),
            )
            self.assertEqual(ok.level, "ok")

    def test_render_uses_largest_prep_plus_half_gib(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            input_dir = folder / "Input"
            input_dir.mkdir()
            (input_dir / "Host Video-prepped.mp4").write_bytes(b"x" * 2000)
            (input_dir / "Guest Video-prepped.mp4").write_bytes(b"y" * 5000)

            self.assertEqual(largest_prep_file_bytes(folder), 5000)
            needed = 5000 + HALF_GIB

            insufficient = assess_render_storage(
                folder,
                root=folder,
                disk_usage=_usage(needed - 1),
            )
            self.assertEqual(insufficient.level, "insufficient")
            self.assertEqual(insufficient.required_bytes, needed)

            ok = assess_render_storage(
                folder,
                root=folder,
                disk_usage=_usage(needed),
            )
            self.assertEqual(ok.level, "ok")

    def test_prep_fallback_when_no_raw(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            assessment = assess_prep_storage(
                folder,
                root=folder,
                disk_usage=_usage(MIN_DISK_BYTES_WARN - 1),
            )
            self.assertEqual(assessment.level, "insufficient")
            self.assertEqual(assessment.required_bytes, MIN_DISK_BYTES_WARN)

    def test_clean_button_text_includes_free_gb(self) -> None:
        text = clean_working_files_button_text(120 * 1024**3)
        self.assertEqual(text, "Clean Old Working Files (120 GB free)")

    def test_clean_button_text_without_free_space(self) -> None:
        self.assertEqual(
            clean_working_files_button_text(None),
            "Clean Old Working Files",
        )


if __name__ == "__main__":
    unittest.main()
