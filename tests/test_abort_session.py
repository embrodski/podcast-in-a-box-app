"""Tests for abort session logging and process-tree kill."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.controller.abort_session import (
    find_interrupted_step,
    find_last_completed_step,
    record_session_abort,
)
from app.controller.jobs import JobRunner, kill_process_tree


class AbortSessionTests(unittest.TestCase):
    def test_find_last_completed_and_interrupted(self) -> None:
        state = {
            "resume_at": "08_video_sync",
            "steps": {
                "06_conversation_sync": {
                    "id": "06_conversation_sync",
                    "title": "Conversation-sync",
                    "status": "completed",
                    "completed_at": "2026-08-13T01:00:00+00:00",
                },
                "07_deroom_placeholder": {
                    "id": "07_deroom_placeholder",
                    "title": "Clean audio",
                    "status": "completed",
                    "completed_at": "2026-08-13T01:01:00+00:00",
                },
                "08_video_sync": {
                    "id": "08_video_sync",
                    "title": "Video-sync",
                    "status": "in_progress",
                },
            },
        }
        last = find_last_completed_step(state)
        assert last is not None
        self.assertEqual(last["id"], "07_deroom_placeholder")
        self.assertEqual(find_interrupted_step(state), "08_video_sync")

    def test_record_session_abort_marks_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            state = {
                "kind": "podcast_in_a_box",
                "name": "demo",
                "resume_at": "09_transcribe",
                "steps": {
                    "08_video_sync": {
                        "id": "08_video_sync",
                        "title": "Video-sync",
                        "status": "completed",
                    },
                    "09_transcribe": {
                        "id": "09_transcribe",
                        "title": "Transcribe",
                        "status": "in_progress",
                    },
                },
                "paths": {"episode_folder": str(folder)},
            }
            (folder / "podcast-in-a-box.json").write_text("{}", encoding="utf-8")

            with mock.patch(
                "app.controller.abort_session.load_session_state",
                return_value=state,
            ), mock.patch(
                "app.controller.abort_session.save_session_state",
            ) as save:
                payload = record_session_abort(folder, message="Aborted by user.")

            self.assertEqual(payload["interrupted_step"], "09_transcribe")
            self.assertEqual(payload["last_completed_step"]["id"], "08_video_sync")
            self.assertEqual(state["steps"]["09_transcribe"]["status"], "aborted")
            self.assertEqual(state["resume_at"], "09_transcribe")
            self.assertIn("last_abort", state)
            save.assert_called_once()

    def test_abort_after_preview_routes_to_full_prep(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            state = {
                "kind": "podcast_in_a_box",
                "name": "demo",
                "resume_at": "06_conversation_sync",
                "fast_preview_approval": {"approved_at": "2026-08-13T23:58:42+00:00"},
                "steps": {
                    "11_one_min_approval": {
                        "id": "11_one_min_approval",
                        "title": "Fast Preview approval",
                        "status": "completed",
                    },
                    "06_conversation_sync": {
                        "id": "06_conversation_sync",
                        "title": "Conversation-sync",
                        "status": "in_progress",
                    },
                },
            }
            (folder / "podcast-in-a-box.json").write_text("{}", encoding="utf-8")
            with mock.patch(
                "app.controller.abort_session.load_session_state",
                return_value=state,
            ), mock.patch(
                "app.controller.abort_session.save_session_state",
            ):
                payload = record_session_abort(folder)
            self.assertEqual(payload["interrupted_step"], "06_conversation_sync")
            self.assertEqual(state["resume_at"], "13_full_prep_after_preview")


class KillProcessTreeTests(unittest.TestCase):
    def test_kill_process_tree_invokes_taskkill_on_windows(self) -> None:
        with mock.patch("app.controller.jobs.sys.platform", "win32"), mock.patch(
            "app.controller.jobs.subprocess.run"
        ) as run:
            kill_process_tree(12345)
            run.assert_called_once()
            args = run.call_args.args[0]
            self.assertEqual(args[:5], ["taskkill", "/F", "/T", "/PID", "12345"])

    def test_abort_uses_process_tree_kill(self) -> None:
        import sys
        import time

        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
            handle.write(
                "import subprocess, sys, time\n"
                "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                "time.sleep(30)\n"
            )
            script = Path(handle.name)

        runner = JobRunner()
        try:
            with mock.patch(
                "app.controller.jobs.kill_process_tree",
                wraps=kill_process_tree,
            ) as wrapped:
                job = runner.start_script(
                    "prep",
                    script,
                    [],
                    cwd=Path.cwd(),
                )
                time.sleep(0.4)
                result = runner.abort_job(job.id, confirmed=True)
                self.assertEqual(result.status, "aborted")
                self.assertTrue(wrapped.called)
        finally:
            script.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
