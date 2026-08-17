"""Unit tests for PIAB app controller."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.controller import PiabController
from app.controller.jobs import JobRunner
from app.controller.lock import AppLock
from app.controller.overwrite import check_overwrite_risk
from app.controller.preflight import run_preflight
from app.controller.resume_router import resume_screen_for
from app.controller.prep_progress import prep_needs_resume, read_prep_progress
from app.controller.failure_info import read_failure_info, retry_screen_for_failure
from app.controller.flag_report import load_flag_report_text
from app.controller.render_progress import read_render_progress
from app.controller.paths import migrate_legacy_work_files
from app.controller.session_store import (
    existing_state_conflict,
    generate_session_name,
    is_resumable_piab_session,
    list_recent_sessions,
    remember_session_folder,
)


class ResumeRouterTests(unittest.TestCase):
    def test_maps_prep_steps_to_processing_screen(self) -> None:
        self.assertEqual(resume_screen_for("09_transcribe"), "E1")
        self.assertEqual(resume_screen_for("10a_sync_offset_approval"), "F2a")
        self.assertEqual(resume_screen_for("11_one_min_approval"), "F2")
        self.assertEqual(resume_screen_for("13_queued_full"), "F4")
        self.assertEqual(resume_screen_for("13_full_prep_after_preview"), "F4")
        self.assertEqual(resume_screen_for("14_done"), "F5")
        self.assertEqual(resume_screen_for("cleaned"), "A1")

    def test_unknown_defaults_to_home(self) -> None:
        self.assertEqual(resume_screen_for("unknown_step"), "A1")

    def test_full_prep_after_preview_approval_goes_to_final(self) -> None:
        state = {
            "resume_at": "06_conversation_sync",
            "fast_preview_approval": {"approved_at": "2026-08-13T23:58:42+00:00"},
            "steps": {},
        }
        self.assertEqual(resume_screen_for("06_conversation_sync", state=state), "F4")
        self.assertEqual(resume_screen_for("06_conversation_sync"), "E1")


class OverwriteTests(unittest.TestCase):
    def test_lists_existing_prep_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            out = folder / "Output" / "1 Min Test.mp4"
            out.parent.mkdir(parents=True)
            out.write_bytes(b"x")
            at_risk = check_overwrite_risk("run_prep", folder)
            self.assertIn(out.resolve(), [path.resolve() for path in at_risk])


class PrepProgressTests(unittest.TestCase):
    def test_prep_complete_when_at_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            state = {"resume_at": "11_one_min_approval", "steps": {}}
            progress = read_prep_progress(state, folder)
            self.assertTrue(progress.prep_complete)

    def test_current_step_from_resume_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            state = {"resume_at": "09_transcribe", "steps": {}}
            progress = read_prep_progress(state, folder)
            self.assertEqual(progress.current_step, "09_transcribe")
            self.assertIn("Transcribing", progress.current_label)

    def test_prep_needs_resume_after_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            state = {
                "resume_at": "05_estimate_prep",
                "steps": {"06_conversation_sync": {"status": "completed"}},
            }
            self.assertTrue(prep_needs_resume(state, folder))

    def test_step_timing_from_started_at_and_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            started = datetime(2026, 7, 29, 19, 31, 0, tzinfo=timezone.utc)
            now = started.replace(minute=41)
            state = {
                "resume_at": "08_video_sync",
                "estimate_prep": {
                    "breakdown": {
                        "video_sync_sec": 3600,
                    }
                },
                "steps": {
                    "08_video_sync": {
                        "status": "in_progress",
                        "started_at": started.isoformat(),
                    }
                },
            }
            progress = read_prep_progress(state, folder, now=now)
            self.assertEqual(progress.step_started_display, "12:31 PM")
            self.assertEqual(progress.step_eta_display, "Est. about 50 min remaining for this step")

    def test_fast_preview_tracks_p_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            state = {
                "resume_at": "06p_conversation_sync",
                "steps": {
                    "06p_conversation_sync": {"status": "completed"},
                    "07p_deroom_placeholder": {"status": "completed"},
                    "08p_video_sync": {"status": "completed"},
                    "09p_transcribe": {"status": "completed"},
                },
            }
            progress = read_prep_progress(state, folder)
            self.assertEqual(progress.current_step, "10p_fast_preview_one_min")
            self.assertIn("1-minute", progress.current_label)
            self.assertTrue(progress.step_lines[0].startswith("✓"))
            self.assertTrue(progress.step_lines[-1].startswith("→"))


class WorkRootTests(unittest.TestCase):
    def test_migrate_legacy_work_files_moves_log_and_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "PodcastRoom"
            work = Path(tmp) / "PodcastRoom" / "PodcastInABox"
            legacy.mkdir()
            (legacy / "piab-process-log.json").write_text("{\"kind\": \"piab_process_log\"}", encoding="utf-8")
            (legacy / "piab-job-queue.json").write_text("{\"full\": []}", encoding="utf-8")
            migrate_legacy_work_files(
                legacy_root=legacy,
                work_root=work,
                extra_legacy_roots=(),
            )
            self.assertTrue((work / "piab-process-log.json").is_file())
            self.assertTrue((work / "piab-job-queue.json").is_file())
            self.assertFalse((legacy / "piab-process-log.json").exists())
            self.assertFalse((legacy / "piab-job-queue.json").exists())

    def test_migrate_legacy_work_files_keeps_existing_dest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "old"
            work = Path(tmp) / "new"
            legacy.mkdir()
            work.mkdir()
            (legacy / "piab-process-log.json").write_text("old", encoding="utf-8")
            (work / "piab-process-log.json").write_text("new", encoding="utf-8")
            migrate_legacy_work_files(legacy_root=legacy, work_root=work)
            self.assertEqual(
                (work / "piab-process-log.json").read_text(encoding="utf-8"),
                "new",
            )
            self.assertTrue((legacy / "piab-process-log.json").is_file())

    def test_migrate_legacy_work_files_from_app_home_into_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scan = Path(tmp) / "PodcastRoom"
            home = scan / "PodcastInABox"
            sessions = home / "Sessions"
            scan.mkdir()
            home.mkdir()
            (home / "piab-process-log.json").write_text("from-home", encoding="utf-8")
            (home / ".piab-app.lock").write_text("lock", encoding="utf-8")
            migrate_legacy_work_files(
                legacy_root=scan,
                work_root=sessions,
                extra_legacy_roots=(home,),
            )
            self.assertEqual(
                (sessions / "piab-process-log.json").read_text(encoding="utf-8"),
                "from-home",
            )
            self.assertTrue((sessions / ".piab-app.lock").is_file())
            self.assertFalse((home / "piab-process-log.json").exists())
            self.assertFalse((home / ".piab-app.lock").exists())


class SessionNameTests(unittest.TestCase):
    def test_auto_name_collision_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            when = datetime(2026, 7, 28, 15, 45)
            first = generate_session_name(root, when=when)
            (root / first).mkdir()
            second = generate_session_name(root, when=when)
            self.assertEqual(first, "2026-07-28_1545")
            self.assertEqual(second, "2026-07-28_1545_2")


class PreflightTests(unittest.TestCase):
    def test_mocked_preflight_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            preset = Path(tmp) / "preset.vmix"
            preset.write_text("preset", encoding="utf-8")
            report = run_preflight(
                scan_root=Path(tmp),
                deps={
                    "disk_usage": lambda _path: (100, 0, 50 * 1024**3),
                    "which": lambda name: f"C:/bin/{name}.exe",
                    "is_vmix_running": lambda: True,
                    "preset_path": preset,
                    "read_elevenlabs_key": lambda: "key",
                    "env": {
                        "HARNESS_SMTP_USER": "a",
                        "HARNESS_SMTP_PASSWORD": "b",
                        "FRAMEIO_ACCOUNT_ID": "1",
                        "FRAMEIO_PROJECT_ID": "2",
                        "FRAMEIO_UPLOAD_FOLDER_ID": "3",
                    },
                    "vmix_api_ping": lambda: True,
                },
            )
        self.assertTrue(report.ok_for_recording)
        self.assertTrue(report.ok_for_autocut)

    def test_vmix_not_running_is_ok_when_executable_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            preset = Path(tmp) / "preset.vmix"
            preset.write_text("preset", encoding="utf-8")
            report = run_preflight(
                scan_root=Path(tmp),
                deps={
                    "disk_usage": lambda _path: (100, 0, 50 * 1024**3),
                    "which": lambda name: f"C:/bin/{name}.exe",
                    "is_vmix_running": lambda: False,
                    "preset_path": preset,
                    "read_elevenlabs_key": lambda: "key",
                    "env": {
                        "HARNESS_SMTP_USER": "a",
                        "HARNESS_SMTP_PASSWORD": "b",
                        "FRAMEIO_ACCOUNT_ID": "1",
                        "FRAMEIO_PROJECT_ID": "2",
                        "FRAMEIO_UPLOAD_FOLDER_ID": "3",
                    },
                },
            )
        vmix = next(c for c in report.checks if c.id == "vmix")
        api = next(c for c in report.checks if c.id == "vmix_api")
        self.assertEqual(vmix.status, "ok")
        self.assertEqual(api.status, "ok")
        self.assertTrue(report.ok_for_recording)


class AppLockTests(unittest.TestCase):
    def test_acquire_and_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = AppLock(Path(tmp) / "lock.json")
            ok, message = lock.acquire()
            self.assertTrue(ok, message)
            self.assertTrue(lock.path.is_file())
            lock.release()
            self.assertFalse(lock.path.is_file())

    def test_recording_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = AppLock(Path(tmp) / "lock.json")
            lock.acquire()
            lock.set_recording_active(True)
            self.assertTrue(lock.is_recording_active())


class JobRunnerTests(unittest.TestCase):
    def test_abort_subprocess(self) -> None:
        import sys
        import time

        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
            handle.write("import time\nfor _ in range(300):\n    time.sleep(0.1)\n")
            script = Path(handle.name)

        runner = JobRunner()
        try:
            job = runner.start_script(
                "prep",
                script,
                [],
                cwd=Path.cwd(),
            )
            time.sleep(0.3)
            result = runner.abort_job(job.id, confirmed=True)
            self.assertEqual(result.status, "aborted")
        finally:
            script.unlink(missing_ok=True)

    def test_rejects_second_processing_job(self) -> None:
        runner = JobRunner(max_processing_jobs=1)
        job_a = runner.register_job("prep")
        self.assertEqual(job_a.kind, "prep")
        with self.assertRaises(RuntimeError):
            runner.start_script(
                "render",
                Path(__file__),
                [],
                cwd=Path.cwd(),
            )

    def test_allows_fast_preview_alongside_full(self) -> None:
        runner = JobRunner()
        runner.register_job("full_after_preview")
        job = runner.register_job("fast_preview")
        self.assertEqual(job.kind, "fast_preview")
        self.assertEqual(runner.running_full_count(), 1)
        self.assertEqual(runner.running_fast_preview_count(), 1)

    def test_poll_reports_quick_exit_without_deadlock(self) -> None:
        import time

        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
            handle.write("raise SystemExit(3)\n")
            script = Path(handle.name)

        runner = JobRunner()
        try:
            job = runner.start_script("full_after_preview", script, [], cwd=Path.cwd())
            deadline = time.time() + 5.0
            finished = []
            while time.time() < deadline and not finished:
                finished = runner.poll()
                if not finished:
                    time.sleep(0.05)
            self.assertTrue(finished)
            self.assertEqual(finished[0].id, job.id)
            self.assertEqual(finished[0].status, "failed")
            self.assertEqual(runner.poll(), [])
        finally:
            script.unlink(missing_ok=True)


class ControllerTests(unittest.TestCase):
    def test_hold_and_resume_held_job(self) -> None:
        from app.controller.job_queue import JobQueueStore
        from app.controller.types import Job

        with tempfile.TemporaryDirectory() as tmp:
            lock = AppLock(Path(tmp) / "lock.json")
            lock.acquire()
            queue = JobQueueStore(Path(tmp) / "queue.json")
            folder = Path(tmp) / "sess"
            folder.mkdir()
            queue.enqueue(folder, "full", name="S")
            controller = PiabController(lock=lock, jobs=JobRunner(), job_queue=queue)
            self.assertTrue(controller.hold_queued_job(folder, "full"))
            self.assertEqual(controller.list_held_jobs()[0]["status"], "held")
            self.assertFalse(controller.should_confirm_close_program())
            fake = Job(id="abc", kind="full_after_preview", session_folder=folder)
            with patch.object(
                controller, "_start_full_lane_job", return_value=fake
            ) as started:
                job = controller.resume_held_job(folder, "full")
            started.assert_called_once()
            self.assertIs(job, fake)
            entry = queue.entry_for(folder, "full")
            self.assertIsNotNone(entry)
            assert entry is not None
            self.assertEqual(entry.status, "queued")
            self.assertEqual(queue.held(), [])

    def test_close_program_confirm_only_when_running_or_queued(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = AppLock(Path(tmp) / "lock.json")
            lock.acquire()
            from app.controller.job_queue import JobQueueStore

            queue = JobQueueStore(Path(tmp) / "queue.json")
            controller = PiabController(lock=lock, jobs=JobRunner(), job_queue=queue)
            self.assertFalse(controller.should_confirm_close_program())
            folder = Path(tmp) / "sess"
            folder.mkdir()
            queue.enqueue(folder, "full", name="S")
            self.assertTrue(controller.should_confirm_close_program())
            queue.mark_failed(folder, "full")
            self.assertFalse(controller.should_confirm_close_program())

    def test_busy_when_recording_lock_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = AppLock(Path(tmp) / "lock.json")
            lock.acquire()
            lock.set_recording_active(True)
            controller = PiabController(lock=lock, jobs=JobRunner())
            self.assertIn("recording", controller.busy_reasons())

    def test_resume_screen_uses_preview_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            state = {
                "kind": "podcast_in_a_box",
                "name": "demo",
                "resume_at": "06_conversation_sync",
                "fast_preview_approval": {"approved_at": "2026-08-13T23:58:42+00:00"},
                "last_abort": {"message": "Aborted by user."},
                "steps": {
                    "06_conversation_sync": {
                        "id": "06_conversation_sync",
                        "status": "in_progress",
                    }
                },
            }
            (folder / "podcast-in-a-box.json").write_text(
                json.dumps(state), encoding="utf-8"
            )
            controller = PiabController()
            self.assertEqual(controller.resume_screen_for(folder), "F4")
            self.assertTrue(controller.resume_full_needs_overwrite(folder))

    def test_resume_screen_shows_failure_when_marker_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            state = {
                "kind": "podcast_in_a_box",
                "name": "demo",
                "resume_at": "12_estimate_full",
                "fast_preview_approval": {"approved_at": "2026-08-13T23:58:42+00:00"},
                "steps": {
                    "13_full_render": {
                        "id": "13_full_render",
                        "status": "failed",
                    }
                },
            }
            (folder / "podcast-in-a-box.json").write_text(
                json.dumps(state), encoding="utf-8"
            )
            temp = folder / "Temp"
            temp.mkdir()
            (temp / "harness-FAILURE.json").write_text(
                json.dumps(
                    {
                        "pipeline": "piab_full_render",
                        "step_id": "13_full_render",
                        "error_summary": "Unknown segment: main",
                    }
                ),
                encoding="utf-8",
            )
            controller = PiabController()
            self.assertEqual(controller.resume_screen_for(folder), "F1")
            info = read_failure_info(folder, state)
            self.assertEqual(info.retry_screen, "F4")

    def test_start_next_queued_recovers_stale_running(self) -> None:
        from app.controller.job_queue import JobQueueStore
        from app.controller.types import Job

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "sess"
            folder.mkdir()
            (folder / "podcast-in-a-box.json").write_text(
                json.dumps(
                    {
                        "name": "sess",
                        "resume_at": "06_conversation_sync",
                        "fast_preview_approval": {"approved_at": "x"},
                    }
                ),
                encoding="utf-8",
            )
            store = JobQueueStore(Path(tmp) / "queue.json")
            store.enqueue(folder, "full", name="sess")
            store.mark_running(folder, "full")
            controller = PiabController(job_queue=store, jobs=JobRunner())
            fake = Job(id="abc", kind="full_after_preview", session_folder=folder)
            with patch.object(
                controller, "_start_full_lane_job", return_value=fake
            ) as started:
                job = controller.start_next_queued("full", allow_overwrite=True)
            started.assert_called_once()
            self.assertIs(job, fake)

    def test_check_overwrite_blocks_prep(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            out = folder / "Output" / "1 Min Test.mp4"
            out.parent.mkdir(parents=True)
            out.write_bytes(b"x")
            controller = PiabController()
            with self.assertRaises(RuntimeError):
                controller.start_prep(folder)


class ReviewHelpersTests(unittest.TestCase):
    def test_resolve_one_min_test_path_from_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            out = folder / "Output" / "1 Min Test.mp4"
            out.parent.mkdir(parents=True)
            out.write_bytes(b"mp4")
            controller = PiabController()
            state = {"podcast_autocut_test_mp4": str(out)}
            self.assertEqual(
                controller.resolve_one_min_test_path(state, folder),
                out.resolve(),
            )

    def test_approve_one_min_test_updates_resume_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            state = {
                "kind": "podcast_in_a_box",
                "source_duration_sec": 120.0,
                "resume_at": "11_one_min_approval",
                "steps": {},
                "paths": {
                    "episode_folder": str(folder),
                    "raw": str(folder / "Raw"),
                    "input": str(folder / "Input"),
                    "output": str(folder / "Output"),
                    "temp": str(folder / "Temp"),
                },
            }
            (folder / "podcast-in-a-box.json").write_text(
                json.dumps(state),
                encoding="utf-8",
            )

            controller = PiabController()
            with patch("app.controller.review._run_script") as run_script:
                updated = controller.approve_one_min_test(folder)

            self.assertEqual(
                updated["steps"]["11_one_min_approval"]["status"],
                "completed",
            )
            self.assertEqual(run_script.call_count, 1)
            estimate_argv = run_script.call_args[0][1]
            self.assertIn("--which", estimate_argv)
            self.assertIn("full", estimate_argv)
            self.assertIn("--mark-awaiting", estimate_argv)


class RenderProgressTests(unittest.TestCase):
    def test_render_complete_at_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            state = {"resume_at": "14_done", "steps": {}}
            progress = read_render_progress(state, folder)
            self.assertTrue(progress.render_complete)

    def test_render_in_progress_shows_timing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            started = datetime(2026, 7, 29, 22, 0, 0, tzinfo=timezone.utc)
            now = started.replace(minute=5)
            state = {
                "resume_at": "13_full_render",
                "estimate_full": {"center_sec": 600},
                "steps": {
                    "13_full_render": {
                        "status": "in_progress",
                        "started_at": started.isoformat(),
                    }
                },
            }
            progress = read_render_progress(state, folder, now=now)
            self.assertFalse(progress.render_complete)
            self.assertEqual(progress.current_step, "13_full_render")
            self.assertTrue(progress.step_lines[0].startswith("→"))
            self.assertIn("Rendering full interview", progress.step_lines[0])
            self.assertEqual(progress.step_started_display, "3:00 PM")
            self.assertIn("remaining", progress.step_eta_display or "")

    def test_render_advances_past_completed_encode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            state = {
                "resume_at": "13_full_render",
                "steps": {
                    "13_full_render": {"status": "completed"},
                    "13_output_transcripts": {"status": "in_progress"},
                },
            }
            progress = read_render_progress(state, folder)
            self.assertEqual(progress.current_step, "13_output_transcripts")
            self.assertTrue(progress.step_lines[0].startswith("✓"))
            self.assertTrue(any(line.startswith("→") and "transcript" in line.lower() for line in progress.step_lines))

    def test_chained_full_after_preview_uses_render_phases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            state = {
                "resume_at": "13_full_render",
                "fast_preview_approval": {"approved_at": "2026-08-11T12:00:00+00:00"},
                "steps": {
                    "06_conversation_sync": {"status": "completed"},
                    "07_deroom_placeholder": {"status": "completed"},
                    "08_video_sync": {"status": "completed"},
                    "09_transcribe": {"status": "completed"},
                    "10_one_min_test": {"status": "skipped"},
                    "13_full_render": {"status": "in_progress"},
                },
            }
            progress = read_prep_progress(state, folder)
            self.assertEqual(progress.current_step, "13_full_render")
            self.assertIn("Rendering full interview", progress.current_label)
            self.assertTrue(any("Fast Preview" in line for line in progress.step_lines))
            self.assertFalse(any("→ Rendering 1-minute preview" == line for line in progress.step_lines))
            self.assertTrue(any(line.startswith("→") and "full interview" in line for line in progress.step_lines))


class FailureInfoTests(unittest.TestCase):
    def test_retry_screen_for_prep_failure(self) -> None:
        failure = {
            "pipeline": "piab_prep",
            "step_id": "09_transcribe",
            "error_summary": "ElevenLabs failed",
        }
        self.assertEqual(retry_screen_for_failure(failure, "09_transcribe"), "E1")

    def test_retry_screen_for_render_failure(self) -> None:
        failure = {
            "pipeline": "piab_full_render",
            "step_id": "13_full_render",
            "error_summary": "Render failed",
        }
        self.assertEqual(retry_screen_for_failure(failure, "12_estimate_full"), "F4")

    def test_read_failure_info_from_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            temp = folder / "Temp"
            temp.mkdir()
            payload = {
                "pipeline": "piab_prep",
                "step_id": "09_transcribe",
                "step_title": "Transcribing (ElevenLabs)",
                "error_summary": "Billing issue",
                "error_detail": "HTTP 401 payment_required",
            }
            (temp / "harness-FAILURE.json").write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            info = read_failure_info(folder, {"resume_at": "09_transcribe"})
            self.assertEqual(info.summary, "Billing issue")
            self.assertEqual(info.step_title, "Transcribing (ElevenLabs)")
            self.assertEqual(info.retry_screen, "E1")

    def test_read_failure_info_rewrites_disk_full_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            temp = folder / "Temp"
            temp.mkdir()
            detail = (
                r"RuntimeError: ffmpeg ... E:\PodcastRoom\session\Input\out.mp4\n"
                "No space left on device"
            )
            payload = {
                "pipeline": "piab_prep",
                "step_id": "08_video_sync",
                "step_title": "Video-sync (main)",
                "error_summary": "Command failed (1): multicam_align_trim.py ...",
                "error_detail": detail,
                "working_folder": str(folder),
            }
            (temp / "harness-FAILURE.json").write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            state = {"source_duration_sec": 3000.0, "resume_at": "08_video_sync"}
            info = read_failure_info(folder, state)
            self.assertIn("disk space", info.summary.lower())
            self.assertIn("drive", info.summary)
            self.assertIn("60 GB", info.summary)

    def test_clear_prep_failure_removes_artifact(self) -> None:
        from app.controller.prep_progress import clear_prep_failure, read_prep_failure

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            temp = folder / "Temp"
            temp.mkdir()
            (temp / "harness-FAILURE.json").write_text("{}", encoding="utf-8")
            (temp / "harness-FAILURE.txt").write_text("failed", encoding="utf-8")
            self.assertIsNotNone(read_prep_failure(folder))
            clear_prep_failure(folder)
            self.assertIsNone(read_prep_failure(folder))
            self.assertFalse((temp / "harness-FAILURE.txt").is_file())


class SessionStoreTests(unittest.TestCase):
    def test_is_resumable_piab_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "Raw").mkdir()
            state_path = folder / "podcast-in-a-box.json"
            state_path.write_text(
                json.dumps({"kind": "podcast_in_a_box", "resume_at": "03_label_videos"}),
                encoding="utf-8",
            )
            self.assertTrue(is_resumable_piab_session(folder))

    def test_not_resumable_without_raw(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "Preview Files").mkdir()
            (folder / "Output").mkdir()
            (folder / "podcast-in-a-box.json").write_text(
                json.dumps({"kind": "podcast_in_a_box", "resume_at": "11_one_min_approval"}),
                encoding="utf-8",
            )
            self.assertFalse(is_resumable_piab_session(folder))
            conflict = existing_state_conflict(folder)
            self.assertIsNotNone(conflict)
            self.assertIn("Clean Old Working Files", conflict)

    def test_not_resumable_after_clean_stamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "Raw").mkdir()
            (folder / "podcast-in-a-box.json").write_text(
                json.dumps(
                    {
                        "kind": "podcast_in_a_box",
                        "resume_at": "cleaned",
                        "working_files_cleaned_at": "2026-08-15T22:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            self.assertFalse(is_resumable_piab_session(folder))

    def test_list_recent_sessions_hides_finished(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            done = root / "Test"
            done.mkdir()
            (done / "Raw").mkdir()
            (done / "podcast-in-a-box.json").write_text(
                json.dumps({"kind": "podcast_in_a_box", "resume_at": "14_done"}),
                encoding="utf-8",
            )
            open_session = root / "Open"
            open_session.mkdir()
            (open_session / "Raw").mkdir()
            (open_session / "podcast-in-a-box.json").write_text(
                json.dumps({"kind": "podcast_in_a_box", "resume_at": "11_one_min_approval"}),
                encoding="utf-8",
            )
            self.assertTrue(is_resumable_piab_session(done))
            self.assertFalse(is_resumable_piab_session(done, include_finished=False))
            self.assertEqual(
                list_recent_sessions(root, include_process_log=False),
                [open_session.resolve()],
            )

    def test_list_recent_sessions_requires_raw(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            keep = root / "Keep"
            keep.mkdir()
            (keep / "Raw").mkdir()
            (keep / "podcast-in-a-box.json").write_text(
                json.dumps({"kind": "podcast_in_a_box", "resume_at": "11_one_min_approval"}),
                encoding="utf-8",
            )
            drop = root / "Drop"
            drop.mkdir()
            (drop / "Preview Files").mkdir()
            (drop / "podcast-in-a-box.json").write_text(
                json.dumps({"kind": "podcast_in_a_box", "resume_at": "11_one_min_approval"}),
                encoding="utf-8",
            )
            self.assertEqual(
                list_recent_sessions(root, include_process_log=False),
                [keep.resolve()],
            )

    def test_list_recent_sessions_includes_logged_special_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "PodcastInABox"
            other = Path(tmp) / "My Audio" / "Special"
            work.mkdir()
            other.mkdir(parents=True)
            (other / "Raw").mkdir()
            (other / "Input").mkdir()
            (other / "podcast-in-a-box.json").write_text(
                json.dumps(
                    {
                        "kind": "podcast_in_a_box",
                        "name": "Special",
                        "resume_at": "03_label_videos",
                    }
                ),
                encoding="utf-8",
            )
            log_path = work / "piab-process-log.json"
            remember_session_folder(other, log_path=log_path)
            found = list_recent_sessions(work, log_path=log_path)
            self.assertEqual(found, [other.resolve()])

    def test_harness_state_is_not_resumable_piab(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            state_path = folder / "podcast-in-a-box.json"
            state_path.write_text(
                json.dumps({"harness": "inkhaven-episode-harness"}),
                encoding="utf-8",
            )
            self.assertFalse(is_resumable_piab_session(folder))
            conflict = existing_state_conflict(folder)
            self.assertIsNotNone(conflict)
            self.assertIn("harness", conflict.lower())

    def test_cursor_agent_state_is_conflict_for_gui(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "cursor-podcast-in-a-box.json").write_text(
                json.dumps({"kind": "podcast_in_a_box", "resume_at": "03_label_videos"}),
                encoding="utf-8",
            )
            self.assertFalse(is_resumable_piab_session(folder))
            conflict = existing_state_conflict(folder)
            self.assertIsNotNone(conflict)
            self.assertIn("Cursor", conflict)


class FlagReportTests(unittest.TestCase):
    def test_prefers_flag_report_from_state(self) -> None:
        state = {
            "flag_timestamps": {
                "flag_report": (
                    "Flags Dropped At These Timestamps:\n00:01:02\n\n"
                    "Pause Flags At These Timestamps:\n00:03:04"
                )
            }
        }
        text = load_flag_report_text(state)
        self.assertIn("00:01:02", text)
        self.assertIn("00:03:04", text)

    def test_builds_report_from_hhmmss_lists(self) -> None:
        state = {
            "flag_timestamps": {
                "flag_timestamps_hhmmss": ["00:10:00"],
                "pause_flag_timestamps_hhmmss": [],
            }
        }
        text = load_flag_report_text(state)
        self.assertIn("Flags Dropped At These Timestamps:", text)
        self.assertIn("00:10:00", text)
        self.assertIn("Pause Flags At These Timestamps:", text)
        self.assertIn("-none-", text)

    def test_reads_temp_file_when_state_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            temp = folder / "Temp"
            temp.mkdir()
            report = temp / "interview-flag-timestamps.txt"
            report.write_text(
                "Flags Dropped At These Timestamps:\n00:05:00\n\n"
                "Pause Flags At These Timestamps:\n-none-",
                encoding="utf-8",
            )
            state = {"paths": {"temp": str(temp)}}
            text = load_flag_report_text(state, working_folder=folder)
            self.assertIn("00:05:00", text)


if __name__ == "__main__":
    unittest.main()
