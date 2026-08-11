#!/usr/bin/env python3
"""Tests for PIAB sync offset A/B resume routing."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_av_sync_lib import (
    ONE_MIN_FORCED_OFFSET,
    ONE_MIN_NO_OFFSET,
    SYNC_CHOICE_START_ALIGNED,
    write_failed_sync_confidence_flag,
)
from piab_lib import mark_piab_sync_ab_steps, mark_piab_sync_choice_completed
from piab_resume import build_prep_resume_plan, detect_prep_completion


def _minimal_state(root: Path) -> dict:
    return {
        "kind": "podcast_in_a_box",
        "paths": {
            "raw": str(root / "Raw"),
            "input": str(root / "Input"),
            "output": str(root / "Output"),
            "temp": str(root / "Temp"),
        },
        "main_combined_audio": str(root / "Raw" / "Host Combined Audio.wav"),
        "main_clean_audio": str(root / "Raw" / "Host Clean Audio.wav"),
        "main_prepped": {
            "prepped_videos": [
                str(root / "Input" / "Host Video-prepped.mp4"),
                str(root / "Input" / "Guest Video-prepped.mp4"),
                str(root / "Input" / "Wide Video-prepped.mp4"),
            ],
            "prepped_audio_wav": str(root / "Input" / "Main Prepped Audio.wav"),
        },
        "main_transcript_json": str(root / "Input" / "Main Prepped Audio Transcript.json"),
        "steps": {},
    }


class PiabSyncOffsetResumeTests(unittest.TestCase):
    def test_detect_ab_pair_as_one_min_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for sub in ("Raw", "Input", "Output", "Temp"):
                (root / sub).mkdir()
            state = _minimal_state(root)
            (root / "Raw" / "Host Combined Audio.wav").write_bytes(b"x")
            (root / "Raw" / "Host Clean Audio.wav").write_bytes(b"x")
            for rel in (
                "Input/Host Video-prepped.mp4",
                "Input/Guest Video-prepped.mp4",
                "Input/Wide Video-prepped.mp4",
                "Input/Main Prepped Audio.wav",
                "Input/Main Prepped Audio Transcript.json",
            ):
                (root / rel).write_bytes(b"x")
            write_failed_sync_confidence_flag(root / "Temp", [{"start_aligned_fallback": True}])
            (root / "Output" / ONE_MIN_NO_OFFSET).write_bytes(b"x")
            (root / "Output" / ONE_MIN_FORCED_OFFSET).write_bytes(b"x")
            completion = detect_prep_completion(state)
            self.assertTrue(completion["10_one_min_test"])

    def test_resume_plan_routes_to_10a_when_flag_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for sub in ("Raw", "Input", "Output", "Temp"):
                (root / sub).mkdir()
            state = _minimal_state(root)
            for rel in (
                "Raw/Host Combined Audio.wav",
                "Raw/Host Clean Audio.wav",
                "Input/Host Video-prepped.mp4",
                "Input/Guest Video-prepped.mp4",
                "Input/Wide Video-prepped.mp4",
                "Input/Main Prepped Audio.wav",
                "Input/Main Prepped Audio Transcript.json",
                f"Output/{ONE_MIN_NO_OFFSET}",
                f"Output/{ONE_MIN_FORCED_OFFSET}",
            ):
                (root / rel).write_bytes(b"x")
            write_failed_sync_confidence_flag(root / "Temp", [{"start_aligned_fallback": True}])
            mark_piab_sync_ab_steps(
                state,
                ab_result={
                    "one_min_no_offset": str(root / "Output" / ONE_MIN_NO_OFFSET),
                    "one_min_forced_offset": str(root / "Output" / ONE_MIN_FORCED_OFFSET),
                },
            )
            plan = build_prep_resume_plan(state, root, resume=True)
            self.assertTrue(plan.ready_for_approval)
            self.assertEqual(plan.resume_at, "10a_sync_offset_approval")

    def test_resume_plan_advances_to_11_after_choice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for sub in ("Raw", "Input", "Output", "Temp"):
                (root / sub).mkdir()
            state = _minimal_state(root)
            for rel in (
                "Raw/Host Combined Audio.wav",
                "Raw/Host Clean Audio.wav",
                "Input/Host Video-prepped.mp4",
                "Input/Guest Video-prepped.mp4",
                "Input/Wide Video-prepped.mp4",
                "Input/Main Prepped Audio.wav",
                "Input/Main Prepped Audio Transcript.json",
                f"Output/{ONE_MIN_NO_OFFSET}",
                f"Output/{ONE_MIN_FORCED_OFFSET}",
            ):
                (root / rel).write_bytes(b"x")
            write_failed_sync_confidence_flag(root / "Temp", [{"start_aligned_fallback": True}])
            state["sync_offset_choice"] = SYNC_CHOICE_START_ALIGNED
            state["sync_offset_choice_pending"] = False
            mark_piab_sync_choice_completed(state)
            plan = build_prep_resume_plan(state, root, resume=True)
            self.assertEqual(plan.resume_at, "11_one_min_approval")


if __name__ == "__main__":
    unittest.main()
