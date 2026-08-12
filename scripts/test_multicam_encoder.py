"""Tests for multicam prep video encoder selection and fallback."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import multicam_align_trim as multicam


class EncoderArgsTests(unittest.TestCase):
    def test_nvenc_quality_args(self) -> None:
        args = multicam._video_encoder_args("h264_nvenc", 20)
        self.assertIn("h264_nvenc", args)
        self.assertIn("-cq", args)
        self.assertNotIn("-crf", args)

    def test_libx264_quality_args(self) -> None:
        args = multicam._video_encoder_args("libx264", 20)
        self.assertIn("libx264", args)
        self.assertIn("-crf", args)


class EncoderFallbackTests(unittest.TestCase):
    def test_hardware_failure_retries_libx264(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "output.mp4"
            with patch.object(
                multicam,
                "_run",
                side_effect=[RuntimeError("NVENC unavailable"), None],
            ) as run:
                used = multicam._trim_av_reencode(
                    Path(td) / "input.mp4",
                    output,
                    trim_sec=0.1,
                    crf=20,
                    audio_bitrate="192k",
                    downscale_1080p=True,
                    video_encoder="h264_nvenc",
                )

        self.assertEqual(used, "libx264")
        self.assertEqual(run.call_count, 2)
        self.assertIn("h264_nvenc", run.call_args_list[0].args[0])
        self.assertIn("libx264", run.call_args_list[1].args[0])

    def test_loudnorm_pass2_in_filter_complex(self) -> None:
        from harness_loudnorm import LoudnormMeasurement

        measured = LoudnormMeasurement(
            input_i=-20.0,
            input_tp=-3.0,
            input_lra=8.0,
            input_thresh=-30.0,
            target_offset=6.0,
        )
        with tempfile.TemporaryDirectory() as td:
            with patch.object(multicam, "_run") as run:
                multicam._trim_av_reencode(
                    Path(td) / "input.mp4",
                    Path(td) / "output.mp4",
                    trim_sec=0.25,
                    crf=20,
                    audio_bitrate="192k",
                    downscale_1080p=False,
                    video_encoder="libx264",
                    loudnorm_measured=measured,
                )
        fc = run.call_args.args[0][run.call_args.args[0].index("-filter_complex") + 1]
        self.assertIn("loudnorm", fc)
        self.assertIn("measured_I=-20.00", fc)
        self.assertIn("atrim=start=0.25", fc)

    def test_libx264_failure_does_not_retry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch.object(
                multicam, "_run", side_effect=RuntimeError("encode failed")
            ) as run:
                with self.assertRaises(RuntimeError):
                    multicam._trim_av_reencode(
                        Path(td) / "input.mp4",
                        Path(td) / "output.mp4",
                        trim_sec=0.1,
                        crf=20,
                        audio_bitrate="192k",
                        downscale_1080p=False,
                        video_encoder="libx264",
                    )
        run.assert_called_once()


class EncoderResolutionTests(unittest.TestCase):
    def test_auto_uses_first_available_hardware_encoder(self) -> None:
        with patch.object(
            multicam,
            "_encoder_is_usable",
            side_effect=lambda encoder: encoder == "h264_qsv",
        ):
            self.assertEqual(multicam._resolve_video_encoder("auto"), "h264_qsv")

    def test_auto_falls_back_to_libx264(self) -> None:
        with patch.object(multicam, "_encoder_is_usable", return_value=False):
            self.assertEqual(multicam._resolve_video_encoder("auto"), "libx264")


if __name__ == "__main__":
    unittest.main()
