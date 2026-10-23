#!/usr/bin/env python3
"""Unit tests for streaming multipart body in elevenlabs_transcribe_wav."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from elevenlabs_transcribe_wav import (  # noqa: E402
    _COMPRESS_THRESHOLD_BYTES,
    _MultipartStream,
    _should_compress_for_upload,
)


class MultipartStreamTests(unittest.TestCase):
    def test_stream_matches_full_body_and_length(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.wav"
            # Not a real WAV; bytes only matter for multipart framing.
            payload = b"RIFF" + (b"\x00\x01" * 50_000)
            path.write_bytes(payload)

            fields = {"model_id": "scribe_v2", "diarize": "true"}
            stream = _MultipartStream(fields, path)
            try:
                full = stream.read()
                self.assertEqual(len(stream), len(full))
                self.assertTrue(full.startswith(b"--"))
                self.assertIn(b'name="model_id"', full)
                self.assertIn(b'filename="sample.wav"', full)
                self.assertTrue(full.endswith(b"--\r\n") or full.rstrip().endswith(b"--"))
                # File bytes appear exactly once between headers and closing boundary.
                self.assertEqual(full.count(payload), 1)
            finally:
                stream.close()

    def test_chunked_read_equals_read_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chunk.wav"
            payload = b"abcdefgh" * 10_000
            path.write_bytes(payload)
            fields = {"model_id": "scribe_v2"}
            stream = _MultipartStream(fields, path)
            try:
                parts: list[bytes] = []
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    parts.append(chunk)
                joined = b"".join(parts)
                self.assertEqual(len(joined), len(stream))
                self.assertEqual(joined.count(payload), 1)
            finally:
                stream.close()

    def test_should_compress_only_large_wav(self) -> None:
        import elevenlabs_transcribe_wav as mod
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            small = Path(tmp) / "small.wav"
            small.write_bytes(b"x" * 1024)
            with patch.object(mod, "_COMPRESS_THRESHOLD_BYTES", 10_000):
                self.assertFalse(_should_compress_for_upload(small))

                large = Path(tmp) / "large.wav"
                large.write_bytes(b"x" * 10_000)
                self.assertTrue(_should_compress_for_upload(large))

                mp3 = Path(tmp) / "large.mp3"
                mp3.write_bytes(b"x" * 10_000)
                self.assertFalse(_should_compress_for_upload(mp3))

        # Sanity: production threshold stays in a sensible range for PIAB WAVs.
        self.assertGreaterEqual(_COMPRESS_THRESHOLD_BYTES, 16 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
