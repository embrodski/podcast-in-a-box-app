"""Tests for harness failure notification helpers."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness_email import PIAB_ERROR_REPORT_SUBJECT, PIAB_ERROR_REPORT_TO
from harness_notify_failure import (
    FAILURE_JSON_NAME,
    collect_session_log,
    notify_harness_failure,
    send_error_report_email,
    summarize_error,
    write_failure_artifacts,
)
from piab_process_log import upsert_process_log_entry


class SummarizeErrorTests(unittest.TestCase):
    def test_elevenlabs_payment(self) -> None:
        exc = RuntimeError(
            'ElevenLabs API HTTP 401: {"detail":{"type":"payment_required",'
            '"message":"Complete the latest invoice"}}'
        )
        summary = summarize_error(exc)
        self.assertIn("billing/payment", summary.lower())
        self.assertIn("ElevenLabs", summary)

    def test_generic_runtime_error(self) -> None:
        summary = summarize_error(RuntimeError("ffmpeg audio clip failed"))
        self.assertIn("ffmpeg", summary)


class FailureArtifactTests(unittest.TestCase):
    def test_writes_json_and_txt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            json_path, txt_path = write_failure_artifacts(
                temp,
                pipeline="piab_prep",
                step_id="09_transcribe",
                step_title="Transcribe prepped WAV",
                error_summary="Test failure",
                error_detail="detail here",
                working_folder=Path("E:/Demo"),
            )
            self.assertEqual(json_path.name, FAILURE_JSON_NAME)
            self.assertTrue(txt_path.is_file())
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["notify_immediately"])
            self.assertEqual(payload["step_id"], "09_transcribe")
            self.assertIn("Test failure", txt_path.read_text(encoding="utf-8"))


class SessionLogAndEmailTests(unittest.TestCase):
    _SMTP_ENV = {
        "HARNESS_SMTP_HOST": "smtp.test",
        "HARNESS_SMTP_USER": "u",
        "HARNESS_SMTP_PASSWORD": "p",
        "HARNESS_SMTP_FROM": "from@example.com",
    }

    def test_collect_session_log_includes_process_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "Temp").mkdir()
            state = {
                "kind": "podcast_in_a_box",
                "name": "DemoSession",
                "resume_at": "13_full_render",
                "session_mode": "default",
                "created_at": "2026-08-15T20:00:00+00:00",
            }
            (folder / "podcast-in-a-box.json").write_text(
                json.dumps(state), encoding="utf-8"
            )
            log_path = folder / "piab-process-log.json"
            upsert_process_log_entry(folder, state, log_path=log_path)
            failure_txt = folder / "Temp" / "harness-FAILURE.txt"
            failure_txt.write_text("HARNESS JOB FAILED\nUnknown segment: main\n", encoding="utf-8")

            body = collect_session_log(
                working_folder=folder,
                step_title="Full interview render",
                error_summary="Unknown segment: main",
                error_detail="ValueError: Unknown segment: main",
                failure_txt=failure_txt,
                process_log_path=log_path,
            )
            self.assertIn("PIAB autocut error", body)
            self.assertIn("DemoSession", body)
            self.assertIn("Full interview render", body)
            self.assertIn("Unknown segment: main", body)
            self.assertIn("Process log entry", body)
            self.assertIn(str(folder.resolve()), body)

    def test_send_error_report_email_uses_fixed_subject(self) -> None:
        captured: list = []

        def capture(_config, message) -> None:
            captured.append(message)

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with patch.dict(os.environ, self._SMTP_ENV, clear=False):
                with patch("harness_env.load_harness_env"):
                    sent = send_error_report_email(
                        working_folder=folder,
                        step_title="Full interview render",
                        error_summary="Unknown segment: main",
                        error_detail="detail",
                        sender=capture,
                    )
        self.assertTrue(sent)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["Subject"], PIAB_ERROR_REPORT_SUBJECT)
        self.assertEqual(captured[0]["To"], PIAB_ERROR_REPORT_TO)
        self.assertIn("Unknown segment: main", captured[0].get_content())

    def test_send_error_report_email_skips_when_already_sent(self) -> None:
        captured: list = []

        def capture(_config, message) -> None:
            captured.append(message)

        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "harness-FAILURE.json"
            marker.write_text(
                json.dumps({"error_report_emailed_at": "2026-08-15T20:00:00+00:00"}),
                encoding="utf-8",
            )
            sent = send_error_report_email(
                working_folder=Path(tmp),
                step_title="step",
                error_summary="summary",
                failure_json=marker,
                sender=capture,
            )
        self.assertTrue(sent)
        self.assertEqual(captured, [])

    def test_send_error_report_email_returns_false_without_smtp(self) -> None:
        with patch("harness_env.load_harness_env"):
            with patch(
                "harness_email.SmtpConfig.from_env",
                side_effect=ValueError("Missing SMTP configuration"),
            ):
                sent = send_error_report_email(
                    working_folder=None,
                    step_title="step",
                    error_summary="summary",
                )
        self.assertFalse(sent)

    def test_notify_sends_error_email_and_survives_smtp_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            temp = folder / "Temp"
            with patch(
                "harness_notify_failure.send_error_report_email",
                return_value=True,
            ) as mock_send:
                result = notify_harness_failure(
                    temp_dir=temp,
                    pipeline="piab_full_render",
                    step_id="13_full_render",
                    step_title="Full interview render",
                    exc=RuntimeError("Unknown segment: main"),
                    working_folder=folder,
                    notify=False,
                )
            mock_send.assert_called_once()
            self.assertTrue(result["failed"])
            self.assertTrue(result["error_report_emailed"])

            with patch(
                "harness_notify_failure.send_error_report_email",
                return_value=False,
            ):
                result = notify_harness_failure(
                    temp_dir=temp,
                    pipeline="piab_full_render",
                    step_id="13_full_render",
                    step_title="Full interview render",
                    exc=RuntimeError("still failing"),
                    working_folder=folder,
                    notify=False,
                )
            self.assertTrue(result["failed"])
            self.assertFalse(result["error_report_emailed"])


if __name__ == "__main__":
    unittest.main()
