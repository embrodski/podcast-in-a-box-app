"""Unit tests for podcast_dsl delivery / intermediate H.264 encoder args."""

from __future__ import annotations

import os
import unittest

from podcast_dsl.video_renderer import (
    DELIVERY_AUDIO_BITRATE,
    DELIVERY_QUALITY_LEVEL,
    DELIVERY_VIDEO_BITRATE,
    DELIVERY_VIDEO_BUFSIZE,
    DELIVERY_VIDEO_MAXRATE,
    VIDEO_HARDWARE_ENCODE_ENV,
    _append_video_encoder_args,
    _hardware_encode_requested,
    _map_encoder_preset,
    _resolved_encoder_memo,
)


class NvencPresetMapTests(unittest.TestCase):
    def test_fast_maps_to_p4(self) -> None:
        self.assertEqual(_map_encoder_preset("h264_nvenc", "fast"), "p4")


class EncoderArgsTests(unittest.TestCase):
    def test_nvenc_intermediate_uses_unconstrained_cq(self) -> None:
        cmd: list[str] = []
        _append_video_encoder_args(cmd, "h264_nvenc", "fast", 23, delivery=False)
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "h264_nvenc")
        self.assertEqual(cmd[cmd.index("-preset") + 1], "p4")
        self.assertEqual(cmd[cmd.index("-cq") + 1], "23")
        self.assertEqual(cmd[cmd.index("-b:v") + 1], "0")
        self.assertNotIn("-maxrate", cmd)

    def test_nvenc_delivery_uses_youtube_like_vbr_caps(self) -> None:
        cmd: list[str] = []
        _append_video_encoder_args(
            cmd, "h264_nvenc", "fast", DELIVERY_QUALITY_LEVEL, delivery=True
        )
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "h264_nvenc")
        self.assertEqual(cmd[cmd.index("-preset") + 1], "p4")
        self.assertEqual(cmd[cmd.index("-tune") + 1], "hq")
        self.assertEqual(cmd[cmd.index("-rc") + 1], "vbr")
        self.assertEqual(cmd[cmd.index("-cq") + 1], str(DELIVERY_QUALITY_LEVEL))
        self.assertEqual(cmd[cmd.index("-b:v") + 1], DELIVERY_VIDEO_BITRATE)
        self.assertEqual(cmd[cmd.index("-maxrate") + 1], DELIVERY_VIDEO_MAXRATE)
        self.assertEqual(cmd[cmd.index("-bufsize") + 1], DELIVERY_VIDEO_BUFSIZE)

    def test_libx264_delivery_uses_crf_with_rate_caps(self) -> None:
        cmd: list[str] = []
        _append_video_encoder_args(
            cmd, "libx264", "fast", DELIVERY_QUALITY_LEVEL, delivery=True
        )
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "libx264")
        self.assertEqual(cmd[cmd.index("-crf") + 1], str(DELIVERY_QUALITY_LEVEL))
        self.assertEqual(cmd[cmd.index("-b:v") + 1], DELIVERY_VIDEO_BITRATE)
        self.assertEqual(cmd[cmd.index("-maxrate") + 1], DELIVERY_VIDEO_MAXRATE)
        self.assertEqual(cmd[cmd.index("-bufsize") + 1], DELIVERY_VIDEO_BUFSIZE)
        self.assertNotIn("-cq", cmd)

    def test_delivery_audio_bitrate_constant(self) -> None:
        self.assertEqual(DELIVERY_AUDIO_BITRATE, "192k")


class HardwareEncodeDefaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self._prev = os.environ.get(VIDEO_HARDWARE_ENCODE_ENV)
        _resolved_encoder_memo.clear()

    def tearDown(self) -> None:
        _resolved_encoder_memo.clear()
        if self._prev is None:
            os.environ.pop(VIDEO_HARDWARE_ENCODE_ENV, None)
        else:
            os.environ[VIDEO_HARDWARE_ENCODE_ENV] = self._prev

    def test_unset_env_defaults_to_hardware(self) -> None:
        os.environ.pop(VIDEO_HARDWARE_ENCODE_ENV, None)
        self.assertTrue(_hardware_encode_requested())

    def test_explicit_off(self) -> None:
        os.environ[VIDEO_HARDWARE_ENCODE_ENV] = "0"
        self.assertFalse(_hardware_encode_requested())


if __name__ == "__main__":
    unittest.main()
