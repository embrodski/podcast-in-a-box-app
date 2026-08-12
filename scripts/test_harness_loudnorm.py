"""Tests for two-pass loudnorm prep normalization."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness_loudnorm import (
    LoudnormMeasurement,
    PREPPED_TARGET_I_LUFS,
    build_loudnorm_pass1_filter,
    build_loudnorm_pass2_filter,
    build_trimmed_audio_pass1_filter,
    build_trimmed_audio_pass2_filter_chain,
    measure_loudnorm,
    normalize_wav_two_pass,
    parse_loudnorm_json,
)


class LoudnormFilterTests(unittest.TestCase):
    def test_pass1_filter_includes_target_and_json(self) -> None:
        filt = build_loudnorm_pass1_filter()
        self.assertIn("I=-14.0", filt)
        self.assertIn("print_format=json", filt)

    def test_pass2_filter_uses_measured_linear(self) -> None:
        measured = LoudnormMeasurement(
            input_i=-27.0,
            input_tp=-4.0,
            input_lra=7.0,
            input_thresh=-37.0,
            target_offset=13.0,
        )
        filt = build_loudnorm_pass2_filter(measured)
        self.assertIn("measured_I=-27.00", filt)
        self.assertIn("offset=13.00", filt)
        self.assertIn("linear=true", filt)

    def test_trimmed_pass1_includes_atrim(self) -> None:
        filt = build_trimmed_audio_pass1_filter(1.25)
        self.assertIn("atrim=start=1.25", filt)
        self.assertIn("print_format=json", filt)

    def test_trimmed_pass2_chains_trim_and_loudnorm(self) -> None:
        measured = LoudnormMeasurement(
            input_i=-20.0,
            input_tp=-3.0,
            input_lra=8.0,
            input_thresh=-30.0,
            target_offset=6.0,
        )
        filt = build_trimmed_audio_pass2_filter_chain(0.5, measured)
        self.assertIn("atrim=start=0.5", filt)
        self.assertIn("measured_I=-20.00", filt)

    def test_parse_loudnorm_json_from_stderr_blob(self) -> None:
        blob = '[Parsed_loudnorm_0 @ 0x0] \n{\n\t"input_i" : "-21.75",\n\t"target_offset" : "0.05"\n}\n'
        data = parse_loudnorm_json(blob)
        self.assertEqual(data["input_i"], "-21.75")


class MeasureLoudnormCommandTests(unittest.TestCase):
    def test_measure_loudnorm_uses_vn_and_audio_map(self) -> None:
        measured = LoudnormMeasurement(
            input_i=-18.0,
            input_tp=-2.0,
            input_lra=6.0,
            input_thresh=-28.0,
            target_offset=4.0,
        )
        with patch("harness_loudnorm._run") as run:
            run.return_value.stderr = (
                '{"input_i":"-18.0","input_tp":"-2.0","input_lra":"6.0",'
                '"input_thresh":"-28.0","target_offset":"4.0"}'
            )
            result = measure_loudnorm(Path("sample.mp4"))
        self.assertEqual(result.input_i, -18.0)
        cmd = run.call_args.args[0]
        self.assertIn("-vn", cmd)
        self.assertIn("-map", cmd)
        self.assertIn("0:a:0?", cmd)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not on PATH")
class LoudnormIntegrationTests(unittest.TestCase):
    def _make_quiet_wav(self, path: Path) -> None:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=1.5",
                "-af",
                "volume=-20dB",
                "-ac",
                "2",
                "-ar",
                "48000",
                "-c:a",
                "pcm_s16le",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_two_pass_wav_moves_toward_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "quiet.wav"
            out = Path(tmp) / "normalized.wav"
            self._make_quiet_wav(src)

            before = measure_loudnorm(src)
            self.assertLess(before.input_i, PREPPED_TARGET_I_LUFS - 3.0)

            normalize_wav_two_pass(src, out)
            self.assertTrue(out.is_file())

            after = measure_loudnorm(out)
            self.assertAlmostEqual(after.input_i, PREPPED_TARGET_I_LUFS, delta=1.0)


if __name__ == "__main__":
    unittest.main()
