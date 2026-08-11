"""Tests for harness_av_sync_lib sync confidence detection."""

from __future__ import annotations

import unittest

from harness_av_sync_lib import sync_confidence_failed


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


if __name__ == "__main__":
    unittest.main()
