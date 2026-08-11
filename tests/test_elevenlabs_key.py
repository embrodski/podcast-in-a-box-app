"""Tests for ElevenLabs key resolution."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from harness_episode_lib import (  # noqa: E402
    ELEVENLABS_KEY_FILENAME,
    find_elevenlabs_key_file,
    read_elevenlabs_api_key,
)


class ElevenLabsKeyTests(unittest.TestCase):
    def test_prefers_env_var(self) -> None:
        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "sk-test"}, clear=False):
            self.assertEqual(read_elevenlabs_api_key(), "sk-test")

    def test_falls_back_to_upstream_key_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            upstream = Path(tmp) / "upstream"
            upstream.mkdir()
            key_file = upstream / ELEVENLABS_KEY_FILENAME
            key_file.write_text("file-key\n", encoding="utf-8")
            env = os.environ.copy()
            env.pop("ELEVENLABS_API_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                with patch(
                    "harness_episode_lib.elevenlabs_key_file_candidates",
                    return_value=[
                        Path(tmp) / "app" / ELEVENLABS_KEY_FILENAME,
                        key_file,
                    ],
                ):
                    self.assertEqual(find_elevenlabs_key_file(), key_file)
                    self.assertEqual(read_elevenlabs_api_key(), "file-key")


if __name__ == "__main__":
    unittest.main()
