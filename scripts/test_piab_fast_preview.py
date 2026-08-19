"""Tests for Fast Preview helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from piab_fast_preview_lib import (
    FAST_PREVIEW_CLIP_SEC,
    FAST_PREVIEW_ESTIMATE_DIVISOR,
    PREVIEW_PREFIX,
    estimate_fast_preview_prep,
    fast_preview_eligible,
    is_short_source_duration,
    preview_filename,
    preview_paths,
    should_use_tail_preview,
    slice_simplified_transcript_last_seconds,
)
from piab_lib import estimate_prep_through_one_min


class FastPreviewLibTests(unittest.TestCase):
    def test_preview_filename(self) -> None:
        self.assertEqual(preview_filename("Host Raw Video.mp4"), "Preview Host Raw Video.mp4")

    def test_preview_paths_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = preview_paths(Path(tmp))
            self.assertTrue(paths["raw"].endswith("Preview Files"))
            self.assertTrue(paths["input"].endswith("Preview Files\\Input") or paths["input"].endswith("Preview Files/Input"))

    def test_eligible_for_any_labeled_duration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            for name in ("Host Raw Video.mp4", "Guest Raw Video.mp4", "Wide Raw Video.mp4"):
                (raw / name).write_bytes(b"x")
            with patch("piab_fast_preview_lib.ffprobe_duration", return_value=120.0):
                self.assertTrue(fast_preview_eligible(raw))
            with patch("piab_fast_preview_lib.ffprobe_duration", return_value=800.0):
                self.assertTrue(fast_preview_eligible(raw))

    def test_short_source_threshold(self) -> None:
        self.assertTrue(is_short_source_duration(299.9))
        self.assertFalse(is_short_source_duration(300.0))
        self.assertFalse(is_short_source_duration(None))

    def test_tail_when_source_shorter_than_five_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            simplified = Path(tmp) / "simplified.json"
            simplified.write_text(json.dumps([]), encoding="utf-8")
            with patch("piab_fast_preview_lib.find_start_phrase_time_sec", return_value=10.0):
                use_tail, reason = should_use_tail_preview(
                    simplified_json=simplified,
                    prepped_duration_sec=240.0,
                    state={"max_video_duration_sec": 240.0},
                )
            self.assertTrue(use_tail)
            self.assertEqual(reason, "source_shorter_than_5min")

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
            _, origin = slice_simplified_transcript_last_seconds(
                simplified,
                output_path=out,
                window_sec=60.0,
                media_duration_sec=300.0,
            )
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertIsInstance(data, dict)
            self.assertEqual(origin, 240.0)
            self.assertEqual(data["0"]["words"][0]["text"], "late")
            self.assertAlmostEqual(data["0"]["words"][0]["start"], 10.0)

    def test_promote_short_source_copies_stripped_names(self) -> None:
        from piab_fast_preview_lib import promote_short_source_preview_to_canonical
        from piab_lib import save_piab_state

        with tempfile.TemporaryDirectory() as tmp:
            working = Path(tmp)
            preview_input = working / "Preview Files" / "Input"
            preview_input.mkdir(parents=True)
            src = preview_input / "Preview Host Video-prepped.mp4"
            src.write_bytes(b"video")
            transcript = working / "Preview Files" / "Temp" / "preview-transcript.json"
            transcript.parent.mkdir(parents=True, exist_ok=True)
            transcript.write_text("{}", encoding="utf-8")
            state = {
                "kind": "podcast_in_a_box",
                "name": "test",
                "paths": {
                    "raw": str(working / "Raw"),
                    "input": str(working / "Input"),
                    "temp": str(working / "Temp"),
                    "output": str(working / "Output"),
                },
                "max_video_duration_sec": 120.0,
                "fast_preview": {
                    "max_video_duration_sec": 120.0,
                    "sandbox_artifacts": {
                        "main_prepped": {
                            "prepped_videos": [str(src)],
                            "prepped_audio_wav": str(src),
                        },
                        "main_transcript_json": str(transcript),
                    },
                },
                "fast_preview_approval": {
                    "approved_at": "2026-01-01T00:00:00+00:00",
                    "sync_offset_choice": "start_aligned",
                    "swap_speaker_ids": False,
                },
                "steps": {},
            }
            (working / "Raw").mkdir()
            (working / "Input").mkdir()
            (working / "Temp").mkdir()
            save_piab_state(working, state)
            with patch("harness_av_sync_lib.write_canonical_main_segments"):
                promote_short_source_preview_to_canonical(working, allow_overwrite=True)
            dest = working / "Input" / "Host Video-prepped.mp4"
            self.assertTrue(dest.is_file())
            self.assertEqual(dest.read_bytes(), b"video")

    def test_promote_short_source_copies_elevenlabs_sidecars(self) -> None:
        from piab_fast_preview_lib import promote_short_source_preview_to_canonical
        from piab_lib import save_piab_state

        with tempfile.TemporaryDirectory() as tmp:
            working = Path(tmp)
            preview_input = working / "Preview Files" / "Input"
            preview_input.mkdir(parents=True)
            wav = preview_input / "Preview Clean Audio-prepped.wav"
            wav.write_bytes(b"wav")
            json_path = preview_input / "Preview Clean Audio-prepped Transcript.json"
            json_path.write_text("{}", encoding="utf-8")
            text_path = preview_input / "Preview Clean Audio-prepped Text.txt"
            text_path.write_text("00:00:00,000 --> 00:00:01,000 [Speaker 0]\nHello.\n", encoding="utf-8")
            id_path = preview_input / "Preview Clean Audio-prepped Transcription ID.txt"
            id_path.write_text("tr_abc", encoding="utf-8")
            video = preview_input / "Preview Host Video-prepped.mp4"
            video.write_bytes(b"video")
            state = {
                "kind": "podcast_in_a_box",
                "name": "test",
                "paths": {
                    "raw": str(working / "Raw"),
                    "input": str(working / "Input"),
                    "temp": str(working / "Temp"),
                    "output": str(working / "Output"),
                },
                "max_video_duration_sec": 120.0,
                "fast_preview": {
                    "max_video_duration_sec": 120.0,
                    "sandbox_artifacts": {
                        "main_prepped": {
                            "prepped_videos": [str(video)],
                            "prepped_audio_wav": str(wav),
                        },
                        "main_transcript_json": str(json_path),
                    },
                },
                "fast_preview_approval": {
                    "approved_at": "2026-01-01T00:00:00+00:00",
                    "sync_offset_choice": "start_aligned",
                    "swap_speaker_ids": False,
                },
                "steps": {},
            }
            (working / "Raw").mkdir()
            (working / "Input").mkdir()
            (working / "Temp").mkdir()
            save_piab_state(working, state)
            with patch("harness_av_sync_lib.write_canonical_main_segments"):
                result = promote_short_source_preview_to_canonical(
                    working, allow_overwrite=True
                )
            input_dir = working / "Input"
            self.assertTrue((input_dir / "Host Video-prepped.mp4").is_file())
            self.assertTrue((input_dir / "Clean Audio-prepped.wav").is_file())
            self.assertTrue((input_dir / "Clean Audio-prepped Transcript.json").is_file())
            dest_text = input_dir / "Clean Audio-prepped Text.txt"
            dest_id = input_dir / "Clean Audio-prepped Transcription ID.txt"
            self.assertTrue(dest_text.is_file())
            self.assertTrue(dest_id.is_file())
            self.assertEqual(dest_text.read_text(encoding="utf-8"), text_path.read_text(encoding="utf-8"))
            self.assertEqual(dest_id.read_text(encoding="utf-8"), "tr_abc")
            self.assertEqual(
                Path(str(result["main_transcript_json"])).name,
                "Clean Audio-prepped Transcript.json",
            )

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
            _, origin = slice_simplified_transcript_last_seconds(
                simplified,
                output_path=out,
                window_sec=60.0,
                media_duration_sec=300.0,
            )
            rows = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(origin, 240.0)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["words"][0]["text"], "late")
            self.assertAlmostEqual(rows[0]["words"][0]["start"], 10.0)
            self.assertAlmostEqual(rows[0]["start"], 10.0)

    def test_slice_clamps_overlapping_row_and_rebases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            simplified = Path(tmp) / "simplified.json"
            simplified.write_text(
                json.dumps(
                    [
                        {
                            "speaker_id": 1,
                            "start": 220.0,
                            "end": 255.0,
                            "words": [
                                {"start": 220.0, "end": 221.0, "text": "early"},
                                {"start": 248.0, "end": 255.0, "text": "late"},
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            out = Path(tmp) / "tail.json"
            _, origin = slice_simplified_transcript_last_seconds(
                simplified,
                output_path=out,
                window_sec=60.0,
                media_duration_sec=300.0,
            )
            rows = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(origin, 240.0)
            self.assertEqual(len(rows), 1)
            self.assertEqual([w["text"] for w in rows[0]["words"]], ["late"])
            self.assertAlmostEqual(rows[0]["start"], 8.0)
            self.assertAlmostEqual(rows[0]["end"], 15.0)

    def test_fast_preview_estimate_is_one_third_of_shared_formula(self) -> None:
        raw = estimate_prep_through_one_min(FAST_PREVIEW_CLIP_SEC)
        scaled = estimate_fast_preview_prep()
        self.assertEqual(
            scaled["center_sec"],
            int(round(raw["center_sec"] / FAST_PREVIEW_ESTIMATE_DIVISOR)),
        )
        self.assertEqual(
            scaled["breakdown"]["one_min_render_sec"],
            int(round(raw["breakdown"]["one_min_render_sec"] / FAST_PREVIEW_ESTIMATE_DIVISOR)),
        )


if __name__ == "__main__":
    unittest.main()
