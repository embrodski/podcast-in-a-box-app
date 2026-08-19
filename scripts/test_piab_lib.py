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
    resolve_init_layout,
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

    def test_init_layout_creates_under_work_root_and_scans_dump_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dump = Path(tmp) / "PodcastRoom"
            work = dump / "PodcastInABox"
            dump.mkdir()
            work.mkdir()

            def fake_list(path: Path, skipped=None) -> list[MediaInfo]:
                if path == dump.resolve():
                    return [_info("parent.mp4", "video", 1, 100)]
                return []

            with patch("piab_lib.list_top_level_multicorder", side_effect=fake_list):
                working, scan_dir, name, mode = resolve_init_layout(
                    mode="default",
                    root=work,
                    name="Bayeswatch",
                    working_folder=None,
                    scan_root=dump,
                )
            self.assertEqual(mode, "default")
            self.assertEqual(name, "Bayeswatch")
            self.assertEqual(working, (work / "Bayeswatch").resolve())
            self.assertEqual(scan_dir, dump.resolve())

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

    def test_cancel_keeps_finished_copies_and_deletes_partial(self) -> None:
        from piab_lib import LabelApplyCancelled, move_labeled_media

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dump = root / "dump"
            dump.mkdir()
            raw = root / "Session" / "Raw"
            raw.mkdir(parents=True)
            host_video = dump / "host.mp4"
            guest_video = dump / "guest.mp4"
            wide_video = dump / "wide.mp4"
            host_audio = dump / "host.wav"
            guest_audio = dump / "guest.wav"
            host_video.write_bytes(b"h" * 128)
            guest_video.write_bytes(b"g" * (512 * 1024))
            wide_video.write_bytes(b"w" * 128)
            host_audio.write_bytes(b"a" * 128)
            guest_audio.write_bytes(b"b" * 128)

            finished_first = {"done": False}

            def should_cancel() -> bool:
                return finished_first["done"]

            def on_copy(src, dest, index, total, phase) -> None:
                if phase == "done" and dest.name == "Host Raw Video.mp4":
                    finished_first["done"] = True

            state = {"paths": {"raw": str(raw)}}
            with self.assertRaises(LabelApplyCancelled) as raised:
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
                    on_copy=on_copy,
                    should_cancel=should_cancel,
                )
            exc = raised.exception
            self.assertTrue((raw / "Host Raw Video.mp4").is_file())
            self.assertFalse((raw / "Guest Raw Video.mp4").exists())
            self.assertEqual(len(exc.completed), 1)
            self.assertEqual(exc.completed[0]["dest_name"], "Host Raw Video.mp4")
            self.assertGreaterEqual(len(exc.remaining), 1)
            self.assertEqual(exc.remaining[0]["dest_name"], "Guest Raw Video.mp4")
            if exc.partial is not None:
                self.assertEqual(exc.partial["dest_name"], "Guest Raw Video.mp4")

    def test_cancel_mid_file_deletes_partial(self) -> None:
        from piab_lib import LabelApplyCancelled, move_labeled_media

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dump = root / "dump"
            dump.mkdir()
            raw = root / "Session" / "Raw"
            raw.mkdir(parents=True)
            host_video = dump / "host.mp4"
            guest_video = dump / "guest.mp4"
            wide_video = dump / "wide.mp4"
            host_audio = dump / "host.wav"
            guest_audio = dump / "guest.wav"
            host_video.write_bytes(b"h" * (1024 * 1024))
            for path in (guest_video, wide_video, host_audio, guest_audio):
                path.write_bytes(b"x" * 64)

            checks = {"n": 0}

            def should_cancel() -> bool:
                checks["n"] += 1
                return checks["n"] > 3

            state = {"paths": {"raw": str(raw)}}
            with self.assertRaises(LabelApplyCancelled) as raised:
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
                    should_cancel=should_cancel,
                )
            self.assertFalse((raw / "Host Raw Video.mp4").exists())
            self.assertEqual(raised.exception.partial["dest_name"], "Host Raw Video.mp4")

    def test_cancel_writes_session_resume_log(self) -> None:
        from piab_apply_labels import apply_labeled_media_session
        from piab_lib import LabelApplyCancelled, load_piab_state, save_piab_state

        with tempfile.TemporaryDirectory() as tmp:
            working = Path(tmp) / "Session"
            dump = Path(tmp) / "dump"
            raw = working / "Raw"
            dump.mkdir()
            raw.mkdir(parents=True)
            host_video = dump / "host.mp4"
            guest_video = dump / "guest.mp4"
            wide_video = dump / "wide.mp4"
            host_audio = dump / "host.wav"
            guest_audio = dump / "guest.wav"
            host_video.write_bytes(b"h" * 128)
            for path in (guest_video, wide_video, host_audio, guest_audio):
                path.write_bytes(b"x" * 64)
            save_piab_state(
                working,
                {
                    "kind": "podcast_in_a_box",
                    "name": "CancelTest",
                    "source_duration_sec": 1,
                    "paths": {"raw": str(raw), "episode_folder": str(working)},
                    "steps": {},
                },
            )
            finished_first = {"done": False}

            def should_cancel() -> bool:
                return finished_first["done"]

            def on_copy(src, dest, index, total, phase) -> None:
                if phase == "done" and dest.name == "Host Raw Video.mp4":
                    finished_first["done"] = True

            with self.assertRaises(LabelApplyCancelled):
                apply_labeled_media_session(
                    working,
                    video_labels={
                        str(host_video): "host",
                        str(guest_video): "guest",
                        str(wide_video): "wide",
                    },
                    audio_labels={
                        str(host_audio): "host",
                        str(guest_audio): "guest",
                    },
                    on_copy=on_copy,
                    should_cancel=should_cancel,
                )
            state = load_piab_state(working)
            self.assertEqual(state["resume_at"], "04a_apply_labels")
            self.assertEqual(state["label_apply"]["status"], "cancelled")
            self.assertEqual(
                state["label_apply"]["completed"][0]["dest_name"],
                "Host Raw Video.mp4",
            )
            remaining_names = [row["dest_name"] for row in state["label_apply"]["remaining"]]
            self.assertIn("Guest Raw Video.mp4", remaining_names)
            self.assertEqual(state["last_abort"]["interrupted_step"], "04a_apply_labels")
            self.assertIn("label_paths", state)

    def test_resume_skips_already_copied_raw_files(self) -> None:
        from piab_lib import move_labeled_media

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dump = root / "dump"
            dump.mkdir()
            raw = root / "Session" / "Raw"
            raw.mkdir(parents=True)
            host_video = dump / "host.mp4"
            guest_video = dump / "guest.mp4"
            wide_video = dump / "wide.mp4"
            host_audio = dump / "host.wav"
            guest_audio = dump / "guest.wav"
            for path, payload in (
                (host_video, b"host"),
                (guest_video, b"guest"),
                (wide_video, b"wide"),
                (host_audio, b"ha"),
                (guest_audio, b"ga"),
            ):
                path.write_bytes(payload)
            already = raw / "Host Raw Video.mp4"
            already.write_bytes(b"host")

            skipped: list[str] = []

            def on_copy(src, dest, index, total, phase) -> None:
                if phase == "skipped":
                    skipped.append(dest.name)

            state = {"paths": {"raw": str(raw)}}
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
                on_copy=on_copy,
            )
            self.assertIn("Host Raw Video.mp4", skipped)
            self.assertTrue((raw / "Guest Raw Video.mp4").is_file())
            self.assertEqual((raw / "Host Raw Video.mp4").read_bytes(), b"host")

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
