"""Unit tests for Podcast In A Box discovery / estimates / labeling helpers."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from piab_lib import (
    MediaInfo,
    _max_window_rms,
    cluster_session_files,
    estimate_full_render,
    estimate_prep_through_one_min,
    resolve_scan_dir,
    role_to_audio_name,
    role_to_video_name,
    validate_audio_labels,
    validate_video_labels,
)
import numpy as np


def _info(name: str, kind: str, mtime: float, duration: float) -> MediaInfo:
    return MediaInfo(
        path=f"E:/PodcastRoom/{name}",
        name=name,
        kind=kind,
        mtime=mtime,
        mtime_iso="2026-07-10T16:49:00",
        duration_sec=duration,
    )


class AudibleContentTests(unittest.TestCase):
    def test_max_window_rms_detects_silence(self) -> None:
        rate = 16000
        silent = np.zeros(rate * 2, dtype=np.float32)
        loud = np.zeros(rate * 2, dtype=np.float32)
        loud[rate : rate + 8000] = 0.5
        self.assertLess(_max_window_rms(silent, rate), 0.008)
        self.assertGreater(_max_window_rms(loud, rate), 0.008)

    def test_find_loud_clip_starts_enforces_min_separation(self) -> None:
        from piab_lib import MIN_PREVIEW_CLIP_SEPARATION_SEC, find_loud_clip_starts

        fd, name = tempfile.mkstemp(suffix=".wav")
        path = Path(name)
        os.close(fd)
        try:
            with (
                patch("piab_lib.ffprobe_duration", return_value=120.0),
                patch("piab_lib.find_loud_clip_start", side_effect=[30.0, 30.0]),
                patch("piab_lib.find_loud_clip_start_after", return_value=30.0),
                patch("piab_lib.find_loud_clip_start_before", return_value=18.0),
            ):
                starts = find_loud_clip_starts(path)
            self.assertEqual(len(starts), 2)
            self.assertGreaterEqual(
                abs(starts[1] - starts[0]),
                MIN_PREVIEW_CLIP_SEPARATION_SEC,
            )
            self.assertEqual(starts[1], 18.0)
        finally:
            path.unlink(missing_ok=True)

    def test_find_loud_clip_starts_uses_before_when_after_unavailable(self) -> None:
        from piab_lib import find_loud_clip_starts

        fd, name = tempfile.mkstemp(suffix=".wav")
        path = Path(name)
        os.close(fd)
        try:
            with (
                patch("piab_lib.ffprobe_duration", return_value=35.0),
                patch("piab_lib.find_loud_clip_start", side_effect=[30.0, 30.0]),
                patch("piab_lib.find_loud_clip_start_after", return_value=30.0),
                patch("piab_lib.find_loud_clip_start_before", return_value=18.0),
            ):
                starts = find_loud_clip_starts(path)
            self.assertEqual(starts, [30.0, 18.0])
        finally:
            path.unlink(missing_ok=True)

    def test_find_loud_clip_starts_prefers_after_when_closer_to_target(self) -> None:
        from piab_lib import find_loud_clip_starts

        fd, name = tempfile.mkstemp(suffix=".wav")
        path = Path(name)
        os.close(fd)
        try:
            with (
                patch("piab_lib.ffprobe_duration", return_value=120.0),
                patch("piab_lib.find_loud_clip_start", side_effect=[30.0, 32.0]),
                patch("piab_lib.find_loud_clip_start_after", return_value=72.0),
                patch("piab_lib.find_loud_clip_start_before", return_value=10.0),
            ):
                starts = find_loud_clip_starts(path)
            self.assertEqual(starts, [30.0, 72.0])
        finally:
            path.unlink(missing_ok=True)


class ResolveScanDirTests(unittest.TestCase):
    def test_prefers_working_folder_when_it_has_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            working = root / "Jessiah"
            working.mkdir()

            def fake_list(path: Path, skipped=None) -> list[MediaInfo]:
                if path == working.resolve():
                    return [_info("child.mp4", "video", 1, 100)]
                return [_info("parent.mp4", "video", 1, 100)]

            with patch("piab_lib.list_top_level_multicorder", side_effect=fake_list):
                self.assertEqual(resolve_scan_dir(root=root, working=working), working.resolve())

    def test_falls_back_to_root_for_empty_working_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            working = root / "Viv"
            working.mkdir()

            def fake_list(path: Path, skipped=None) -> list[MediaInfo]:
                if path == working.resolve():
                    return []
                return [_info("parent.mp4", "video", 1, 100)]

            with patch("piab_lib.list_top_level_multicorder", side_effect=fake_list):
                self.assertEqual(resolve_scan_dir(root=root, working=working), root.resolve())

    def test_falls_back_to_root_when_working_folder_does_not_exist_yet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            working = root / "2026-07-29_1545"

            def fake_list(path: Path, skipped=None) -> list[MediaInfo]:
                self.assertEqual(path, root.resolve())
                return [_info("parent.mp4", "video", 1, 100)]

            with patch("piab_lib.list_top_level_multicorder", side_effect=fake_list):
                self.assertEqual(resolve_scan_dir(root=root, working=working), root.resolve())
    def test_picks_recent_cluster(self) -> None:
        old = [
            _info("MultiCorder1 - DeckLink Quad HDMI Recorder old.mp4", "video", 1000, 100),
            _info("MultiCorder2 - Output 1 old.wav", "audio", 1001, 100),
        ]
        new = [
            _info("MultiCorder1 - DeckLink Quad HDMI Recorder a.mp4", "video", 5000, 1873.0),
            _info("MultiCorder2 - DeckLink Quad HDMI Recorder b.mp4", "video", 5005, 1873.5),
            _info("MultiCorder3 - DeckLink Quad HDMI Recorder c.mp4", "video", 5010, 1872.2),
            _info("MultiCorder4 - Output 1 a.wav", "audio", 5002, 1873.1),
            _info("MultiCorder5 - Output 2 b.wav", "audio", 5008, 1873.0),
        ]
        cluster = cluster_session_files(old + new)
        names = {f.name for f in cluster}
        self.assertTrue(names.isdisjoint({f.name for f in old}))
        self.assertEqual(len(cluster), 5)

    def test_duration_filter(self) -> None:
        files = [
            _info("MultiCorder1 - DeckLink Quad HDMI Recorder a.mp4", "video", 5000, 1873.0),
            _info("MultiCorder2 - DeckLink Quad HDMI Recorder b.mp4", "video", 5001, 900.0),
            _info("MultiCorder3 - Output 1 a.wav", "audio", 5002, 1873.0),
        ]
        cluster = cluster_session_files(files)
        self.assertEqual(len(cluster), 2)
        self.assertTrue(all(abs(f.duration_sec - 1873.0) <= 2 for f in cluster))


class LabelValidationTests(unittest.TestCase):
    def test_video_ok(self) -> None:
        validate_video_labels(
            {"a.mp4": "host", "b.mp4": "guest", "c.mp4": "wide", "d.mp4": "do_not_use"}
        )

    def test_video_missing_wide(self) -> None:
        with self.assertRaises(ValueError):
            validate_video_labels({"a.mp4": "host", "b.mp4": "guest", "c.mp4": "do_not_use"})

    def test_audio_ok(self) -> None:
        validate_audio_labels({"a.wav": "host", "b.wav": "guest", "c.wav": "do_not_use"})

    def test_role_names(self) -> None:
        self.assertEqual(role_to_video_name("host"), "Host Raw Video.mp4")
        self.assertEqual(role_to_audio_name("guest"), "Guest Raw Audio.wav")


class EstimateTests(unittest.TestCase):
    def test_prep_and_full_ranges(self) -> None:
        prep = estimate_prep_through_one_min(1873.0)
        full = estimate_full_render(1873.0)
        self.assertIn("summary", prep)
        self.assertGreater(prep["high_sec"], prep["low_sec"])
        self.assertGreater(full["high_sec"], full["low_sec"])
        self.assertIn("–", prep["summary"])
        # Full render is parallel cut/assemble (~0.5×), much faster than prep.
        self.assertEqual(full["breakdown"]["full_render_x"], 0.5)
        self.assertAlmostEqual(full["center_sec"], int(round(1873.0 * 0.5)))


class ClassifyNameTests(unittest.TestCase):
    def test_classify(self) -> None:
        from piab_lib import classify_multicorder

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "MultiCorder1 - DeckLink Quad HDMI Recorder (1).MP4"
            mini = root / "MultiCorder5 - DeckLink Mini Recorder 4K 5 - 29 July 2026.mp4"
            audio = root / "MultiCorder2 - Output 3 (mix).wav"
            other = root / "random.mp4"
            for path in (video, mini, audio, other):
                path.write_bytes(b"x")
            self.assertEqual(classify_multicorder(video), "video")
            self.assertEqual(classify_multicorder(mini), "video")
            self.assertEqual(classify_multicorder(audio), "audio")
            self.assertIsNone(classify_multicorder(other))


class DiscoverClustersTests(unittest.TestCase):
    def test_finds_multiple_sessions(self) -> None:
        from piab_lib import discover_all_clusters

        old = [
            _info("MultiCorder1 - DeckLink Quad HDMI Recorder old.mp4", "video", 1000, 100),
            _info("MultiCorder2 - Output 1 old.wav", "audio", 1001, 100),
            _info("MultiCorder3 - DeckLink Mini Recorder 4K old2.mp4", "video", 1002, 100),
        ]
        new = [
            _info("MultiCorder1 - DeckLink Quad HDMI Recorder a.mp4", "video", 5000, 1873.0),
            _info("MultiCorder2 - DeckLink Quad HDMI Recorder b.mp4", "video", 5005, 1873.5),
            _info("MultiCorder3 - DeckLink Quad HDMI Recorder c.mp4", "video", 5010, 1872.2),
            _info("MultiCorder4 - Output 1 a.wav", "audio", 5002, 1873.1),
            _info("MultiCorder5 - Output 2 b.wav", "audio", 5008, 1873.0),
        ]
        clusters = discover_all_clusters(new + old)
        self.assertEqual(len(clusters), 2)
        self.assertEqual(len(clusters[0]), 5)
        self.assertEqual(len(clusters[1]), 3)


class CopyLabeledMediaTests(unittest.TestCase):
    def test_copy_leaves_sources_in_place(self) -> None:
        from piab_lib import move_labeled_media

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dump = root / "dump"
            dump.mkdir()
            working = root / "Session"
            raw = working / "Raw"
            raw.mkdir(parents=True)

            host_video = dump / "MultiCorder1 - DeckLink Quad HDMI Recorder (1) 1.mp4"
            guest_video = dump / "MultiCorder2 - DeckLink Quad HDMI Recorder (2) 2.mp4"
            wide_video = dump / "MultiCorder3 - DeckLink Quad HDMI Recorder (3) 3.mp4"
            host_audio = dump / "MultiCorder6 - Output 1 a.wav"
            guest_audio = dump / "MultiCorder7 - Output 2 b.wav"
            for path in (host_video, guest_video, wide_video, host_audio, guest_audio):
                path.write_bytes(b"x" * 128)

            state = {
                "paths": {"raw": str(raw)},
            }
            move_labeled_media(
                state,
                video_labels={
                    str(host_video): "host",
                    str(guest_video): "guest",
                    str(wide_video): "wide",
                },
                audio_labels={
                    str(host_audio): "host",
                    str(guest_audio): "guest",
                },
            )

            for path in (host_video, guest_video, wide_video, host_audio, guest_audio):
                self.assertTrue(path.is_file(), f"source should remain: {path}")
            self.assertTrue((raw / "Host Raw Video.mp4").is_file())
            self.assertTrue((raw / "Guest Raw Audio.wav").is_file())
            self.assertEqual(len(state["original_paths"]), 5)
            self.assertEqual(state["copied_raw"]["host"], str((raw / "Host Raw Video.mp4").resolve()))

    def test_restore_moved_sources_from_raw_copies(self) -> None:
        from piab_lib import restore_moved_sources

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dump = root / "dump"
            dump.mkdir()
            working = root / "Session"
            raw = working / "Raw"
            raw.mkdir(parents=True)
            original = dump / "MultiCorder1 - DeckLink Quad HDMI Recorder (1) 1.mp4"
            raw_copy = raw / "Host Raw Video.mp4"
            raw_copy.write_bytes(b"video")

            state = {
                "paths": {"raw": str(raw)},
                "original_paths": {
                    "Host Raw Video.mp4": str(original),
                },
            }
            restored = restore_moved_sources(state, working, allow_overwrite=True)
            self.assertEqual(len(restored), 1)
            self.assertTrue(original.is_file())
            self.assertTrue(raw_copy.is_file())


class PiabStatePathTests(unittest.TestCase):
    def test_save_load_roundtrip(self) -> None:
        from harness_episode_lib import load_episode_state, save_episode_state
        from piab_lib import new_piab_state, save_piab_state

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "Demo Session"
            folder.mkdir()
            (folder / "Raw").mkdir()
            (folder / "Input").mkdir()
            (folder / "Output").mkdir()
            (folder / "Temp").mkdir()
            files = [
                _info("MultiCorder1 - DeckLink Quad HDMI Recorder a.mp4", "video", 1, 100),
                _info("MultiCorder2 - Output 1 a.wav", "audio", 1, 100),
            ]
            # Patch paths to exist for MediaInfo realism
            for f in files:
                p = folder / f.name
                p.write_bytes(b"x")
                f.path = str(p)
            state = new_piab_state(folder, name="Demo Session", scan_root=folder.parent, session_files=files)
            save_piab_state(folder, state)
            loaded = load_episode_state(folder)
            self.assertEqual(loaded["kind"], "podcast_in_a_box")
            self.assertTrue((folder / "podcast-in-a-box.json").is_file())


if __name__ == "__main__":
    unittest.main()
