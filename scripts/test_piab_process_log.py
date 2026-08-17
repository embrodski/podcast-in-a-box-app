"""Tests for the app-wide PIAB process log."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from piab_process_log import (
    DEFAULT_PROCESS_LOG_PATH,
    build_process_entry,
    find_process_log_entry,
    load_process_log,
    sync_process_log_from_state,
    upsert_process_log_entry,
)


class ProcessLogTests(unittest.TestCase):
    def test_upsert_records_session_and_updates_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "2026-08-12_1315"
            (project / "Raw").mkdir(parents=True)
            (project / "Input").mkdir()
            (project / "Output").mkdir()
            (project / "Temp").mkdir()
            log_path = root / "piab-process-log.json"

            state = {
                "kind": "podcast_in_a_box",
                "name": "2026-08-12_1315",
                "created_at": "2026-08-12T20:00:00+00:00",
                "resume_at": "03_label_videos",
                "session_mode": "default",
                "delivery": {"enabled": True, "email": "guest@example.com"},
                "steps": {
                    "01_scan_confirm": {
                        "id": "01_scan_confirm",
                        "title": "Scan and confirm session files",
                        "status": "completed",
                        "completed_at": "2026-08-12T20:00:01+00:00",
                    },
                    "02_create_folder": {
                        "id": "02_create_folder",
                        "title": "Create working folder",
                        "status": "completed",
                        "completed_at": "2026-08-12T20:00:02+00:00",
                    },
                },
            }

            with patch("piab_process_log._utc_now_iso", return_value="2026-08-12T20:00:03+00:00"):
                written = upsert_process_log_entry(project, state, log_path=log_path)
            self.assertEqual(written, log_path.resolve())

            log = load_process_log(log_path)
            self.assertEqual(log["kind"], "piab_process_log")
            self.assertEqual(len(log["processes"]), 1)
            entry = log["processes"][0]
            self.assertEqual(entry["email"], "guest@example.com")
            self.assertEqual(entry["project_folder"], str(project.resolve()))
            self.assertEqual(entry["subfolders"], ["Input", "Output", "Raw", "Temp"])
            self.assertEqual(entry["begun_at"], "2026-08-12T20:00:00+00:00")
            self.assertEqual(len(entry["steps_completed"]), 2)
            self.assertFalse(entry["final_video"]["completed"])
            self.assertFalse(entry["frameio"]["uploaded"])
            self.assertFalse(entry["email_delivery"]["sent"])

            state["steps"]["13_full_render"] = {
                "id": "13_full_render",
                "title": "Full interview render",
                "status": "completed",
                "completed_at": "2026-08-12T22:00:00+00:00",
            }
            state["full_interview_mp4"] = str(project / "Output" / "Full Interview.mp4")
            (project / "Output" / "Full Interview.mp4").write_bytes(b"x")
            state["resume_at"] = "14_done"
            state["delivery"] = {
                "enabled": True,
                "email": "guest@example.com",
                "frameio": {
                    "status": "completed",
                    "completed_at": "2026-08-12T22:05:00+00:00",
                    "short_url": "https://example.test/share",
                },
                "email_delivery": {
                    "status": "sent",
                    "sent_at": "2026-08-12T22:06:00+00:00",
                },
            }
            (project / "Preview Files").mkdir()

            with patch("piab_process_log._utc_now_iso", return_value="2026-08-12T22:06:01+00:00"):
                upsert_process_log_entry(project, state, log_path=log_path)

            log = load_process_log(log_path)
            self.assertEqual(len(log["processes"]), 1)
            entry = log["processes"][0]
            self.assertIn("Preview Files", entry["subfolders"])
            self.assertTrue(entry["final_video"]["completed"])
            self.assertTrue(entry["frameio"]["uploaded"])
            self.assertEqual(entry["frameio"]["completed_at"], "2026-08-12T22:05:00+00:00")
            self.assertTrue(entry["email_delivery"]["sent"])
            self.assertEqual(entry["email_delivery"]["sent_at"], "2026-08-12T22:06:00+00:00")
            self.assertEqual(len(entry["steps_completed"]), 3)

    def test_build_entry_preserves_begun_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            prior = {"begun_at": "2026-01-01T00:00:00+00:00"}
            entry = build_process_entry(
                project,
                {"created_at": "2026-08-12T20:00:00+00:00", "steps": {}},
                prior=prior,
            )
            self.assertEqual(entry["begun_at"], "2026-01-01T00:00:00+00:00")

    def test_find_process_log_entry_by_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "Demo"
            project.mkdir()
            log_path = root / "piab-process-log.json"
            upsert_process_log_entry(
                project,
                {"name": "Demo", "steps": {}},
                log_path=log_path,
            )
            found = find_process_log_entry(project, log_path=log_path)
            self.assertIsNotNone(found)
            assert found is not None
            self.assertEqual(found["name"], "Demo")
            missing = find_process_log_entry(root / "Other", log_path=log_path)
            self.assertIsNone(missing)

    def test_sync_from_temp_folder_skips_default_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "Demo"
            project.mkdir()
            before = None
            if DEFAULT_PROCESS_LOG_PATH.is_file():
                before = DEFAULT_PROCESS_LOG_PATH.read_text(encoding="utf-8")
            written = sync_process_log_from_state(
                project,
                {"name": "Demo", "resume_at": "11_one_min_approval", "steps": {}},
            )
            self.assertIsNone(written)
            after = None
            if DEFAULT_PROCESS_LOG_PATH.is_file():
                after = DEFAULT_PROCESS_LOG_PATH.read_text(encoding="utf-8")
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
