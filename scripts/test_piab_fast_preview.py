"""Tests for Fast Preview helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from piab_fast_preview_lib import (
    FAST_PREVIEW_THRESHOLD_SEC,
    PREVIEW_PREFIX,
    fast_preview_eligible,
    preview_filename,
    preview_paths,
    should_use_tail_preview,
    slice_simplified_transcript_last_seconds,
)


class FastPreviewLibTests(unittest.TestCase):
    def test_preview_filename(self) -> None:
        self.assertEqual(preview_filename("Host Raw Video.mp4"), "Preview Host Raw Video.mp4")

    def test_preview_paths_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = preview_paths(Path(tmp))
            self.assertTrue(paths["raw"].endswith("Preview Files"))
            self.assertTrue(paths["input"].endswith("Preview Files\\Input") or paths["input"].endswith("Preview Files/Input"))

    def test_eligible_when_max_video_over_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            (raw / "Host Raw Video.mp4").write_bytes(b"x")
            (raw / "Guest Raw Video.mp4").write_bytes(b"x")
            (raw / "Wide Raw Video.mp4").write_bytes(b"x")
            durations = {
                str(raw / "Host Raw Video.mp4"): 700.0,
                str(raw / "Guest Raw Video.mp4"): 650.0,
                str(raw / "Wide Raw Video.mp4"): 800.0,
            }
            with patch("piab_fast_preview_lib.ffprobe_duration", side_effect=lambda p: durations[str(p)]):
                self.assertTrue(fast_preview_eligible(raw))
                self.assertFalse(fast_preview_eligible(raw) and 650 > FAST_PREVIEW_THRESHOLD_SEC is False)

    def test_not_eligible_at_ten_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            for name in ("Host Raw Video.mp4", "Guest Raw Video.mp4", "Wide Raw Video.mp4"):
                (raw / name).write_bytes(b"x")
            with patch("piab_fast_preview_lib.ffprobe_duration", return_value=600.0):
                self.assertFalse(fast_preview_eligible(raw))

    def test_tail_when_start_phrase_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            simplified = Path(tmp) / "simplified.json"
            simplified.write_text(json.dumps([]), encoding="utf-8")
            with patch("piab_fast_preview_lib.find_start_phrase_time_sec", return_value=None):
                use_tail, reason = should_use_tail_preview(
                    simplified_json=simplified,
                    prepped_duration_sec=280.0,
                    state={},
                )
            self.assertTrue(use_tail)
            self.assertEqual(reason, "start_phrase_missing")

    def test_tail_when_start_after_four_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            simplified = Path(tmp) / "simplified.json"
            simplified.write_text(json.dumps([]), encoding="utf-8")
            with patch("piab_fast_preview_lib.find_start_phrase_time_sec", return_value=255.0):
                use_tail, reason = should_use_tail_preview(
                    simplified_json=simplified,
                    prepped_duration_sec=290.0,
                    state={},
                )
            self.assertTrue(use_tail)
            self.assertEqual(reason, "start_phrase_after_4min")

    def test_load_simplified_dict_index_shape(self) -> None:
        from piab_fast_preview_lib import _load_simplified_rows

        with tempfile.TemporaryDirectory() as tmp:
            simplified = Path(tmp) / "simplified.json"
            simplified.write_text(
                json.dumps(
                    {
                        "0": {"start": 0.1, "end": 0.5, "text": "hello", "words": []},
                        "1": {"start": 1.0, "end": 1.5, "text": "world", "words": []},
                    }
                ),
                encoding="utf-8",
            )
            rows = _load_simplified_rows(simplified)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["text"], "hello")

    def test_slice_preserves_dict_index_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            simplified = Path(tmp) / "simplified.json"
            simplified.write_text(
                json.dumps(
                    {
                        "0": {"speaker_id": 0, "words": [{"start": 100.0, "text": "early"}]},
                        "1": {"speaker_id": 1, "words": [{"start": 250.0, "text": "late"}]},
                    }
                ),
                encoding="utf-8",
            )
            out = Path(tmp) / "tail.json"
            slice_simplified_transcript_last_seconds(
                simplified,
                output_path=out,
                window_sec=60.0,
                media_duration_sec=300.0,
            )
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertIsInstance(data, dict)
            self.assertEqual(data["0"]["words"][0]["text"], "late")

    def test_slice_last_sixty_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            simplified = Path(tmp) / "simplified.json"
            simplified.write_text(
                json.dumps(
                    [
                        {"speaker_id": 0, "words": [{"start": 100.0, "text": "early"}]},
                        {"speaker_id": 1, "words": [{"start": 250.0, "text": "late"}]},
                    ]
                ),
                encoding="utf-8",
            )
            out = Path(tmp) / "tail.json"
            slice_simplified_transcript_last_seconds(
                simplified,
                output_path=out,
                window_sec=60.0,
                media_duration_sec=300.0,
            )
            rows = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["words"][0]["text"], "late")


if __name__ == "__main__":
    unittest.main()
