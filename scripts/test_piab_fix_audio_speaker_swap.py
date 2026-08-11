"""Tests for piab_fix_audio_speaker_swap (speaker-ID mapping fix, no Raw swap)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from piab_fix_audio_speaker_swap import fix_audio_speaker_swap
from piab_lib import save_piab_state


class FixAudioSpeakerSwapTests(unittest.TestCase):
    def _write_state(self, root: Path, *, swap_speaker_ids: bool = False) -> None:
        transcript = root / "Input" / "Host Clean Audio-prepped Transcript.json"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text("{}", encoding="utf-8")
        state = {
            "kind": "podcast_in_a_box",
            "swap_speaker_ids": swap_speaker_ids,
            "paths": {
                "raw": str(root / "Raw"),
                "input": str(root / "Input"),
                "output": str(root / "Output"),
                "temp": str(root / "Temp"),
                "state": str(root / "podcast-in-a-box.json"),
            },
            "main_prepped": {
                "prepped_videos": [
                    str(root / "Input" / "Host Video-prepped.mp4"),
                    str(root / "Input" / "Guest Video-prepped.mp4"),
                    str(root / "Input" / "Wide Video-prepped.mp4"),
                ],
                "prepped_audio_wav": str(root / "Input" / "Host Clean Audio-prepped.wav"),
            },
            "main_transcript_json": str(transcript),
            "steps": {},
        }
        save_piab_state(root, state)

    def test_toggles_swap_speaker_ids_and_rerenders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_state(root, swap_speaker_ids=False)
            out = root / "Output" / "1 Min Test.mp4"

            with patch(
                "piab_fix_audio_speaker_swap.rerun_one_min_test",
                return_value=out,
            ) as rerun:
                result = fix_audio_speaker_swap(root, allow_overwrite=True)

            self.assertEqual(result, out)
            rerun.assert_called_once_with(root, allow_overwrite=True)
            state = json.loads((root / "podcast-in-a-box.json").read_text(encoding="utf-8"))
            self.assertTrue(state["swap_speaker_ids"])
            self.assertIn("swap_or_relabel", state["steps"])
            self.assertTrue(state["steps"]["swap_or_relabel"].get("raw_files_unchanged"))

    def test_requires_prep_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "podcast-in-a-box.json").write_text(
                json.dumps(
                    {
                        "kind": "podcast_in_a_box",
                        "paths": {"state": str(root / "podcast-in-a-box.json")},
                        "steps": {},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(FileNotFoundError):
                fix_audio_speaker_swap(root, allow_overwrite=True)


if __name__ == "__main__":
    unittest.main()
