"""Tests for flag phrase detection and DSL timeline reporting."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from podcast_flag_phrases import (
    find_flag_hits_in_row,
    format_combined_flag_report,
    format_flag_timestamp_report,
    format_pause_flag_timestamp_report,
    phrase_token_variants,
    report_flag_timestamps_after_render,
    scan_dsl_flag_markers,
    scan_dsl_flag_output_times,
)
from podcast_phrase_gates import EMBEDDED_DEFAULTS, flag_phrases_from_gates, load_phrase_gates


def _words(*items: tuple[str, float, float]) -> list[dict]:
    return [{"text": t, "start": s, "end": e} for t, s, e in items]


class FlagPhraseMatchTests(unittest.TestCase):
    def test_phrase_token_variants_expands_timestamp(self) -> None:
        variants = phrase_token_variants("Computer Drop Timestamp")
        self.assertIn(["computer", "drop", "timestamp"], variants)
        self.assertIn(["computer", "drop", "time", "stamp"], variants)

    def test_matches_timestamp_as_two_words(self) -> None:
        row = {
            "start": 10.0,
            "end": 12.0,
            "text": "Okay, computer drop time stamp now.",
            "words": _words(
                ("Okay,", 10.0, 10.2),
                ("computer", 10.3, 10.6),
                ("drop", 10.7, 10.9),
                ("time", 11.0, 11.2),
                ("stamp", 11.25, 11.5),
                ("now.", 11.6, 11.8),
            ),
        }
        hits = find_flag_hits_in_row(
            row,
            row_id="5",
            segment_num="main",
            phrases=["Computer Drop Timestamp"],
        )
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(hits[0].source_start_sec, 10.3)

    def test_matches_within_longer_sentence(self) -> None:
        row = {
            "start": 0.0,
            "end": 5.0,
            "text": "So anyway, computer drop flag, moving on.",
            "words": _words(
                ("So", 0.0, 0.2),
                ("anyway,", 0.3, 0.6),
                ("computer", 1.0, 1.4),
                ("drop", 1.5, 1.7),
                ("flag,", 1.8, 2.0),
                ("moving", 2.5, 2.8),
                ("on.", 2.9, 3.1),
            ),
        }
        hits = find_flag_hits_in_row(
            row,
            row_id="1",
            segment_num="main",
            phrases=["Computer Drop Flag"],
        )
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(hits[0].source_start_sec, 1.0)

    def test_format_none(self) -> None:
        self.assertEqual(
            format_flag_timestamp_report([]),
            "Flags Dropped At These Timestamps:\n-none-",
        )

    def test_format_times(self) -> None:
        text = format_flag_timestamp_report([383.0, 1279.4])
        self.assertIn("00:06:23", text)
        self.assertIn("00:21:19", text)

    def test_format_pause_flag_section(self) -> None:
        text = format_pause_flag_timestamp_report([120.0])
        self.assertIn("Pause Flags At These Timestamps:", text)
        self.assertIn("00:02:00", text)

    def test_combined_report(self) -> None:
        text = format_combined_flag_report([10.0], [120.0])
        self.assertIn("Flags Dropped At These Timestamps:", text)
        self.assertIn("Pause Flags At These Timestamps:", text)
        self.assertIn("00:00:10", text)
        self.assertIn("00:02:00", text)


class FlagDslScanTests(unittest.TestCase):
    def test_scan_dsl_maps_flag_to_output_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript = {
                "0": {
                    "start": 0.0,
                    "end": 5.0,
                    "text": "Intro line.",
                    "words": _words(("Intro", 0.0, 0.4), ("line.", 0.5, 1.0)),
                },
                "1": {
                    "start": 10.0,
                    "end": 15.0,
                    "text": "Then computer drop flag here.",
                    "words": _words(
                        ("Then", 10.0, 10.3),
                        ("computer", 10.5, 10.9),
                        ("drop", 11.0, 11.2),
                        ("flag", 11.3, 11.5),
                        ("here.", 11.6, 12.0),
                    ),
                },
            }
            transcript_path = root / "t.json"
            transcript_path.write_text(json.dumps(transcript), encoding="utf-8")
            segments = {
                "main": {
                    "transcript_file": str(transcript_path),
                    "audio_file": "dummy.wav",
                    "video_files": {
                        "speaker_0": {"file": "host.mp4", "offset": 0.0},
                    },
                }
            }
            (root / "segments.json").write_text(json.dumps(segments), encoding="utf-8")
            dsl = root / "interview.dsl"
            dsl.write_text(
                "\n".join(
                    [
                        "!cut 0 0",
                        "!camera speaker_0",
                        "$segmentmain/0 // Intro",
                        "$segmentmain/1 // Flag row",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            times = scan_dsl_flag_output_times(dsl, root)
            # Row 0 is 5s; flag starts 0.5s into row 1 => 5.5s output.
            self.assertEqual(len(times), 1)
            self.assertAlmostEqual(times[0], 5.5, places=2)

    def test_scan_dsl_pause_flag_at_seam(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript = {
                "0": {
                    "start": 0.0,
                    "end": 5.0,
                    "text": "Before pause.",
                    "words": _words(("Before", 0.0, 0.5), ("pause.", 0.6, 1.0)),
                },
                "1": {
                    "start": 10.0,
                    "end": 15.0,
                    "text": "After pause.",
                    "words": _words(("After", 10.0, 10.3), ("pause.", 10.4, 10.8)),
                },
            }
            transcript_path = root / "t.json"
            transcript_path.write_text(json.dumps(transcript), encoding="utf-8")
            segments = {
                "main": {
                    "transcript_file": str(transcript_path),
                    "audio_file": "dummy.wav",
                    "video_files": {
                        "speaker_0": {"file": "host.mp4", "offset": 0.0},
                    },
                }
            }
            (root / "segments.json").write_text(json.dumps(segments), encoding="utf-8")
            dsl = root / "interview.dsl"
            dsl.write_text(
                "\n".join(
                    [
                        "!cut 0 0",
                        "!camera speaker_0",
                        "$segmentmain/0 // Before",
                        "!pause-flag",
                        "$segmentmain/1 // After pause seam",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            spoken, pause = scan_dsl_flag_markers(dsl, root)
            self.assertEqual(spoken, [])
            self.assertEqual(len(pause), 1)
            self.assertAlmostEqual(pause[0], 5.0, places=2)

    def test_report_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript = {
                "0": {
                    "start": 0.0,
                    "end": 2.0,
                    "text": "computer drop flag",
                    "words": _words(
                        ("computer", 0.0, 0.4),
                        ("drop", 0.5, 0.7),
                        ("flag", 0.8, 1.0),
                    ),
                }
            }
            transcript_path = root / "t.json"
            transcript_path.write_text(json.dumps(transcript), encoding="utf-8")
            segments = {
                "main": {
                    "transcript_file": str(transcript_path),
                    "audio_file": "dummy.wav",
                    "video_files": {
                        "speaker_0": {"file": "host.mp4", "offset": 0.0},
                    },
                }
            }
            (root / "segments.json").write_text(json.dumps(segments), encoding="utf-8")
            dsl = root / "interview.dsl"
            dsl.write_text(
                "!camera speaker_0\n$segmentmain/0\n",
                encoding="utf-8",
            )
            summary = report_flag_timestamps_after_render(
                dsl,
                root,
                write_report_file=True,
            )
            self.assertEqual(summary["flag_timestamps_hhmmss"], ["00:00:00"])
            self.assertEqual(summary["pause_flag_timestamps_hhmmss"], [])
            report_file = root / "interview-flag-timestamps.txt"
            self.assertTrue(report_file.is_file())
            self.assertIn("Pause Flags At These Timestamps:", report_file.read_text(encoding="utf-8"))
            self.assertIn("00:00:00", report_file.read_text(encoding="utf-8"))


class FlagPhraseGatesConfigTests(unittest.TestCase):
    def test_embedded_defaults_include_flag_phrases(self) -> None:
        self.assertIn("flag_phrases", EMBEDDED_DEFAULTS)
        phrases = flag_phrases_from_gates(EMBEDDED_DEFAULTS)
        self.assertIn("Computer Drop Flag", phrases)
        self.assertGreaterEqual(len(phrases), 4)

    def test_repo_phrase_gates_file_has_flags(self) -> None:
        gates = load_phrase_gates(create_file_if_missing=False)
        phrases = flag_phrases_from_gates(gates)
        self.assertIn("Computer Timestamp", phrases)


if __name__ == "__main__":
    unittest.main()
