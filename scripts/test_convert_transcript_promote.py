#!/usr/bin/env python3
"""Tests for extra-speaker promotion in convert_transcript_json."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from convert_transcript_json import (
    extra_speaker_promotion_swaps,
    promote_extra_speakers_in_output,
    speaker_speech_seconds,
    swap_speaker_ids_in_output,
)


def _row(speaker_id: int, duration: float, start: float = 0.0) -> dict:
    return {
        "start": start,
        "end": start + duration,
        "text": f"s{speaker_id}",
        "speaker_id": speaker_id,
    }


def _output(*rows: dict) -> dict[str, dict]:
    return {str(i): dict(row) for i, row in enumerate(rows)}


class ExtraSpeakerPromotionTests(unittest.TestCase):
    def test_no_extras_means_no_swaps(self) -> None:
        swaps = extra_speaker_promotion_swaps({0: 40.0, 1: 20.0})
        self.assertEqual(swaps, [])

    def test_speaker_2_replaces_weak_guest_slot(self) -> None:
        # Eneasz/Paul shape: Host is 0, leftover room voice is 1, real guest is 2.
        swaps = extra_speaker_promotion_swaps({0: 49.5, 1: 3.3, 2: 15.2})
        self.assertEqual(swaps, [(2, 1)])

    def test_speaker_2_replaces_weak_host_slot(self) -> None:
        swaps = extra_speaker_promotion_swaps({0: 3.0, 1: 40.0, 2: 20.0})
        self.assertEqual(swaps, [(2, 0)])

    def test_slightly_more_speech_does_not_promote(self) -> None:
        swaps = extra_speaker_promotion_swaps({0: 40.0, 1: 12.0, 2: 15.0})
        self.assertEqual(swaps, [])

    def test_tiny_extra_cluster_is_ignored(self) -> None:
        swaps = extra_speaker_promotion_swaps({0: 40.0, 1: 0.0, 2: 1.5})
        self.assertEqual(swaps, [])

    def test_empty_guest_slot_promotes_substantial_speaker_2(self) -> None:
        swaps = extra_speaker_promotion_swaps({0: 40.0, 2: 10.0})
        self.assertEqual(swaps, [(2, 1)])

    def test_speaker_3_can_take_guest_slot(self) -> None:
        swaps = extra_speaker_promotion_swaps({0: 40.0, 1: 3.0, 3: 18.0})
        self.assertEqual(swaps, [(3, 1)])

    def test_two_extras_fill_both_weak_slots(self) -> None:
        swaps = extra_speaker_promotion_swaps({0: 3.0, 1: 4.0, 2: 50.0, 3: 40.0})
        self.assertEqual(swaps, [(2, 0), (3, 1)])

    def test_promote_rewrites_row_ids(self) -> None:
        output = _output(
            _row(0, 20.0, 0.0),
            _row(1, 3.0, 20.0),
            _row(2, 12.0, 23.0),
        )
        swaps = promote_extra_speakers_in_output(output)
        self.assertEqual(swaps, [(2, 1)])
        self.assertEqual(output["0"]["speaker_id"], 0)
        self.assertEqual(output["1"]["speaker_id"], 2)
        self.assertEqual(output["2"]["speaker_id"], 1)
        seconds = speaker_speech_seconds(output)
        self.assertAlmostEqual(seconds[1], 12.0)
        self.assertAlmostEqual(seconds[2], 3.0)

    def test_explicit_host_guest_swap_still_runs_after_promote(self) -> None:
        output = _output(
            _row(0, 20.0, 0.0),
            _row(1, 3.0, 20.0),
            _row(2, 12.0, 23.0),
        )
        promote_extra_speakers_in_output(output)
        swap_speaker_ids_in_output(output)
        # After promote, real guest is 1; explicit 0<->1 swap then makes guest 0.
        self.assertEqual(output["0"]["speaker_id"], 1)
        self.assertEqual(output["2"]["speaker_id"], 0)


if __name__ == "__main__":
    unittest.main()
