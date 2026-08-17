"""Tests for Clean Old Working Files."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from piab_clean_working_files import (
    clean_project_working_files,
    list_clean_candidates,
    removable_working_subfolders,
    scan_lost_clean_candidates,
)
from piab_process_log import upsert_process_log_entry


class CleanWorkingFilesTests(unittest.TestCase):
    def test_list_candidates_from_log_with_working_folders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_path = root / "piab-process-log.json"
            project = root / "sess1"
            for name in ("Raw", "Input", "Output", "Temp"):
                (project / name).mkdir(parents=True)

            state = {
                "name": "sess1",
                "created_at": "2026-08-12T12:00:00+00:00",
                "delivery": {"email": "a@b.com"},
                "steps": {},
            }
            upsert_process_log_entry(project, state, log_path=log_path)

            candidates = list_clean_candidates(log_path=log_path)
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].project_folder, project.resolve())
            self.assertIn("Raw", candidates[0].removable_subfolders)
            self.assertIn("Temp", candidates[0].removable_subfolders)

    def test_list_candidates_includes_special_folder_outside_work_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_path = root / "PodcastInABox" / "piab-process-log.json"
            log_path.parent.mkdir()
            special = root / "My Audio" / "Special"
            for name in ("Raw", "Input", "Output", "Temp"):
                (special / name).mkdir(parents=True)
            upsert_process_log_entry(
                special,
                {"name": "Special", "created_at": "2026-08-15T12:00:00+00:00"},
                log_path=log_path,
            )
            candidates = list_clean_candidates(log_path=log_path)
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].project_folder, special.resolve())
            self.assertEqual(candidates[0].name, "Special")

    def test_clean_keeps_output_and_updates_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_path = root / "piab-process-log.json"
            project = root / "sess2"
            for name in ("Raw", "Input", "Output", "Temp", "Preview Files"):
                (project / name).mkdir(parents=True)
            (project / "Output" / "Full Interview.mp4").write_bytes(b"x")
            (project / "podcast-in-a-box.json").write_text(
                json.dumps(
                    {
                        "kind": "podcast_in_a_box",
                        "name": "sess2",
                        "resume_at": "13_full_render",
                        "created_at": "2026-08-12T12:00:00+00:00",
                        "steps": {},
                    }
                ),
                encoding="utf-8",
            )

            upsert_process_log_entry(
                project,
                {
                    "name": "sess2",
                    "created_at": "2026-08-12T12:00:00+00:00",
                    "steps": {},
                    "full_interview_mp4": str(project / "Output" / "Full Interview.mp4"),
                },
                log_path=log_path,
            )

            def _fake_recycle(path: Path) -> None:
                shutil.rmtree(path)

            result = clean_project_working_files(
                project,
                log_path=log_path,
                recycle=_fake_recycle,
            )
            self.assertEqual(
                set(result.deleted),
                {"Raw", "Input", "Temp", "Preview Files"},
            )
            self.assertTrue((project / "Output" / "Full Interview.mp4").is_file())
            self.assertFalse((project / "Raw").exists())
            self.assertTrue(result.log_updated)

            log = json.loads(log_path.read_text(encoding="utf-8"))
            entry = log["processes"][0]
            self.assertEqual(entry["subfolders"], ["Output"])
            self.assertIn("working_files_cleaned_at", entry)
            self.assertEqual(
                set(entry["cleaned_subfolders"]),
                {"Raw", "Input", "Temp", "Preview Files"},
            )

            # After cleaning, project should no longer be a candidate.
            self.assertEqual(list_clean_candidates(log_path=log_path), [])
            state = json.loads(
                (project / "podcast-in-a-box.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["resume_at"], "cleaned")
            self.assertIn("working_files_cleaned_at", state)

    def test_scan_lost_finds_unlogged_session_and_skips_infra(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_path = root / "piab-process-log.json"
            logged = root / "Logged"
            for name in ("Raw", "Output"):
                (logged / name).mkdir(parents=True)
            upsert_process_log_entry(
                logged,
                {"name": "Logged", "created_at": "2026-08-12T12:00:00+00:00"},
                log_path=log_path,
            )

            lost = root / "Lost"
            for name in ("Raw", "Input", "Temp"):
                (lost / name).mkdir(parents=True)
            (lost / "podcast-in-a-box.json").write_text(
                json.dumps(
                    {
                        "kind": "podcast_in_a_box",
                        "name": "Lost",
                        "created_at": "2026-08-15T12:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )

            decoy = root / "Cursor"
            (decoy / "Raw").mkdir(parents=True)
            empty = root / "Empty"
            empty.mkdir()

            found = scan_lost_clean_candidates(root=root, log_path=log_path)
            self.assertEqual([c.project_folder for c in found], [lost.resolve()])
            self.assertIn("Raw", found[0].removable_subfolders)

            after = list_clean_candidates(log_path=log_path)
            names = {c.project_folder for c in after}
            self.assertIn(lost.resolve(), names)
            self.assertIn(logged.resolve(), names)

    def test_scan_lost_does_nothing_when_already_logged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_path = root / "piab-process-log.json"
            project = root / "sess1"
            (project / "Raw").mkdir(parents=True)
            upsert_process_log_entry(project, {"name": "sess1"}, log_path=log_path)
            found = scan_lost_clean_candidates(root=root, log_path=log_path)
            self.assertEqual(found, [])

    def test_removable_only_known_subfolders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "Raw").mkdir()
            (project / "Extra").mkdir()
            self.assertEqual(removable_working_subfolders(project), ["Raw"])


if __name__ == "__main__":
    unittest.main()
