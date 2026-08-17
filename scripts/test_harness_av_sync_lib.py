"""Tests for harness_av_sync_lib sync confidence detection."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_av_sync_lib import sync_confidence_failed, write_canonical_main_segments


class SyncConfidenceFailedTests(unittest.TestCase):
    def test_false_when_offsets_applied(self) -> None:
        reports = [
            {
                "start_aligned": False,
                "start_aligned_fallback": False,
                "correlation_peak_strength": 0.9,
            }
        ]
        self.assertFalse(sync_confidence_failed(reports))

    def test_true_when_below_threshold_fallback(self) -> None:
        reports = [
            {
                "start_aligned": True,
                "start_aligned_fallback": True,
                "start_aligned_reason": "correlation peak 0.0786 below threshold 0.3500",
            }
        ]
        self.assertTrue(sync_confidence_failed(reports))

    def test_false_when_assume_start_aligned(self) -> None:
        reports = [
            {
                "start_aligned": True,
                "assume_start_aligned": True,
                "start_aligned_fallback": True,
            }
        ]
        self.assertFalse(sync_confidence_failed(reports))


class WriteCanonicalMainSegmentsTests(unittest.TestCase):
    def test_writes_full_temp_segments_not_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            temp = root / "Temp"
            preview_temp = root / "Preview Files" / "Temp"
            temp.mkdir()
            preview_temp.mkdir(parents=True)
            simplified = temp / "interview_transcript_simplified.json"
            simplified.write_text("{}", encoding="utf-8")
            (preview_temp / "segments.json").write_text(
                json.dumps(
                    {
                        "main": {
                            "audio_file": str(root / "Preview Files" / "preview.wav"),
                            "audio_offset": 240.0,
                        }
                    }
                ),
                encoding="utf-8",
            )
            host = root / "Input" / "Host Video-prepped.mp4"
            guest = root / "Input" / "Guest Video-prepped.mp4"
            wide = root / "Input" / "Wide Video-prepped.mp4"
            wav = root / "Input" / "Host Clean Audio-prepped.wav"
            state = {
                "paths": {"temp": str(temp)},
                "main_prepped": {
                    "prepped_videos": [str(host), str(guest), str(wide)],
                    "prepped_audio_wav": str(wav),
                },
                "segments_file": str(preview_temp / "segments.json"),
            }
            path = write_canonical_main_segments(state, simplified_json=simplified)
            self.assertEqual(path, temp / "segments.json")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["main"]["audio_offset"], 0)
            self.assertEqual(data["main"]["audio_file"], str(wav))
            self.assertEqual(
                data["main"]["video_files"]["speaker_0"]["file"],
                str(host),
            )
            self.assertEqual(state["segments_file"], str(path.resolve()))


if __name__ == "__main__":
    unittest.main()
